import sqlite3

import pytest

from app.core.errors import (
    LLMProviderError,
    OutlineSourceError,
    SlideWriteError,
    StructuredOutputError,
)
from app.jobs.failures import USER_ERROR, describe_failure
from app.jobs.store import JobStore


@pytest.mark.parametrize(
    "exc, stage, expected_code",
    [
        (TimeoutError("PRIVATE"), "generate", "generation_timeout"),
        (OutlineSourceError([0]), "generate", "outline_fact_not_in_source"),
        (
            SlideWriteError(2, "PRIVATE_TITLE", "unprovided numeric claim: PRIVATE"),
            "generate",
            "unprovided_numeric_data",
        ),
        (StructuredOutputError("outline", 2, "PRIVATE"), "generate", "invalid_ai_response"),
        (ValueError("story failed final source review"), "generate", "source_review_failed"),
        (RuntimeError("PRIVATE"), "render", "render_or_visual_check_failed"),
        (RuntimeError("PRIVATE"), "generate", "generation_failed"),
    ],
)
def test_failure_details_are_categorized_without_private_exception_text(exc, stage, expected_code):
    details = describe_failure(exc, stage=stage)
    assert details.code == expected_code
    assert "PRIVATE" not in details.model_dump_json()


def test_wrapped_provider_auth_failure_is_distinct_from_rate_limit():
    failure = StructuredOutputError("write", 1, "PRIVATE")
    failure.__cause__ = LLMProviderError("PRIVATE_CREDENTIAL", status_code=401)
    details = describe_failure(failure)
    assert details.code == "provider_access_failed"
    assert details.stage == "write"
    assert details.provider_status == 401
    assert "PRIVATE" not in details.model_dump_json()


def test_failure_details_survive_restart_and_terminal_updates(tmp_path):
    store = JobStore(tmp_path)
    job = store.create("PRIVATE_BRIEF")
    store.mark_running(job.id)
    failure = describe_failure(TimeoutError())
    assert store.fail_if_running(job.id, USER_ERROR, failure=failure)
    with pytest.raises(RuntimeError, match="no longer running"):
        store.update_stage(job.id, "Writing another slide")
    store.mark_failed(job.id, "Another later error", failure=describe_failure(RuntimeError()))
    restored = JobStore(tmp_path).get(job.id)
    assert restored.error == USER_ERROR
    assert restored.failure == failure


def test_legacy_database_migrates_without_losing_jobs(tmp_path):
    store = JobStore(tmp_path)
    job = store.create("A saved brief")
    store.mark_failed(job.id, USER_ERROR)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("ALTER TABLE jobs DROP COLUMN failure")
    migrated = JobStore(tmp_path).get(job.id)
    assert migrated.id == job.id
    assert migrated.status == "failed"
    assert migrated.error == USER_ERROR
    assert migrated.failure is None
