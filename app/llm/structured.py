"""Validated structured-output requests with bounded stage-local retries."""

import json
from threading import Event
from collections.abc import Mapping
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.errors import LLMProviderError, ProviderLimitError, StructuredOutputError
from app.llm.client import LLMClient
from app.llm.schema import recovery_format
from app.llm.usage import TokenUsage, usage_scope


T = TypeVar("T", bound=BaseModel)


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
    def __init__(self, client: LLMClient, *, max_attempts: int = 2):
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.client = client
        self.max_attempts = max_attempts
        self.usage = TokenUsage()
        self.on_provider_limit = None
        self._limited = Event()

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
        request_format = {"type": "json_object"}

        for attempt in range(1, self.max_attempts + 1):
            if self._limited.is_set():
                raise ProviderLimitError()
            try:
                with usage_scope(self.usage, stage):
                    raw = self.client.complete(
                        messages,
                        model=model,
                        temperature=temperature,
                        response_format=request_format,
                        max_tokens=max_tokens,
                    )
            except ProviderLimitError:
                self._limited.set()
                if self.on_provider_limit is not None:
                    self.on_provider_limit()
                raise
            except LLMProviderError as exc:
                last_detail = str(exc)
                # Groq can reject a completion when its JSON-mode generation
                # is malformed, before returning any content to parse. Retry
                # only that specific response-format failure. In particular,
                # do not repeat 429/quota errors: another request would spend
                # more of the exhausted provider budget without helping.
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
