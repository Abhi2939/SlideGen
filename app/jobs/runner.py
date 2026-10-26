"""Run deck generation jobs on a bounded in-process thread pool."""

from __future__ import annotations

import logging
import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import time
from threading import Timer, Lock
from datetime import datetime, timezone
from typing import Callable

from app.core.deck_schema import Deck
from app.core.errors import DeckReviewError, LLMProviderError, ProviderLimitError
from app.jobs.store import Job, JobStore
from app.jobs.failures import USER_ERROR, describe_failure, failure_location
from app.llm.usage import TokenUsage
from app.llm.limits import safe_label
from app.llm.checkpoints import checkpoint_scope
from app.pipeline.orchestrator import generate_deck
from app.render.builder import build_deck


log = logging.getLogger(__name__)


class JobRunner:
    """Persist submitted jobs and execute them with bounded concurrency."""

    def __init__(
        self,
        store: JobStore,
        *,
        max_workers: int | None = None,
        generate: Callable[[str], Deck] | None = None,
        build: Callable[[Deck, str | Path], Path] = build_deck,
        max_runtime_seconds: float | None = None,
        delivery_mode: str | None = None,
    ) -> None:
        configured_workers = max_workers
        if configured_workers is None:
            configured_workers = int(os.getenv("SLIDEGEN_MAX_JOBS", "1"))
        if configured_workers < 1:
            raise ValueError("SLIDEGEN_MAX_JOBS must be at least 1")
        self.delivery_mode = delivery_mode or os.getenv("SLIDEGEN_DELIVERY_MODE", "draft")
        if self.delivery_mode not in {"draft", "reviewed"}:
            raise ValueError("SLIDEGEN_DELIVERY_MODE must be draft or reviewed")
        configured_runtime = max_runtime_seconds
        if configured_runtime is None:
            configured_runtime = float(os.getenv("SLIDEGEN_JOB_TIMEOUT_SECONDS", "1800"))
            if self.delivery_mode == "draft":
                configured_runtime = min(configured_runtime, 600)
        if configured_runtime <= 0:
            raise ValueError("SLIDEGEN_JOB_TIMEOUT_SECONDS must be greater than zero")
        self.store = store
        self._uses_default_generator = generate is None
        self.generate = generate or generate_deck
        self.build = build
        self.max_runtime_seconds = configured_runtime
        self._executor = ThreadPoolExecutor(
            max_workers=configured_workers,
            thread_name_prefix="slidegen-job",
        )
        self._review_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="slidegen-review"
        )
        self._review_lock = Lock()
        self._reviews = set()
        for job_id in store.queued_ids():
            self._executor.submit(self._run, job_id)

    def submit(self, prompt: str, *, max_active: int | None = None) -> Job:
        self.store.cleanup_expired()
        job = self.store.create(prompt, max_active=max_active)
        self._executor.submit(self._run, job.id)
        return job

    def _run(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if job is None:
            log.error("job %s disappeared before execution", job_id)
            return
        output_path = self.store.output_dir / f"{job_id}.pptx"
        # A deploy must not restart the time budget of an old queued job.
        age = max(0, (datetime.now(timezone.utc) - job.created_at).total_seconds())
        remaining = (
            self.max_runtime_seconds - age
            if self.delivery_mode == "draft"
            else self.max_runtime_seconds
        )
        deadline = time.monotonic() + remaining
        failure_stage = "generate"

        def report_stage(stage: str) -> None:
            if time.monotonic() >= deadline:
                raise TimeoutError("deck generation exceeded its time limit")
            self.store.update_stage(job_id, stage)

        def time_out_job() -> None:
            try:
                if self.store.fail_if_running(
                    job_id,
                    USER_ERROR,
                    failure=describe_failure(TimeoutError(), stage=failure_stage),
                ):
                    output_path.unlink(missing_ok=True)
                    log.warning("job %s exceeded its time limit", job_id)
            except Exception:
                log.exception("could not time out job %s", job_id)

        timeout_timer = Timer(max(0, remaining), time_out_job)
        timeout_timer.daemon = True
        token_counts = TokenUsage().snapshot()
        try:
            self.store.mark_running(job_id)
            if remaining <= 0:
                raise TimeoutError("job exceeded its queue or generation time limit")
            timeout_timer.start()
            if self._uses_default_generator:
                with checkpoint_scope(self.store, job_id):
                    deck = self.generate(
                        job.prompt,
                        design="draft" if self.delivery_mode == "draft" else None,
                        deadline=deadline,
                        progress_callback=report_stage,
                        usage_callback=token_counts.update,
                    )
            else:
                report_stage("Generating presentation")
                deck = self.generate(job.prompt)
            if time.monotonic() >= deadline:
                raise TimeoutError("deck generation exceeded its time limit")
            report_stage("Rendering PowerPoint")
            failure_stage = "render"
            if deck.design_version == "draft-v1":
                from app.qa.draft import build_draft

                build_draft(deck, output_path, deadline=deadline, progress_callback=report_stage)
            elif deck.design_version == "gamma-v1" and self.delivery_mode == "reviewed":
                from app.qa.gate import build_checked

                build_checked(
                    deck,
                    output_path,
                    source_prompt=job.prompt,
                    deadline=deadline,
                    progress_callback=report_stage,
                )
            else:
                self.build(deck, output_path)
            if time.monotonic() >= deadline:
                raise TimeoutError("deck generation exceeded its time limit")
            self.store.mark_done(job_id, output_path)
            log.info("job %s completed", job_id)
        except Exception as exc:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                log.exception("could not remove partial output for job %s", job_id)
            if isinstance(exc, ProviderLimitError):
                log.warning("job %s stopped: provider quota/rate limit", job_id)
            else:
                cause = exc
                provider_code = None
                while cause is not None:
                    if isinstance(cause, LLMProviderError) and cause.code in {
                        "output_parse_failed",
                        "json_validate_failed",
                        "empty_response",
                    }:
                        provider_code = cause.code
                    cause = cause.__cause__
                log.warning(
                    "job %s failed during deck generation: type=%s provider_code=%s "
                    "reason=%s stage=%s slide_index=%s location=%s",
                    job_id,
                    type(exc).__name__,
                    provider_code,
                    safe_label(str(exc.reason)) if hasattr(exc, "reason") else None,
                    safe_label(str(exc.stage)) if hasattr(exc, "stage") else None,
                    exc.slide_index if type(getattr(exc, "slide_index", None)) is int else None,
                    failure_location(exc),
                )
            if isinstance(exc, DeckReviewError):
                log.error(
                    "job %s final review findings: issues=%d missing_facts=%d repair_rounds=%d",
                    job_id,
                    len(exc.review.issues),
                    len(exc.review.missing_fact_ids),
                    exc.repair_rounds,
                )
            try:
                self.store.mark_failed(
                    job_id,
                    str(exc) if isinstance(exc, ProviderLimitError) else USER_ERROR,
                    failure=describe_failure(exc, stage=failure_stage),
                )
            except Exception:
                log.exception("could not persist failed state for job %s", job_id)
        finally:
            timeout_timer.cancel()
            log.info("job %s token usage (reported calls only): %s", job_id, token_counts)

    def close(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)
        self._review_executor.shutdown(wait=wait, cancel_futures=True)

    def review_status(self, job_id):
        job = self.store.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.status != "done" or job.output_path is None:
            raise ValueError("The PowerPoint draft is not ready yet.")
        folder = self.store.output_dir / (job_id + ".qa")
        if not (folder / "deck.json").is_file():
            raise ValueError("Optional design review is unavailable for this older presentation.")
        path = folder / "review.json"
        report = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.is_file()
            else {"status": "not_requested"}
        )
        if report["status"] in {"running", "queued"} and job_id not in self._reviews:
            report = {
                "status": "interrupted",
                "message": "Review was interrupted. Your draft is still available.",
            }
        return report

    def request_review(self, job_id):
        from app.qa.draft import write_json

        with self._review_lock:
            report = self.review_status(job_id)
            if job_id in self._reviews or report["status"] == "completed":
                return report
            if self._reviews:
                raise ValueError(
                    "Another design review is running. Your draft is available; try again later."
                )
            report = {
                "status": "queued",
                "message": "Optional design review queued. Your draft remains available.",
            }
            write_json(self.store.output_dir / (job_id + ".qa") / "review.json", report)
            self._reviews.add(job_id)
            self._review_executor.submit(self._review, job_id)
            return report

    def _review(self, job_id):
        from app.qa.draft import review_draft, write_json

        try:
            job = self.store.get(job_id)
            if job and job.output_path:
                review_draft(job.output_path, deadline=time.monotonic() + 300)
        except Exception as exc:
            log.warning("optional design review failed: job=%s type=%s", job_id, type(exc).__name__)
            write_json(
                self.store.output_dir / (job_id + ".qa") / "review.json",
                {
                    "status": "failed",
                    "message": "Design review could not finish. Your draft is still available.",
                },
            )
        finally:
            with self._review_lock:
                self._reviews.discard(job_id)

    def __enter__(self) -> "JobRunner":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
