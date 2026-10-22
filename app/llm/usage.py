"""Thread-safe counts only, scoped to each structured generator/job."""

from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock


STAGES = ("analyze", "outline", "write", "review", "repair")
_active = ContextVar("token_accounting", default=None)


class TokenUsage:
    def __init__(self):
        self._lock = Lock()
        self._counts = {stage: {"input": 0, "output": 0, "total": 0} for stage in STAGES}

    def add(self, stage, usage):
        if usage is None:
            return
        counts = {
            "input": getattr(usage, "prompt_tokens", 0) or 0,
            "output": getattr(usage, "completion_tokens", 0) or 0,
        }
        counts["total"] = getattr(usage, "total_tokens", None)
        if counts["total"] is None:
            counts["total"] = counts["input"] + counts["output"]
        with self._lock:
            for key, value in counts.items():
                self._counts[stage][key] += int(value)

    def snapshot(self):
        with self._lock:
            return {stage: dict(counts) for stage, counts in self._counts.items()}


@contextmanager
def usage_scope(usage, stage):
    token = _active.set((usage, stage))
    try:
        yield
    finally:
        _active.reset(token)


def record_usage(usage):
    active = _active.get()
    if active:
        active[0].add(active[1], usage)
