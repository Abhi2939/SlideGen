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
                # The provider SDK already retries transport and rate-limit
                # failures. Repeating the same request here can amplify a 429
                # and consume more of the provider's token budget. Structured
                # retries below are reserved for responses that arrived but
                # did not validate.
                if exc.__cause__ is not None:
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
