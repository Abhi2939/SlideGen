"""Groq chat-completion adapter."""

from collections.abc import Mapping, Sequence
from typing import Any

import httpx
from groq import Groq

from app.llm.usage import record_usage
from app.llm.schema import GPT_OSS_MODELS


class GroqProvider:
    def __init__(
        self,
        api_key: str | None,
        *,
        timeout: float = 90.0,
        use_system_proxy: bool = False,
    ):
        if not api_key:
            raise ValueError("Set GROQ_API_KEY (or SLIDEGEN_API_KEY) to use Groq")
        self._client = Groq(
            api_key=api_key,
            max_retries=0,
            timeout=timeout,
            http_client=httpx.Client(trust_env=use_system_proxy),
        )

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
            if model in GPT_OSS_MODELS:
                request["include_reasoning"] = False
                if response_format.get("type") == "json_schema":
                    # Recovery must leave room for final JSON within the same
                    # completion budget. Normal first attempts keep defaults.
                    request["reasoning_effort"] = "low"
        if max_tokens is not None:
            request["max_completion_tokens"] = max_tokens
        result = self._client.chat.completions.create(**request)
        record_usage(getattr(result, "usage", None))
        content = result.choices[0].message.content
        return content or ""
