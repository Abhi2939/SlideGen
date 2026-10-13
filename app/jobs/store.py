"""Thread-safe SQLite storage for asynchronous deck jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import threading
import uuid


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class Job:
    id: str
    prompt: str
    status: str
    output_path: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class JobQueueFullError(Exception):
    """Raised when the configured number of active jobs has been reached."""


class JobStore:
    """Persist job state and generated file paths in a local SQLite database."""

    def __init__(
        self,
        data_dir: str | Path | None = None,
        *,
        retention_hours: float | None = None,
    ) -> None:
        configured_dir = data_dir if data_dir is not None else os.getenv("SLIDEGEN_DATA_DIR", "./data")
        self.data_dir = Path(configured_dir).expanduser().resolve()
        self.output_dir = self.data_dir / "outputs"
        self.database_path = self.data_dir / "jobs.sqlite3"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        value = retention_hours
        if value is None:
            value = float(os.getenv("SLIDEGEN_RETENTION_HOURS", "24"))
        if value <= 0:
            raise ValueError("SLIDEGEN_RETENTION_HOURS must be greater than zero")
        self.retention = timedelta(hours=value)
        self._lock = threading.RLock()
        self._initialize()
        self.recover_running_jobs()
        self.cleanup_expired()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed')),
                    output_path TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )

    @staticmethod
    def _from_row(row: sqlite3.Row | None) -> Job | None:
        if row is None:
            return None
        return Job(
            id=row["id"],
            prompt=row["prompt"],
            status=row["status"],
            output_path=row["output_path"],
            error=row["error"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def create(self, prompt: str, *, max_active: int | None = None) -> Job:
        now = _timestamp(_utc_now())
        job_id = uuid.uuid4().hex
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if max_active is not None:
                active = connection.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'running')"
                ).fetchone()[0]
                if active >= max_active:
                    raise JobQueueFullError("The job queue is full. Please try again shortly.")
            connection.execute(
                "INSERT INTO jobs (id, prompt, status, created_at, updated_at) VALUES (?, ?, 'queued', ?, ?)",
                (job_id, prompt, now, now),
            )
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._from_row(row)  # type: ignore[return-value]

    def count_active(self) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'running')"
            ).fetchone()
        return int(row[0])

    def get(self, job_id: str) -> Job | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._from_row(row)

    def mark_running(self, job_id: str) -> Job:
        return self._update(job_id, status="running", output_path=None, error=None)

    def mark_done(self, job_id: str, output_path: str | Path) -> Job:
        return self._update(
            job_id,
            status="done",
            output_path=str(Path(output_path).resolve()),
            error=None,
        )

    def mark_failed(self, job_id: str, error: str) -> Job:
        return self._update(job_id, status="failed", output_path=None, error=error)

    def _update(
        self,
        job_id: str,
        *,
        status: str,
        output_path: str | None,
        error: str | None,
    ) -> Job:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET status = ?, output_path = ?, error = ?, updated_at = ? WHERE id = ?",
                (status, output_path, error, _timestamp(_utc_now()), job_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"unknown job id: {job_id}")
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._from_row(row)  # type: ignore[return-value]

    def recover_running_jobs(self) -> int:
        """Fail jobs left running by a previous server process."""
        now = _timestamp(_utc_now())
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET status = 'failed', error = ?, output_path = NULL, updated_at = ? "
                "WHERE status = 'running'",
                ("Server restarted while this job was running. Please submit it again.", now),
            )
        return cursor.rowcount

    def cleanup_expired(self, *, now: datetime | None = None) -> int:
        """Remove expired job rows and any output files they reference."""
        cutoff = _timestamp((now or _utc_now()) - self.retention)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT id, output_path FROM jobs WHERE created_at < ?", (cutoff,)
            ).fetchall()
            connection.executemany("DELETE FROM jobs WHERE id = ?", [(row["id"],) for row in rows])
        for row in rows:
            output = row["output_path"]
            if output:
                candidate = Path(output).resolve()
                try:
                    candidate.relative_to(self.output_dir.resolve())
                except ValueError:
                    continue
                candidate.unlink(missing_ok=True)
        return len(rows)
