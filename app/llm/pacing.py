"""Process-wide, thread-safe admission control for the synchronous job workers."""

from collections import defaultdict, deque
from dataclasses import dataclass
import math
import os
import threading
import time

from app.core.errors import LLMProviderError


@dataclass(frozen=True)
class RateSettings:
    tpm: int = 8000
    safety_margin: float = 0.85
    concurrency: int = 1
    max_retries: int = 8
    wait_budget: float = 180
    fallback_after: float = 30
    fallback_models: tuple[str, ...] = ()

    @classmethod
    def from_env(cls):
        settings = cls(
            tpm=int(os.getenv("LLM_TPM_LIMIT", "8000")),
            safety_margin=float(os.getenv("LLM_SAFETY_MARGIN", "0.85")),
            concurrency=int(os.getenv("LLM_CONCURRENCY", "1")),
            max_retries=int(os.getenv("LLM_MAX_RETRIES", "8")),
            wait_budget=float(os.getenv("LLM_WAIT_BUDGET_SECONDS", "180")),
            fallback_after=float(os.getenv("LLM_FALLBACK_AFTER_SECONDS", "30")),
            fallback_models=tuple(dict.fromkeys(
                m.strip() for m in os.getenv("LLM_FALLBACK_MODELS", "").split(",") if m.strip()
            )),
        )
        if (settings.tpm < 1 or not 0 < settings.safety_margin <= 1
                or settings.concurrency < 1 or settings.max_retries < 0
                or not math.isfinite(settings.wait_budget) or settings.wait_budget <= 0
                or not math.isfinite(settings.fallback_after) or settings.fallback_after < 0):
            raise ValueError("Invalid LLM rate limit settings")
        return settings


class TokenLimiter:
    """Reserve prompt + requested output tokens for a full rolling minute.

    Header feedback can restrict admission further, never erase reservations.
    The semaphore is shared across clients/jobs, as are the model windows.
    """

    def __init__(self, settings=None, *, clock=None, sleep=None):
        self.settings = settings or RateSettings.from_env()
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self.semaphore = threading.BoundedSemaphore(self.settings.concurrency)
        self._lock = threading.Lock()
        self._windows = defaultdict(deque)
        self._remote = {}
        self._blocked = {}

    @property
    def budget(self):
        return max(1, int(self.settings.tpm * self.settings.safety_margin))

    def _prune(self, model, now):
        window = self._windows[model]
        while window and window[0][0] <= now - 60:
            window.popleft()
        if model in self._remote and self._remote[model][1] <= now:
            del self._remote[model]

    def reserve(self, model, cost):
        """Atomically reserve if possible; otherwise return the next delay."""
        if cost > self.budget:
            raise LLMProviderError(
                "AI request exceeds the configured token window; shorten the brief or "
                "reduce this stage's token budget.", code="request_too_large"
            )
        with self._lock:
            now = self.clock()
            self._prune(model, now)
            window = self._windows[model]
            delay = max(0, self._blocked.get(model, 0) - now)
            usage = sum(tokens for _, tokens in window)
            for stamp, tokens in window:
                if usage + cost <= self.budget:
                    break
                delay = max(delay, stamp + 60 - now)
                usage -= tokens
            remaining, reset = self._remote.get(model, (self.settings.tpm, now))
            if cost > remaining:
                delay = max(delay, reset - now)
            if delay > 0:
                return delay
            window.append((now, cost))
            if model in self._remote:
                self._remote[model] = (max(0, remaining - cost), reset)
            return 0

    def feedback(self, model, details):
        with self._lock:
            now = self.clock()
            remaining = details.get("tokens_per_minute_remaining")
            reset = details.get("tokens_reset_seconds")
            if remaining is not None:
                self._remote[model] = (remaining, now + (reset if reset is not None else 60))

    def block(self, model, seconds):
        with self._lock:
            self._blocked[model] = max(self._blocked.get(model, 0), self.clock() + seconds)

    def snapshot(self):
        with self._lock:
            now = self.clock()
            result = {}
            for model in self._windows:
                self._prune(model, now)
                result[model] = {
                    "window_tokens": sum(n for _, n in self._windows[model]),
                    "budget_tokens": self.budget,
                    "provider_remaining_tokens": self._remote.get(model, (None,))[0],
                    "blocked_seconds": round(max(0, self._blocked.get(model, 0) - now), 2),
                }
            return {"tpm_limit": self.settings.tpm, "concurrency": self.settings.concurrency,
                    "models": result}


_shared = None
_shared_lock = threading.Lock()


def shared_limiter():
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = TokenLimiter()
        return _shared
