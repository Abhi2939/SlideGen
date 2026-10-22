"""Provider-neutral interface for chat completion backends."""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from app.core.errors import LLMProviderError, ProviderLimitError


Message = Mapping[str, str]


class ChatProvider(Protocol):
    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.2,
        response_format: Mapping[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Return the assistant's text content for a chat request."""


class LLMClient:
    """A small adapter that normalizes provider failures for pipeline stages."""

    def __init__(self, provider: ChatProvider):
        self.provider = provider

    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.2,
        response_format: Mapping[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> str:
        try:
            content = self.provider.complete(
                messages,
                model=model,
                temperature=temperature,
                response_format=response_format,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            if isinstance(exc, ProviderLimitError):
                raise
            if getattr(exc, "status_code", None) == 429:
                raise ProviderLimitError() from None
            raise LLMProviderError(f"provider request failed: {exc}") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMProviderError("provider returned an empty response")
        return content.strip()
