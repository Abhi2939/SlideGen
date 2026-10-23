"""Provider-neutral interface for chat completion backends."""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from app.core.errors import LLMProviderError, ProviderLimitError
from app.llm.limits import provider_limit_error


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
        reasoning_effort: str | None = None,
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
        reasoning_effort: str | None = None,
    ) -> str:
        try:
            options = {"reasoning_effort": reasoning_effort} if reasoning_effort is not None else {}
            content = self.provider.complete(
                messages,
                model=model,
                temperature=temperature,
                response_format=response_format,
                max_tokens=max_tokens,
                **options,
            )
        except Exception as exc:
            if isinstance(exc, ProviderLimitError):
                raise
            if getattr(exc, "status_code", None) == 429:
                raise provider_limit_error(exc) from None
            status = getattr(exc, "status_code", None)
            status = status if type(status) is int and 100 <= status <= 599 else None
            body = getattr(exc, "body", None)
            error = body.get("error", body) if isinstance(body, Mapping) else None
            code = error.get("code") if isinstance(error, Mapping) else None
            # Keep only known machine-readable codes. SDK messages/bodies can
            # contain failed_generation, reasoning, prompts, or credentials.
            if not isinstance(code, str) or code not in {
                "json_validate_failed",
                "output_parse_failed",
            }:
                code = None
            detail = "provider request failed"
            if status is not None:
                detail += f" (HTTP {status})"
            if code is not None:
                detail += f": {code}"
            raise LLMProviderError(detail, status_code=status, code=code) from None
        if not isinstance(content, str) or not content.strip():
            raise LLMProviderError("provider returned an empty response", code="empty_response")
        return content.strip()
