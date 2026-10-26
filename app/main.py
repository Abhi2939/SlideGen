"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
import os
import logging
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from app.api.routes import _FixedWindowLimiter, router
from app.jobs.runner import JobRunner
from app.jobs.store import JobStore


def create_app(
    *,
    runner: JobRunner | None = None,
    store: JobStore | None = None,
    data_dir: str | Path | None = None,
    max_active_jobs: int | None = None,
    submissions_per_window: int = 10,
    rate_window_seconds: float = 60,
    trust_cloudflare_ip_header: bool | None = None,
) -> FastAPI:
    load_dotenv()
    # Uvicorn configures its own access loggers, not application loggers.
    # Keep stage/count/token diagnostics visible in Render without enabling
    # verbose HTTP-library logs.
    logging.basicConfig(format="%(levelname)s:%(name)s:%(message)s")
    logging.getLogger("app").setLevel(logging.INFO)
    active_limit = max_active_jobs
    if active_limit is None:
        active_limit = int(os.getenv("SLIDEGEN_MAX_ACTIVE_JOBS", "20"))
        if os.getenv("SLIDEGEN_DELIVERY_MODE", "draft") == "draft":
            active_limit = min(active_limit, 3)
    if active_limit < 1:
        raise ValueError("SLIDEGEN_MAX_ACTIVE_JOBS must be at least 1")
    trust_header_value = os.getenv("SLIDEGEN_TRUST_CLOUDFLARE_IP_HEADER", "false").strip().lower()
    if trust_header_value not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
        raise ValueError("SLIDEGEN_TRUST_CLOUDFLARE_IP_HEADER must be a boolean value")
    trust_client_ip = (
        trust_cloudflare_ip_header
        if trust_cloudflare_ip_header is not None
        else trust_header_value in {"1", "true", "yes", "on"}
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.runner = runner or JobRunner(store or JobStore(data_dir))
        application.state.max_active_jobs = active_limit
        application.state.trust_cloudflare_ip_header = trust_client_ip
        application.state.rate_limiter = _FixedWindowLimiter(
            submissions_per_window,
            rate_window_seconds,
        )
        try:
            yield
        finally:
            application.state.runner.close()

    application = FastAPI(title="SlideGen", lifespan=lifespan)
    application.include_router(router)
    static_dir = Path(__file__).resolve().parent / "static"
    application.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return application


app = create_app()
