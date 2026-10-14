"""HTTP endpoints for job submission, status, and deck downloads."""

from __future__ import annotations

import hmac
from pathlib import Path
import threading
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import FileResponse

from app.api.model import CreateJobRequest, CreateJobResponse, JobStatusResponse
from app.jobs.runner import JobRunner
from app.jobs.store import JobQueueFullError


router = APIRouter()
PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


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
    if request.client is None:
        return "unknown"
    return request.client.host


def _require_access_code(
    request: Request,
    access_code: str | None = Header(default=None, alias="X-Access-Code"),
) -> None:
    expected = request.app.state.access_code
    if expected and (access_code is None or not hmac.compare_digest(access_code, expected)):
        raise HTTPException(status_code=401, detail="A valid access code is required.")


@router.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@router.post(
    "/api/jobs",
    response_model=CreateJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_job(
    payload: CreateJobRequest,
    request: Request,
    _: None = Depends(_require_access_code),
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
    _: None = Depends(_require_access_code),
) -> JobStatusResponse:
    job = request.app.state.runner.store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return JobStatusResponse(id=job.id, status=job.status, error=job.error)


@router.get("/api/jobs/{job_id}/download")
def download_job(
    job_id: str,
    request: Request,
    _: None = Depends(_require_access_code),
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
