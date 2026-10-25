"""HTTP endpoints for job submission, status, and deck downloads."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import json
import re
import threading
import time

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import FileResponse

from app.api.model import CreateJobRequest, CreateJobResponse, JobStatusResponse
from app.jobs.runner import JobRunner
from app.jobs.store import JobQueueFullError


router = APIRouter()
PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@router.get("/debug/{deck_id}")
def deck_debug(deck_id: str, request: Request):
    store = request.app.state.runner.store
    if store.get(deck_id) is None:
        raise HTTPException(404, "Deck not found.")
    path = store.output_dir / (deck_id + ".qa") / "report.json"
    if not path.is_file():
        raise HTTPException(409, "Visual report is not available yet.")
    report = json.loads(path.read_text(encoding="utf-8"))
    for attempt in report.get("attempts", []):
        attempt["images"] = [f"/debug/{deck_id}/{image}" for image in attempt.get("images", [])]
    return report


@router.get("/debug/{deck_id}/{iteration}/{filename}")
def deck_debug_image(deck_id: str, iteration: int, filename: str, request: Request):
    store = request.app.state.runner.store
    if (
        store.get(deck_id) is None
        or iteration not in {1, 2, 3}
        or not re.fullmatch(r"slide-\d{2}\.png", filename)
    ):
        raise HTTPException(404, "Slide image not found.")
    path = store.output_dir / (deck_id + ".qa") / str(iteration) / filename
    if not path.is_file():
        raise HTTPException(404, "Slide image not found.")
    return FileResponse(path, media_type="image/png")


class _FixedWindowLimiter:
    def __init__(self, limit: int, window_seconds: float) -> None:
        if limit < 1 or window_seconds <= 0:
            raise ValueError("rate limit and window must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[float, int]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            expired = [
                address
                for address, (started, _) in self._windows.items()
                if now - started >= self.window_seconds
            ]
            for address in expired:
                del self._windows[address]
            started, count = self._windows.get(key, (now, 0))
            if now - started >= self.window_seconds:
                started, count = now, 0
            if count >= self.limit:
                return False
            self._windows[key] = (started, count + 1)
            return True


def _client_address(request: Request) -> str:
    if request.app.state.trust_cloudflare_ip_header:
        forwarded_client = request.headers.get("CF-Connecting-IP", "").strip()
        try:
            return ipaddress.ip_address(forwarded_client).compressed
        except ValueError:
            return "unverified-proxy-client"
    if request.client is None:
        return "unknown"
    return request.client.host


@router.get("/healthz")
def healthz(response: Response) -> dict[str, bool]:
    response.headers["Cache-Control"] = "no-store"
    revision = os.getenv("RENDER_GIT_COMMIT", "")
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision, re.I):
        response.headers["X-SlideGen-Revision"] = revision.lower()
    return {"ok": True}


@router.get("/api/health/llm")
def llm_health(response: Response):
    from app.llm.pacing import shared_limiter

    response.headers["Cache-Control"] = "no-store"
    return shared_limiter().snapshot()


@router.post(
    "/api/jobs",
    response_model=CreateJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_job(
    payload: CreateJobRequest,
    request: Request,
) -> CreateJobResponse:
    if not request.app.state.rate_limiter.allow(_client_address(request)):
        raise HTTPException(
            status_code=429,
            detail="Too many submissions from this address. Please wait and try again.",
        )
    runner: JobRunner = request.app.state.runner
    try:
        job = runner.submit(payload.prompt, max_active=request.app.state.max_active_jobs)
    except JobQueueFullError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return CreateJobResponse(id=job.id, status=job.status)


@router.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(
    job_id: str,
    request: Request,
) -> JobStatusResponse:
    job = request.app.state.runner.store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return JobStatusResponse(
        id=job.id,
        status=job.status,
        error=job.error,
        stage=job.stage,
        queue_position=request.app.state.runner.store.queue_position(job.id),
        failure=job.failure,
    )


@router.get("/api/jobs/{job_id}/download")
def download_job(
    job_id: str,
    request: Request,
) -> FileResponse:
    job = request.app.state.runner.store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status != "done" or job.output_path is None:
        raise HTTPException(status_code=409, detail="The presentation is not ready for download.")
    path = Path(job.output_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="The presentation file is no longer available.")
    return FileResponse(
        path,
        media_type=PPTX_MEDIA_TYPE,
        filename=f"{job.id}.pptx",
    )
