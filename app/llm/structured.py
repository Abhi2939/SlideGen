"""Validated structured-output requests with bounded stage-local retries."""

import json
from collections.abc import Mapping
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.errors import LLMProviderError, StructuredOutputError
from app.llm.client import LLMClient


T = TypeVar("T", bound=BaseModel)


def _decode_json(content: str) -> Any:
    text = content.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    return json.loads(text)


def _is_provider_json_validation_error(exc: LLMProviderError) -> bool:
    """Return True only when the provider rejected its own generated JSON."""
    cause = exc.__cause__
    if getattr(cause, "status_code", None) != 400:
        return False
    body = getattr(cause, "body", None)
    if not isinstance(body, Mapping):
        return False
    error = body.get("error")
    return isinstance(error, Mapping) and error.get("code") == "json_validate_failed"


class StructuredGenerator:
    def __init__(self, client: LLMClient, *, max_attempts: int = 2):
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.client = client
        self.max_attempts = max_attempts

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
    ) -> T:
        schema = output_model.model_json_schema()
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

        for attempt in range(1, self.max_attempts + 1):
            try:
                raw = self.client.complete(
                    messages,
                    model=model,
                    temperature=temperature,
                    response_format={"type": "json_object"},
                    max_tokens=max_tokens,
                )
            except LLMProviderError as exc:
                last_detail = str(exc)
                # Groq can reject a completion when its JSON-mode generation
                # is malformed, before returning any content to parse. Retry
                # only that specific response-format failure. In particular,
                # do not repeat 429/quota errors: another request would spend
                # more of the exhausted provider budget without helping.
                if exc.__cause__ is not None:
                    if not _is_provider_json_validation_error(exc) or attempt == self.max_attempts:
                        raise StructuredOutputError(stage, attempt, last_detail) from exc
                    messages = [
                        *messages,
                        {
                            "role": "user",
                            "content": (
                                "The provider could not produce valid JSON. Retry the same task "
                                "and return exactly one syntactically valid JSON object that "
                                "matches the required schema. Do not use markdown or trailing commas."
                            ),
                        },
                    ]
                    continue
                if attempt == self.max_attempts:
                    break
                messages = [
                    *messages,
                    {
                        "role": "user",
                        "content": "The provider returned an empty response. Retry the same task and return only valid JSON.",
                    },
                ]
                continue

            try:
                return output_model.model_validate(_decode_json(raw))
            except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
                last_detail = str(exc)
                if attempt == self.max_attempts:
                    break
                messages = [
                    *messages,
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": (
                            "The previous JSON did not validate. Correct the response for the same task. "
                            "Validation error: " + last_detail
                        ),
                    },
                ]

        raise StructuredOutputError(stage, self.max_attempts, last_detail)
