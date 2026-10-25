"""Provider-neutral interface for chat completion backends."""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol
import logging
import math
import os
import random

from app.core.errors import LLMProviderError, ProviderLimitError
from app.llm.limits import header_diagnostics, provider_limit_error, safe_label
from app.llm.pacing import shared_limiter
from app.llm.schema import GPT_OSS_MODELS

log = logging.getLogger(__name__)


def prompt_tokens(messages):
    # Do not count base64 image bytes as text tokens. Reserve a conservative
    # image allowance; provider feedback supplies the actual remaining budget.
    chars = 0
    images = 0
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            chars += len(content)
        else:
            for part in content:
                if part.get("type") == "text":
                    chars += len(part.get("text", ""))
                elif part.get("type") == "image_url":
                    images += 1600
    return math.ceil(chars / 4) + 8 * len(messages) + images


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

    def __init__(self, provider: ChatProvider, *, limiter=None):
        self.provider = provider
        self.limiter = limiter or shared_limiter()

    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.2,
        response_format: Mapping[str, Any] | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        stage: str = "generate",
        deadline: float | None = None,
        on_wait=None,
        max_retries: int | None = None,
    ) -> str:
        limiter = self.limiter
        settings = limiter.settings
        retries_limit = settings.max_retries if max_retries is None else max_retries
        defaults = {"outline": 3600, "analyze": 1800, "write": 1800, "repair": 1600, "review": 1600}
        max_tokens = int(
            os.getenv(f"LLM_{stage.upper()}_MAX_TOKENS") or max_tokens or defaults.get(stage, 1600)
        )
        if max_tokens <= 0:
            raise ValueError("LLM stage max_tokens must be positive")
        estimated_prompt = prompt_tokens(messages)
        models = list(dict.fromkeys((model, *settings.fallback_models)))
        model_index = 0
        retries = 0
        waited = 0.0
        model_waited = 0.0
        completion_count = 0
        actual_prompt = estimated_prompt
        last_limit = None

        def check_deadline():
            if deadline is not None and limiter.clock() >= deadline:
                raise TimeoutError("deck generation exceeded its time limit")

        def wait(seconds):
            nonlocal waited, model_waited
            check_deadline()
            if waited + seconds > settings.wait_budget:
                raise last_limit or ProviderLimitError(
                    diagnostics={"limit_type": "tokens_per_minute"}
                )
            if deadline is not None and limiter.clock() + seconds >= deadline:
                raise TimeoutError("deck generation exceeded its time limit")
            if on_wait:
                on_wait(stage, math.ceil(seconds))
            # Keep cancellation/deadline and status reporting responsive.
            remaining = seconds
            while remaining > 0:
                chunk = min(remaining, 10)
                limiter.sleep(chunk)
                waited += chunk
                model_waited += chunk
                remaining -= chunk
                check_deadline()
                if on_wait and remaining > 0:
                    on_wait(stage, math.ceil(remaining))
            if on_wait:
                on_wait(stage, 0)

        try:
            while True:
                check_deadline()
                current = models[model_index]
                # Synchronous pipeline runs in threads, so a process-wide
                # threading semaphore also covers multiple job executors.
                if not limiter.semaphore.acquire(blocking=False):
                    wait(1)
                    continue
                delay = 0
                try:
                    delay = limiter.reserve(current, estimated_prompt + max_tokens)
                    if not delay:
                        fmt = response_format
                        effort = reasoning_effort
                        if current not in GPT_OSS_MODELS:
                            effort = None
                            if fmt and fmt.get("type") == "json_schema":
                                fmt = {"type": "json_object"}
                        try:
                            content = self._request(
                                messages,
                                model=current,
                                temperature=temperature,
                                response_format=fmt,
                                max_tokens=max_tokens,
                                reasoning_effort=effort,
                            )
                        except ProviderLimitError as exc:
                            last_limit = exc
                            details = exc.diagnostics
                            limiter.feedback(current, details)
                            kind = details.get("limit_type")
                            oversized = details.get("requested", 0) > details.get(
                                "limit", float("inf")
                            )
                            if kind in {"requests_per_day", "tokens_per_day"} or oversized:
                                raise
                            # Unknown 429s with Retry-After are transient too;
                            # absent both classification and timing, fail safely.
                            if not kind and "retry_after_seconds" not in details:
                                raise
                            if retries >= retries_limit:
                                raise
                            delay = details.get("retry_after_seconds")
                            if delay is None:
                                delay = details.get(
                                    "tokens_reset_seconds", min(2 ** (retries + 1), 60)
                                )
                            delay = max(0.1, delay) + random.uniform(0.1, 0.5)
                            limiter.block(current, delay)
                            retries += 1
                            log.warning(
                                "provider wait: stage=%s model=%s retry=%d details=%s",
                                safe_label(stage),
                                safe_label(current),
                                retries,
                                details,
                            )
                        else:
                            limiter.feedback(
                                current, header_diagnostics(getattr(content, "headers", {}))
                            )
                            usage = getattr(content, "usage", None)
                            actual_prompt = getattr(usage, "prompt_tokens", estimated_prompt)
                            completion_count = getattr(
                                usage, "completion_tokens", math.ceil(len(content) / 4)
                            )
                            return content.strip()
                finally:
                    limiter.semaphore.release()
                if model_index + 1 < len(models):
                    until_fallback = max(0, settings.fallback_after - model_waited)
                    if delay >= until_fallback:
                        wait(until_fallback)
                        model_index += 1
                        model_waited = 0
                        continue
                wait(delay)
        finally:
            log.info(
                "llm call: stage=%s model=%s prompt_tokens=%s completion_tokens=%s "
                "wait_seconds=%.2f retries=%d fallback_used=%s",
                safe_label(stage),
                safe_label(models[model_index]),
                actual_prompt,
                completion_count,
                waited,
                retries,
                model_index > 0,
            )

    def _request(
        self, messages, *, model, temperature, response_format, max_tokens, reasoning_effort
    ):
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
        return content
