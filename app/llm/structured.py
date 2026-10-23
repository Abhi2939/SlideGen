"""Validated structured-output requests with bounded stage-local retries."""

import json
import logging
import time
from threading import Event
from collections.abc import Mapping
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.errors import LLMProviderError, ProviderLimitError, StructuredOutputError
from app.llm.client import LLMClient
from app.llm.limits import safe_label
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
        self, client: LLMClient, *, max_attempts: int = 2, max_rate_limit_retries: int = 2
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.client = client
        self.max_attempts = max_attempts
        if max_rate_limit_retries < 0:
            raise ValueError("max_rate_limit_retries cannot be negative")
        self.max_rate_limit_retries = max_rate_limit_retries
        self.deadline = None
        self.on_provider_wait = None
        self.usage = TokenUsage()
        self.on_provider_limit = None
        self._limited = Event()

    def _complete(self, *, stage: str, **kwargs) -> str:
        """Resume only a temporarily throttled call, within the job deadline."""
        for retry in range(self.max_rate_limit_retries + 1):
            if self.deadline is not None and time.monotonic() >= self.deadline:
                raise TimeoutError("deck generation exceeded its time limit")
            if self._limited.is_set():
                raise ProviderLimitError()
            try:
                return self.client.complete(**kwargs)
            except ProviderLimitError as exc:
                details = exc.diagnostics
                seconds = details.get("retry_after_seconds")
                oversized = details.get("requested", 0) > details.get("limit", float("inf"))
                temporary = (
                    details.get("limit_type")
                    in {
                        "tokens_per_minute",
                        "requests_per_minute",
                        "input_tokens_per_minute",
                        "output_tokens_per_minute",
                    }
                    and type(seconds) is int
                    and 0 < seconds <= 60
                    and not oversized
                    and details.get("requests_per_day_remaining") != 0
                )
                if not temporary or retry == self.max_rate_limit_retries:
                    raise
                if self.deadline is not None and time.monotonic() + seconds >= self.deadline:
                    raise TimeoutError("deck generation exceeded its time limit") from None
                log.warning(
                    "provider minute limit: stage=%s model=%s waiting_seconds=%s retry=%s details=%s",
                    safe_label(stage),
                    safe_label(kwargs["model"]),
                    seconds,
                    retry + 1,
                    details,
                )
                if self.on_provider_wait is not None:
                    self.on_provider_wait(stage, seconds)
                time.sleep(seconds)
                if self.on_provider_wait is not None:
                    self.on_provider_wait(stage, 0)

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
    ) -> T:
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
                    f"{json.dumps(schema, ensure_ascii=False)}"
                ),
            },
            {"role": "user", "content": user_prompt},
        ]
        last_detail = "no response"
        request_format = recovery_format(model, output_model.__name__, schema)

        for attempt in range(1, self.max_attempts + 1):
            if self._limited.is_set():
                raise ProviderLimitError()
            try:
                options = {}
                if model in GPT_OSS_MODELS:
                    # Strict decoding starts on the first call. Preserve the
                    # previous normal reasoning default; only correction calls
                    # use the existing low-effort recovery policy.
                    options["reasoning_effort"] = "medium" if attempt == 1 else "low"
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
                return output_model.model_validate(_decode_json(raw))
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
