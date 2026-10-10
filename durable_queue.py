"""Durable SQLite-backed job queue with lease safety and B1/B3 invariants.

Preserves submission authority and recovery invariants across remote execution:
worker loss or lease expiration NEVER causes blind re-execution of consequential
actions from scratch. Instead, in-flight jobs transition to RECOVERY_REQUIRED.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class JobState(str, Enum):
    QUEUED = "QUEUED"
    LEASED = "LEASED"
    RUNNING = "RUNNING"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    PAUSED = "PAUSED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class QueuedJob:
    job_id: str
    job_url: str
    application_key: str
    priority: int = 0
    job_type: str = "application"
    status: JobState = JobState.QUEUED
    created_at: float = field(default_factory=time.time)
    assigned_worker: Optional[str] = None
    lease_started_at: Optional[float] = None
    lease_expires_at: Optional[float] = None
    attempt: int = 1
    checkpoint_reference: Optional[str] = None
    last_update: float = field(default_factory=time.time)
    notes: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_url": self.job_url,
            "application_key": self.application_key,
            "priority": self.priority,
            "job_type": self.job_type,
            "status": self.status.value if isinstance(self.status, Enum) else self.status,
            "created_at": self.created_at,
            "assigned_worker": self.assigned_worker,
            "lease_started_at": self.lease_started_at,
            "lease_expires_at": self.lease_expires_at,
            "attempt": self.attempt,
            "checkpoint_reference": self.checkpoint_reference,
            "last_update": self.last_update,
            "notes": self.notes,
        }


class DurableJobQueue:
    """SQLite-backed persistent job queue with lease-based assignment."""

    def __init__(self, db_path: Optional[Path] = None):
        data_dir = (Path(__file__).resolve().parent / "data")
        self.db_path = db_path or (data_dir / "job_queue.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS job_queue (
                    job_id TEXT PRIMARY KEY,
                    job_url TEXT NOT NULL,
                    application_key TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    job_type TEXT NOT NULL DEFAULT 'application',
                    status TEXT NOT NULL DEFAULT 'QUEUED',
                    created_at REAL NOT NULL,
                    assigned_worker TEXT,
                    lease_started_at REAL,
                    lease_expires_at REAL,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    checkpoint_reference TEXT,
                    last_update REAL NOT NULL,
                    notes TEXT NOT NULL DEFAULT ''
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_job_status ON job_queue(status, priority, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_job_lease ON job_queue(assigned_worker, lease_expires_at)")

    def enqueue(
        self,
        job_url: str,
        application_key: Optional[str] = None,
        priority: int = 0,
        job_type: str = "application",
        notes: str = "",
    ) -> QueuedJob:
        """Enqueues a new job into durable storage."""
        jid = f"job_{uuid.uuid4().hex[:12]}"
        now = time.time()
        app_key = application_key or job_url

        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO job_queue (
                    job_id, job_url, application_key, priority, job_type,
                    status, created_at, assigned_worker, lease_started_at,
                    lease_expires_at, attempt, checkpoint_reference, last_update, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, 1, NULL, ?, ?)
                """,
                (jid, job_url, app_key, priority, job_type, JobState.QUEUED.value, now, now, notes),
            )

        return QueuedJob(
            job_id=jid,
            job_url=job_url,
            application_key=app_key,
            priority=priority,
            job_type=job_type,
            status=JobState.QUEUED,
            created_at=now,
            last_update=now,
            notes=notes,
        )

    def claim_next_lease(
        self,
        worker_id: str,
        lease_duration_seconds: float = 300.0,
    ) -> Optional[QueuedJob]:
        """Atomically claims the highest priority QUEUED job for the given worker."""
        now = time.time()
        expires = now + lease_duration_seconds

        with self._lock, self._get_connection() as conn:
            # Reconcile expired leases before claiming
            self._reconcile_expired_leases_locked(conn, now)

            row = conn.execute(
                """
                SELECT * FROM job_queue
                WHERE status = ?
                ORDER BY priority ASC, created_at ASC
                LIMIT 1
                """,
                (JobState.QUEUED.value,),
            ).fetchone()

            if not row:
                return None

            job_id = row["job_id"]
            conn.execute(
                """
                UPDATE job_queue
                SET status = ?, assigned_worker = ?, lease_started_at = ?, lease_expires_at = ?, last_update = ?
                WHERE job_id = ? AND status = ?
                """,
                (JobState.LEASED.value, worker_id, now, expires, now, job_id, JobState.QUEUED.value),
            )

            updated = conn.execute("SELECT * FROM job_queue WHERE job_id = ?", (job_id,)).fetchone()
            if not updated:
                return None

            return self._row_to_job(updated)

    def renew_lease(
        self,
        job_id: str,
        worker_id: str,
        extension_seconds: float = 300.0,
    ) -> bool:
        """Renews an active lease for an in-flight job."""
        now = time.time()
        new_expires = now + extension_seconds
        with self._lock, self._get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE job_queue
                SET lease_expires_at = ?, last_update = ?
                WHERE job_id = ? AND assigned_worker = ? AND status IN (?, ?)
                """,
                (new_expires, now, job_id, worker_id, JobState.LEASED.value, JobState.RUNNING.value),
            )
            return cur.rowcount > 0

    def update_status(
        self,
        job_id: str,
        worker_id: Optional[str] = None,
        new_status: JobState | str = JobState.RUNNING,
        notes: str = "",
        checkpoint_reference: Optional[str] = None,
    ) -> bool:
        """Updates the state of a leased or managed job."""
        now = time.time()
        st_val = new_status.value if isinstance(new_status, JobState) else str(new_status)

        with self._lock, self._get_connection() as conn:
            query = """
                UPDATE job_queue
                SET status = ?, last_update = ?, notes = ?
            """
            params: list = [st_val, now, notes]

            if checkpoint_reference is not None:
                query += ", checkpoint_reference = ?"
                params.append(checkpoint_reference)

            if st_val in (JobState.COMPLETED.value, JobState.FAILED.value, JobState.CANCELLED.value, JobState.QUEUED.value):
                query += ", lease_expires_at = NULL"
                if st_val == JobState.QUEUED.value:
                    query += ", assigned_worker = NULL, lease_started_at = NULL"

            query += " WHERE job_id = ?"
            params.append(job_id)
            if worker_id is not None:
                query += " AND assigned_worker = ?"
                params.append(worker_id)

            cur = conn.execute(query, params)
            return cur.rowcount > 0

    def release_lease(self, job_id: str, worker_id: str) -> bool:
        """Releases a lease back to QUEUED."""
        now = time.time()
        with self._lock, self._get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE job_queue
                SET status = ?, assigned_worker = NULL, lease_started_at = NULL, lease_expires_at = NULL, last_update = ?
                WHERE job_id = ? AND assigned_worker = ? AND status IN (?, ?)
                """,
                (JobState.QUEUED.value, now, job_id, worker_id, JobState.LEASED.value, JobState.RUNNING.value),
            )
            return cur.rowcount > 0

    def reconcile_expired_leases(self) -> int:
        """Finds expired worker leases and transitions them to RECOVERY_REQUIRED.
        
        B1/B3 invariant: Never rerun consequential application from start on lease expiry.
        """
        now = time.time()
        with self._lock, self._get_connection() as conn:
            return self._reconcile_expired_leases_locked(conn, now)

    def _reconcile_expired_leases_locked(self, conn: sqlite3.Connection, now: float) -> int:
        cur = conn.execute(
            """
            UPDATE job_queue
            SET status = ?, last_update = ?, notes = 'Worker lease expired; requires state reconciliation before resume'
            WHERE lease_expires_at IS NOT NULL AND lease_expires_at < ? AND status IN (?, ?)
            """,
            (JobState.RECOVERY_REQUIRED.value, now, now, JobState.LEASED.value, JobState.RUNNING.value),
        )
        return cur.rowcount

    def get_job(self, job_id: str) -> Optional[QueuedJob]:
        with self._lock, self._get_connection() as conn:
            row = conn.execute("SELECT * FROM job_queue WHERE job_id = ?", (job_id,)).fetchone()
            if row:
                return self._row_to_job(row)
        return None

    def list_jobs(
        self,
        status: Optional[JobState | str] = None,
        limit: int = 50,
    ) -> List[QueuedJob]:
        with self._lock, self._get_connection() as conn:
            if status:
                st_val = status.value if isinstance(status, JobState) else str(status)
                rows = conn.execute(
                    "SELECT * FROM job_queue WHERE status = ? ORDER BY priority ASC, created_at DESC LIMIT ?",
                    (st_val, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM job_queue ORDER BY priority ASC, created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [self._row_to_job(r) for r in rows]

    @staticmethod
    def _row_to_job(r: sqlite3.Row) -> QueuedJob:
        return QueuedJob(
            job_id=r["job_id"],
            job_url=r["job_url"],
            application_key=r["application_key"],
            priority=r["priority"],
            job_type=r["job_type"],
            status=JobState(r["status"]),
            created_at=r["created_at"],
            assigned_worker=r["assigned_worker"],
            lease_started_at=r["lease_started_at"],
            lease_expires_at=r["lease_expires_at"],
            attempt=r["attempt"],
            checkpoint_reference=r["checkpoint_reference"],
            last_update=r["last_update"],
            notes=r["notes"],
        )

