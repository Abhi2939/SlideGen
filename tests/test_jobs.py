import json
from pathlib import Path
import threading
import time

import pytest

from app.core.deck_schema import Deck
from app.core.errors import DeckReviewError
from app.jobs.runner import USER_ERROR, JobRunner
from app.jobs.store import JobStore
from app.pipeline.models import ReviewOutput


FIXTURE = Path(__file__).parent / "fixtures" / "decks" / "all_layouts.json"


def fixture_deck() -> Deck:
    return Deck.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


def wait_for_status(store: JobStore, job_id: str, expected: str):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = store.get(job_id)
        if job and job.status == expected:
            return job
        time.sleep(0.01)
    pytest.fail(f"job {job_id} did not reach status {expected!r}")


def test_job_moves_from_queued_to_running_to_done(tmp_path):
    store = JobStore(tmp_path)
    generation_started = threading.Event()
    allow_generation_to_finish = threading.Event()

    def generate(prompt: str) -> Deck:
        assert prompt == "Make a Blush presentation"
        generation_started.set()
        assert allow_generation_to_finish.wait(timeout=5)
        return fixture_deck()

    runner = JobRunner(store, generate=generate)
    try:
        submitted = runner.submit("Make a Blush presentation")
        assert submitted.status == "queued"
        assert generation_started.wait(timeout=5)
        assert wait_for_status(store, submitted.id, "running").status == "running"

        allow_generation_to_finish.set()
        completed = wait_for_status(store, submitted.id, "done")

        assert completed.output_path is not None
        assert Path(completed.output_path).is_file()
        assert completed.error is None
    finally:
        allow_generation_to_finish.set()
        runner.close()


def test_job_failure_stores_clean_user_message(tmp_path):
    store = JobStore(tmp_path)

    def fail_generation(prompt: str) -> Deck:
        raise RuntimeError("private provider detail")

    runner = JobRunner(store, generate=fail_generation)
    try:
        submitted = runner.submit("Make a presentation")
        failed = wait_for_status(store, submitted.id, "failed")
        assert failed.error == USER_ERROR
        assert "private provider detail" not in failed.error
        assert failed.output_path is None
    finally:
        runner.close()


def test_review_failure_logs_findings_but_keeps_user_error_generic(tmp_path, caplog):
    store = JobStore(tmp_path)
    review = ReviewOutput(
        passed=False,
        issues=[
            {
                "category": "unsupported_claim",
                "severity": "medium",
                "detail": "Unsupported AI capability",
                "slide_indices": [1],
            }
        ],
        regenerate_slide_indices=[1],
    )

    def fail_review(prompt: str) -> Deck:
        raise DeckReviewError(review, repair_rounds=2)

    runner = JobRunner(store, generate=fail_review)
    try:
        submitted = runner.submit("Make a product deck")
        failed = wait_for_status(store, submitted.id, "failed")
        assert failed.error == USER_ERROR
        assert "Unsupported AI capability" in caplog.text
        assert submitted.id in caplog.text
    finally:
        runner.close()


def test_startup_recovery_marks_interrupted_jobs_failed(tmp_path):
    store_before_restart = JobStore(tmp_path)
    queued = store_before_restart.create("Interrupted prompt")
    store_before_restart.mark_running(queued.id)

    store_after_restart = JobStore(tmp_path)

    recovered = store_after_restart.get(queued.id)
    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.error == "Server restarted while this job was waiting or running. Please submit it again."
    assert recovered.output_path is None


def test_startup_recovery_marks_queued_jobs_failed(tmp_path):
    store_before_restart = JobStore(tmp_path)
    queued = store_before_restart.create("Waiting prompt")

    store_after_restart = JobStore(tmp_path)

    recovered = store_after_restart.get(queued.id)
    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.error == "Server restarted while this job was waiting or running. Please submit it again."
    assert store_after_restart.count_active() == 0
