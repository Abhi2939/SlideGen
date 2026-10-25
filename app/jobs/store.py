"""Thread-safe SQLite storage for asynchronous deck jobs."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import shutil
import threading
import uuid
from typing import Iterator

from app.jobs.failures import JobFailure


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
    stage: str | None
    created_at: datetime
    updated_at: datetime
    failure: JobFailure | None = None


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
        configured_dir = (
            data_dir if data_dir is not None else os.getenv("SLIDEGEN_DATA_DIR", "./data")
        )
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
        self.recover_interrupted_jobs()
        self.cleanup_expired()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed')),
                    output_path TEXT,
                    error TEXT,
                    stage TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
            if "stage" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN stage TEXT")
            if "failure" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN failure TEXT")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS job_checkpoints ("
                "job_id TEXT NOT NULL, call_key TEXT NOT NULL, value TEXT NOT NULL, "
                "PRIMARY KEY (job_id, call_key))"
            )
            connection.execute("PRAGMA journal_mode=WAL")

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
            stage=row["stage"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            failure=JobFailure.model_validate_json(row["failure"]) if row["failure"] else None,
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
                "INSERT INTO jobs (id, prompt, status, stage, created_at, updated_at) "
                "VALUES (?, ?, 'queued', 'Waiting in queue', ?, ?)",
                (job_id, prompt, now, now),
            )
            # Resubmitting the same failed brief reuses completed calls; exact
            # request/schema hashes prevent reuse after input/schema changes.
            previous = connection.execute(
                "SELECT id FROM jobs WHERE prompt = ? AND status = 'failed' "
                "ORDER BY updated_at DESC LIMIT 1", (prompt,)
            ).fetchone()
            if previous:
                connection.execute(
                    "INSERT INTO job_checkpoints SELECT ?, call_key, value "
                    "FROM job_checkpoints WHERE job_id = ?", (job_id, previous["id"])
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

    def queue_position(self, job_id: str) -> int | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT status, created_at, id FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None or row["status"] != "queued":
                return None
            position = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'queued' AND "
                "(created_at < ? OR (created_at = ? AND id <= ?))",
                (row["created_at"], row["created_at"], row["id"]),
            ).fetchone()[0]
        return int(position)

    def mark_running(self, job_id: str) -> Job:
        return self._update(
            job_id,
            status="running",
            output_path=None,
            error=None,
            stage="Starting generation",
        )

    def mark_done(self, job_id: str, output_path: str | Path) -> Job:
        return self._update(
            job_id,
            status="done",
            output_path=str(Path(output_path).resolve()),
            error=None,
            stage=None,
        )

    def mark_failed(self, job_id: str, error: str, *, failure: JobFailure | None = None) -> Job:
        return self._update(
            job_id, status="failed", output_path=None, error=error, stage=None, failure=failure
        )

    def fail_if_running(
        self, job_id: str, error: str, *, failure: JobFailure | None = None
    ) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET status = 'failed', output_path = NULL, error = ?, "
                "stage = NULL, failure = ?, updated_at = ? WHERE id = ? AND status = 'running'",
                (
                    error,
                    failure.model_dump_json() if failure else None,
                    _timestamp(_utc_now()),
                    job_id,
                ),
            )
        return cursor.rowcount == 1

    def update_stage(self, job_id: str, stage: str) -> Job:
        return self._update(
            job_id,
            status="running",
            output_path=None,
            error=None,
            stage=stage,
        )

    def _update(
        self,
        job_id: str,
        *,
        status: str,
        output_path: str | None,
        error: str | None,
        stage: str | None,
        failure: JobFailure | None = None,
    ) -> Job:
        with self._lock, self._connect() as connection:
            current = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if current is not None and current["status"] in {"failed", "done"}:
                if status == "failed" and current["status"] == "failed":
                    return self._from_row(current)
                raise RuntimeError(f"job {job_id} is no longer running")
            cursor = connection.execute(
                "UPDATE jobs SET status = ?, output_path = ?, error = ?, stage = ?, failure = ?, updated_at = ? "
                "WHERE id = ? AND (? IS NULL OR status = ?)",
                (
                    status,
                    output_path,
                    error,
                    stage,
                    failure.model_dump_json() if failure else None,
                    _timestamp(_utc_now()),
                    job_id,
                    "running" if status == "done" else None,
                    "running" if status == "done" else None,
                ),
            )
            if cursor.rowcount == 0:
                exists = connection.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
                if exists is None:
                    raise KeyError(f"unknown job id: {job_id}")
                raise RuntimeError(f"job {job_id} is no longer running")
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._from_row(row)  # type: ignore[return-value]

    def recover_interrupted_jobs(self) -> int:
        """Requeue unfinished jobs; the runner resumes their checkpoints."""
        now = _timestamp(_utc_now())
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET status = 'queued', error = NULL, failure = NULL, "
                "output_path = NULL, stage = ?, updated_at = ? "
                "WHERE status IN ('queued', 'running')",
                (
                    "Resuming after server restart",
                    now,
                ),
            )
        return cursor.rowcount

    def queued_ids(self) -> list[str]:
        with self._lock, self._connect() as connection:
            return [row[0] for row in connection.execute(
                "SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at, id"
            )]

    def load_checkpoint(self, job_id: str, key: str) -> str | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM job_checkpoints WHERE job_id = ? AND call_key = ?",
                (job_id, key),
            ).fetchone()
        return row[0] if row else None

    def save_checkpoint(self, job_id: str, key: str, value: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO job_checkpoints (job_id, call_key, value) "
                "SELECT ?, ?, ? WHERE EXISTS "
                "(SELECT 1 FROM jobs WHERE id = ? AND status = 'running')",
                (job_id, key, value, job_id),
            )

    def delete_checkpoint(self, job_id: str, key: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM job_checkpoints WHERE job_id = ? AND call_key = ?",
                               (job_id, key))

    def recover_running_jobs(self) -> int:
        """Compatibility alias for startup recovery of all interrupted jobs."""
        return self.recover_interrupted_jobs()

    def cleanup_expired(self, *, now: datetime | None = None) -> int:
        """Remove expired job rows and any output files they reference."""
        cutoff = _timestamp((now or _utc_now()) - self.retention)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT id, output_path FROM jobs WHERE created_at < ?", (cutoff,)
            ).fetchall()
            connection.executemany("DELETE FROM jobs WHERE id = ?", [(row["id"],) for row in rows])
            connection.executemany("DELETE FROM job_checkpoints WHERE job_id = ?",
                                   [(row["id"],) for row in rows])
        for row in rows:
            output = row["output_path"]
            if output:
                candidate = Path(output).resolve()
                try:
                    candidate.relative_to(self.output_dir.resolve())
                except ValueError:
                    continue
                candidate.unlink(missing_ok=True)
            qa_folder = self.output_dir / (row["id"] + ".qa")
            resolved_qa = qa_folder.resolve()
            if (
                not qa_folder.is_symlink()
                and resolved_qa.parent == self.output_dir.resolve()
                and resolved_qa.name == row["id"] + ".qa"
                and resolved_qa.is_dir()
            ):
                shutil.rmtree(resolved_qa)
        return len(rows)
