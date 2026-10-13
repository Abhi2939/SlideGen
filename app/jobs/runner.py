"""Run deck generation jobs on a bounded in-process thread pool."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from app.core.deck_schema import Deck
from app.core.errors import DeckReviewError
from app.jobs.store import Job, JobStore
from app.pipeline.orchestrator import generate_deck
from app.render.builder import build_deck


log = logging.getLogger(__name__)
USER_ERROR = "Deck generation failed. Please try again."


class JobRunner:
    """Persist submitted jobs and execute them with bounded concurrency."""

    def __init__(
        self,
        store: JobStore,
        *,
        max_workers: int | None = None,
        generate: Callable[[str], Deck] = generate_deck,
        build: Callable[[Deck, str | Path], Path] = build_deck,
    ) -> None:
        configured_workers = max_workers
        if configured_workers is None:
            configured_workers = int(os.getenv("SLIDEGEN_MAX_JOBS", "1"))
        if configured_workers < 1:
            raise ValueError("SLIDEGEN_MAX_JOBS must be at least 1")
        self.store = store
        self.generate = generate
        self.build = build
        self._executor = ThreadPoolExecutor(
            max_workers=configured_workers,
            thread_name_prefix="slidegen-job",
        )

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
        try:
            self.store.mark_running(job_id)
            deck = self.generate(job.prompt)
            self.build(deck, output_path)
            self.store.mark_done(job_id, output_path)
            log.info("job %s completed", job_id)
        except Exception as exc:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                log.exception("could not remove partial output for job %s", job_id)
            log.exception("job %s failed during deck generation", job_id)
            if isinstance(exc, DeckReviewError):
                log.error(
                    "job %s final review findings: %s",
                    job_id,
                    exc.review.model_dump_json(),
                )
            try:
                self.store.mark_failed(job_id, USER_ERROR)
            except Exception:
                log.exception("could not persist failed state for job %s", job_id)

    def close(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def __enter__(self) -> "JobRunner":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
