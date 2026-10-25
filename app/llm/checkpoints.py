"""Job-scoped SQLite checkpoints shared by every structured pipeline stage."""

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json

_active = ContextVar("llm_checkpoints", default=None)


@contextmanager
def checkpoint_scope(store, job_id):
    token = _active.set((store, job_id))
    try:
        yield
    finally:
        _active.reset(token)


def checkpoint_key(output_model, **request):
    # Exact inputs, schema and configured generation settings identify a call.
    # Semantic correction/repair requests have different keys. Never persist
    # partial or schema-invalid responses.
    serialized = json.dumps(
        {"schema": output_model.model_json_schema(), **request},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def load_checkpoint(key):
    active = _active.get()
    return active[0].load_checkpoint(active[1], key) if active else None


def save_checkpoint(key, value):
    active = _active.get()
    if active:
        active[0].save_checkpoint(active[1], key, value)


def tag_checkpoint(value, key):
    # Private runtime metadata is excluded from Pydantic JSON/PPTX fields.
    object.__setattr__(value, "_slidegen_checkpoint_key", key)
    return value


def reject_checkpoint(value):
    """A semantic/review rejection must not become a permanent cached failure."""
    active = _active.get()
    key = getattr(value, "_slidegen_checkpoint_key", None)
    if active and key:
        active[0].delete_checkpoint(active[1], key)
