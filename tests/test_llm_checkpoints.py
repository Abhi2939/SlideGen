from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import datetime, timedelta, timezone

import pytest

from app.core.errors import ProviderLimitError, StructuredOutputError
from app.jobs.runner import JobRunner
from app.jobs.store import JobStore
from app.llm.checkpoints import checkpoint_scope, reject_checkpoint
from app.llm.client import LLMClient
from app.llm.structured import StructuredGenerator
from test_jobs import fixture_deck
from test_provider_limits import Provider, Result


def slide(generator, index):
    return generator.generate(Result, stage="write", model="primary", system_prompt="One slide",
                              user_prompt=f"Slide {index}", max_tokens=100)


def test_restart_resumes_completed_slides_and_runner_requeues(tmp_path, monkeypatch):
    store = JobStore(tmp_path)
    job = store.create("Nine slides")
    store.mark_running(job.id)
    first = Provider(['{"title":"Saved"}'] * 4)
    with checkpoint_scope(store, job.id):
        for i in range(4):
            slide(StructuredGenerator(LLMClient(first)), i)

    # Reopen the same SQLite file to simulate losing all process memory.
    reopened = JobStore(tmp_path)
    assert reopened.get(job.id).status == "queued"
    resumed = Provider(['{"title":"New"}'] * 5)

    def generate(prompt, **kwargs):
        generator = StructuredGenerator(LLMClient(resumed))
        results = [slide(generator, i).title for i in range(9)]
        assert results == ["Saved"] * 4 + ["New"] * 5
        return fixture_deck()

    monkeypatch.setattr("app.jobs.runner.generate_deck", generate)
    with JobRunner(reopened):
        pass  # Constructor schedules recovered queued work; close drains it.
    assert reopened.get(job.id).status == "done"
    assert len(resumed.calls) == 5


def test_resubmitting_failed_job_reuses_only_matching_completed_calls(tmp_path):
    store = JobStore(tmp_path)
    job = store.create("Brief")
    store.mark_running(job.id)
    first = Provider(['{"title":"Saved"}', ProviderLimitError()])
    generator = StructuredGenerator(LLMClient(first))
    with checkpoint_scope(store, job.id):
        slide(generator, 0)
        with pytest.raises(ProviderLimitError):
            slide(generator, 1)
    store.mark_failed(job.id, "Temporarily busy")
    retried = store.create("Brief")
    store.mark_running(retried.id)
    resumed = Provider(['{"title":"New"}'])
    with checkpoint_scope(store, retried.id):
        generator = StructuredGenerator(LLMClient(resumed))
        assert slide(generator, 0).title == "Saved"
        assert slide(generator, 1).title == "New"
    assert len(resumed.calls) == 1


def test_changed_inputs_invalidate_cache(tmp_path):
    store = JobStore(tmp_path)
    job = store.create("Brief")
    store.mark_running(job.id)
    provider = Provider(['{"title":"Original"}', '{"title":"Changed"}'])
    generator = StructuredGenerator(LLMClient(provider))
    with checkpoint_scope(store, job.id):
        assert slide(generator, 0).title == "Original"
        assert slide(generator, 0).title == "Original"
        assert slide(generator, 1).title == "Changed"
    assert len(provider.calls) == 2


def test_invalid_json_and_semantically_rejected_slides_are_not_reused(tmp_path):
    store = JobStore(tmp_path)
    job = store.create("Brief")
    store.mark_running(job.id)
    provider = Provider(['not JSON', '{"title":"Unsupported"}', '{"title":"Supported"}'])
    generator = StructuredGenerator(LLMClient(provider), max_attempts=1)
    with checkpoint_scope(store, job.id):
        with pytest.raises(StructuredOutputError):
            slide(generator, 0)
        rejected = slide(generator, 0)
        reject_checkpoint(rejected)
        assert slide(generator, 0).title == "Supported"
        assert slide(generator, 0).model_dump() == {"title": "Supported"}
    assert len(provider.calls) == 3


def test_checkpoint_scope_propagates_to_legacy_worker_threads(tmp_path):
    store = JobStore(tmp_path)
    job = store.create("Brief")
    store.mark_running(job.id)
    provider = Provider(['{"title":"Saved"}'])
    generator = StructuredGenerator(LLMClient(provider))
    with checkpoint_scope(store, job.id), ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(copy_context().run, slide, generator, 0).result().title == "Saved"
        assert pool.submit(copy_context().run, slide, generator, 0).result().title == "Saved"
    assert len(provider.calls) == 1


def test_checkpoint_retention_cleanup(tmp_path):
    store = JobStore(tmp_path, retention_hours=1)
    job = store.create("Brief")
    store.mark_running(job.id)
    store.save_checkpoint(job.id, "slide-0", "data")
    store.mark_failed(job.id, "Failed")
    store.cleanup_expired(now=datetime.now(timezone.utc) + timedelta(hours=2))
    assert store.load_checkpoint(job.id, "slide-0") is None
