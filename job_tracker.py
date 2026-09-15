"""
SQLite-backed application tracker: prevents duplicate applications and keeps
a durable record of everything the assistant has prepared/submitted.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT,
    source_site TEXT,
    url TEXT,
    status TEXT NOT NULL DEFAULT 'prepared',
    resume_path TEXT,
    cover_letter_path TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
"""

# Valid application lifecycle states.
STATUS_PREPARED = "prepared"                    # materials generated
STATUS_FORM_FILLED = "form_filled"              # being filled in
STATUS_READY_TO_SUBMIT = "ready_to_submit"      # filled + validated; the human clicks Submit
STATUS_NEEDS_USER_REVIEW = "needs_user_review"  # something needs a person: blanks, errors,
                                                # a CAPTCHA, an attestation, or a submission
                                                # we could not verify
STATUS_SUBMITTED = "submitted"                  # confirmed by the site or a confirmation email
STATUS_SKIPPED = "skipped"


@dataclass
class ApplicationRecord:
    id: int
    dedup_key: str
    title: str
    company: str
    location: str
    source_site: str
    url: str
    status: str
    resume_path: Optional[str]
    cover_letter_path: Optional[str]
    notes: Optional[str]
    created_at: str
    updated_at: str


class JobTracker:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def is_duplicate(self, dedup_key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM applications WHERE dedup_key = ?", (dedup_key,)
            ).fetchone()
        return row is not None

    def create(
        self,
        *,
        dedup_key: str,
        title: str,
        company: str,
        location: str,
        source_site: str,
        url: str,
        resume_path: Optional[str] = None,
        cover_letter_path: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO applications
                    (dedup_key, title, company, location, source_site, url,
                     status, resume_path, cover_letter_path, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dedup_key, title, company, location, source_site, url,
                    STATUS_PREPARED, resume_path, cover_letter_path, notes, now, now,
                ),
            )
        logger.info("Recorded new application: %s @ %s", title, company)
        return cursor.lastrowid

    def update_materials(
        self, dedup_key: str, resume_path: Optional[str], cover_letter_path: Optional[str]
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE applications
                SET resume_path = ?, cover_letter_path = ?, updated_at = ?
                WHERE dedup_key = ?
                """,
                (resume_path, cover_letter_path, now, dedup_key),
            )

    def update_status(self, dedup_key: str, status: str, notes: Optional[str] = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE applications
                SET status = ?, notes = COALESCE(?, notes), updated_at = ?
                WHERE dedup_key = ?
                """,
                (status, notes, now, dedup_key),
            )
        logger.info("Updated application %s -> status=%s", dedup_key[:12], status)

    def get(self, dedup_key: str) -> Optional[ApplicationRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM applications WHERE dedup_key = ?", (dedup_key,)
            ).fetchone()
        return ApplicationRecord(**dict(row)) if row else None

    def list_all(self, status: Optional[str] = None) -> list[ApplicationRecord]:
        query = "SELECT * FROM applications"
        params: tuple = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [ApplicationRecord(**dict(r)) for r in rows]
