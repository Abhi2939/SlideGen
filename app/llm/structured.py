"""Validated structured-output requests with bounded stage-local retries."""

import json
import logging
import os
from threading import Event
from collections.abc import Mapping
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.errors import LLMProviderError, ProviderLimitError, StructuredOutputError
from app.llm.client import LLMClient
from app.llm.limits import safe_label
from app.llm.checkpoints import checkpoint_key, load_checkpoint, save_checkpoint, tag_checkpoint
from app.llm.schema import GPT_OSS_MODELS, recovery_format
from app.llm.usage import TokenUsage, usage_scope


T = TypeVar("T", bound=BaseModel)
log = logging.getLogger(__name__)


def _decode_json(content: str) -> Any:
    text = content.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    return json.loads(text)


def _is_provider_json_validation_error(exc: LLMProviderError) -> bool:
    """Return True only when the provider rejected its own generated JSON."""
    return exc.status_code == 400 and exc.code in {"json_validate_failed", "output_parse_failed"}


class StructuredGenerator:
    def __init__(
        self, client: LLMClient, *, max_attempts: int = 2, max_rate_limit_retries: int | None = None
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.client = client
        self.max_attempts = max_attempts
        if max_rate_limit_retries is not None and max_rate_limit_retries < 0:
            raise ValueError("max_rate_limit_retries cannot be negative")
        self.max_rate_limit_retries = max_rate_limit_retries
        self.deadline = None
        self.on_provider_wait = None
        self.usage = TokenUsage()
        self.on_provider_limit = None
        self._limited = Event()

    def _complete(self, *, stage: str, **kwargs) -> str:
        return self.client.complete(
            stage=stage, deadline=self.deadline, on_wait=self.on_provider_wait,
            max_retries=self.max_rate_limit_retries, **kwargs,
        )

    def generate(
        self,
        output_model: type[T],
        *,
        stage: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_schema: Mapping[str, Any] | None = None,
        image_urls: list[str] | None = None,
    ) -> T:
        key = checkpoint_key(
            output_model, stage=stage, model=model, system_prompt=system_prompt,
            user_prompt=user_prompt, temperature=temperature,
            max_tokens=os.getenv(f"LLM_{stage.upper()}_MAX_TOKENS") or max_tokens,
            response_schema=response_schema, image_urls=image_urls,
        )
        cached = load_checkpoint(key)
        if cached is not None:
            return tag_checkpoint(output_model.model_validate_json(cached), key)
        # A stage may narrow references to the current job's IDs. The output
        # model and stage validators remain authoritative after generation.
        schema = (
            response_schema if response_schema is not None else output_model.model_json_schema()
        )
        messages: list[Mapping[str, str]] = [
            {
                "role": "system",
                "content": (
                    f"{system_prompt}\nReturn only one JSON object matching this schema. "
                    f"Do not include markdown or commentary.\nJSON Schema:\n"
                    f"{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
                ),
            },
            {
                "role": "user",
                "content": user_prompt
                if not image_urls
                else [
                    {"type": "text", "text": user_prompt},
                    *[{"type": "image_url", "image_url": {"url": url}} for url in image_urls],
                ],
            },
        ]
        last_detail = "no response"
        request_format = recovery_format(model, output_model.__name__, schema)

        for attempt in range(1, self.max_attempts + 1):
            if self._limited.is_set():
                raise ProviderLimitError()
            try:
                options = {}
                if model in GPT_OSS_MODELS:
                    # Single-slide copy needs room for final JSON within the
                    # smaller completion budget, including hidden reasoning.
                    options["reasoning_effort"] = (
                        "medium" if attempt == 1 and stage not in {"write", "repair"} else "low"
                    )
                with usage_scope(self.usage, stage):
                    raw = self._complete(
                        stage=stage,
                        messages=messages,
                        model=model,
                        temperature=temperature,
                        response_format=request_format,
                        max_tokens=max_tokens,
                        **options,
                    )
            except ProviderLimitError as exc:
                self._limited.set()
                log.warning(
                    "provider limit: stage=%s model=%s HTTP=429 details=%s",
                    safe_label(stage),
                    safe_label(model),
                    exc.diagnostics,
                )
                if self.on_provider_limit is not None:
                    self.on_provider_limit()
                raise
            except LLMProviderError as exc:
                last_detail = str(exc)
                # Groq can reject a completion when its JSON-mode generation
                # is malformed, before returning any content to parse. Retry
                # only that specific response-format failure. In particular,
                # do not use JSON corrections to repeat quota failures.
                # Temporary minute limits have their own bounded wait above.
                if _is_provider_json_validation_error(exc):
                    if attempt == self.max_attempts:
                        raise StructuredOutputError(stage, attempt, last_detail) from exc
                    request_format = recovery_format(model, output_model.__name__, schema)
                    messages = [
                        *messages,
                        {
                            "role": "user",
                            "content": (
                                "The provider could not produce valid JSON. Retry the same task "
                                "and return exactly one syntactically valid JSON object that "
                                "matches the required schema. Return the final JSON only, with no "
                                "analysis, reasoning, markdown, or trailing commas."
                            ),
                        },
                    ]
                    continue
                if exc.code != "empty_response":
                    raise StructuredOutputError(stage, attempt, last_detail) from exc
                if attempt == self.max_attempts:
                    break
                messages = [
                    *messages,
                    {
                        "role": "user",
                        "content": "The provider returned an empty response. Retry the same task and return only valid JSON.",
                    },
                ]
                request_format = recovery_format(model, output_model.__name__, schema)
                continue

            try:
                result = output_model.model_validate(_decode_json(raw))
                if stage != "review" or getattr(result, "passed", True):
                    save_checkpoint(key, result.model_dump_json())
                return tag_checkpoint(result, key)
            except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
                # Corrections go back to the provider, but stored exception
                # details must never echo generated text or validator input.
                last_detail = (
                    "response was not valid JSON"
                    if isinstance(exc, json.JSONDecodeError)
                    else "response did not match the required schema"
                )
                if attempt == self.max_attempts:
                    break
                messages = [
                    *messages,
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": (
                            "The previous JSON did not validate. Correct the response for the same task. "
                            "Validation error: " + str(exc)
                        ),
                    },
                ]
                request_format = recovery_format(model, output_model.__name__, schema)

        raise StructuredOutputError(stage, self.max_attempts, last_detail)
