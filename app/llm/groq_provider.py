"""Groq chat-completion adapter."""

from collections.abc import Mapping, Sequence
from typing import Any

import httpx
from groq import Groq

from app.llm.usage import record_usage
from app.llm.schema import GPT_OSS_MODELS


class Completion(str):
    """String-compatible result carrying safe transport metadata."""

    def __new__(cls, content, headers, usage):
        result = super().__new__(cls, content)
        result.headers = headers
        result.usage = usage
        return result


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
        reasoning_effort: str | None = None,
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
        if reasoning_effort is not None:
            if model not in GPT_OSS_MODELS or reasoning_effort not in {"low", "medium", "high"}:
                raise ValueError("unsupported reasoning effort for the configured model")
            request["reasoning_effort"] = reasoning_effort
        if max_tokens is not None:
            request["max_completion_tokens"] = max_tokens
        completions = self._client.chat.completions
        if hasattr(completions, "with_raw_response"):
            response = completions.with_raw_response.create(**request)
            result = response.parse()
            headers = response.headers
        else:  # Lightweight provider doubles may expose only create().
            result = completions.create(**request)
            headers = {}
        record_usage(getattr(result, "usage", None))
        content = result.choices[0].message.content
        return Completion(content or "", headers, getattr(result, "usage", None))
