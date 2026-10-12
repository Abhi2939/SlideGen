"""Groq chat-completion adapter."""

from collections.abc import Mapping, Sequence
from typing import Any

from groq import Groq


class GroqProvider:
    def __init__(self, api_key: str | None, *, timeout: float = 90.0):
        if not api_key:
            raise ValueError("Set GROQ_API_KEY (or SLIDEGEN_API_KEY) to use Groq")
        self._client = Groq(api_key=api_key, timeout=timeout)

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        model: str,
        temperature: float = 0.2,
        response_format: Mapping[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> str:
        request: dict[str, Any] = {
            "messages": [dict(message) for message in messages],
            "model": model,
            "temperature": temperature,
        }
        if response_format is not None:
            request["response_format"] = dict(response_format)
        if max_tokens is not None:
            request["max_completion_tokens"] = max_tokens
        result = self._client.chat.completions.create(**request)
        content = result.choices[0].message.content
        return content or ""
