"""
SQLite-backed application tracker: prevents duplicate applications and keeps
a durable record of everything the assistant has prepared/submitted.
"""

from __future__ import annotations

import logging
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

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

CREATE TABLE IF NOT EXISTS form_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    host TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT,
    options TEXT,
    answered_by TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (application_id, question)
);
CREATE INDEX IF NOT EXISTS idx_form_answers_host ON form_answers(host);

CREATE TABLE IF NOT EXISTS learned_form_recipes (
    host TEXT NOT NULL,
    question_key TEXT NOT NULL,
    role TEXT NOT NULL,
    options_signature TEXT NOT NULL,
    action TEXT NOT NULL,
    method TEXT NOT NULL,
    successful_uses INTEGER NOT NULL DEFAULT 1,
    failed_uses INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (host, question_key, role, options_signature, action)
);
CREATE INDEX IF NOT EXISTS idx_learned_form_recipes_host
    ON learned_form_recipes(host, updated_at DESC);
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
                ON CONFLICT(dedup_key) DO UPDATE SET
                    title = excluded.title,
                    company = excluded.company,
                    location = COALESCE(NULLIF(excluded.location, ''), applications.location),
                    updated_at = excluded.updated_at
                """,
                (
                    dedup_key, title, company, location, source_site, url,
                    STATUS_PREPARED, resume_path, cover_letter_path, notes, now, now,
                ),
            )
            row = conn.execute("SELECT id FROM applications WHERE dedup_key = ?", (dedup_key,)).fetchone()
            row_id = row["id"] if row else (cursor.lastrowid or 0)
        logger.info("Recorded application: %s @ %s (id=%s)", title, company, row_id)
        return row_id

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

    # ------------------------------------------------------------------
    # Form memory
    # ------------------------------------------------------------------
    def record_answer(
        self,
        dedup_key: str,
        host: str,
        question: str,
        answer: Optional[str],
        options: Optional[list[str]] = None,
        answered_by: str = "agent",
    ) -> None:
        """Stores an answer against one application for later owner-approved recall."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            app = conn.execute(
                "SELECT id FROM applications WHERE dedup_key = ?", (dedup_key,)
            ).fetchone()
            if not app:
                raise KeyError(f"Unknown application dedup_key: {dedup_key}")
            conn.execute(
                """
                INSERT INTO form_answers
                    (application_id, host, question, answer, options, answered_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (application_id, question) DO UPDATE
                    SET answer = excluded.answer,
                        options = excluded.options,
                        answered_by = excluded.answered_by,
                        created_at = excluded.created_at
                """,
                (
                    app["id"], host, question, answer,
                    json.dumps(options) if options is not None else None,
                    answered_by, now,
                ),
            )

    def recall_answer(self, question: str, limit: int = 5) -> list[dict[str, Any]]:
        """Returns the closest prior questions, newest first when tied.

        SQLite has no full-text index in the fallback configuration, so this
        keeps the comparison deliberately conservative and leaves the caller
        to require an exact question before replaying a value.
        """
        wanted = set(re.findall(r"[a-z0-9]+", (question or "").lower()))
        if not wanted:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT question, answer, options, host, answered_by, created_at
                   FROM form_answers
                   WHERE answer IS NOT NULL
                   ORDER BY created_at DESC"""
            ).fetchall()
        scored: list[tuple[int, dict[str, Any]]] = []
        normalised_question = " ".join(sorted(wanted))
        for row in rows:
            item = dict(row)
            candidate = set(re.findall(r"[a-z0-9]+", (item.get("question") or "").lower()))
            overlap = len(wanted & candidate)
            if not overlap:
                continue
            score = overlap
            if " ".join(sorted(candidate)) == normalised_question:
                score += 1000
            raw_options = item.get("options")
            if raw_options:
                try:
                    item["options"] = json.loads(raw_options)
                except (TypeError, json.JSONDecodeError):
                    item["options"] = None
            scored.append((score, item))
        scored.sort(key=lambda entry: (entry[0], entry[1].get("created_at") or ""), reverse=True)
        return [item for _score, item in scored[:limit]]

    def record_form_recipe(
        self,
        host: str,
        question_key: str,
        role: str,
        options_signature: str,
        action: str,
        method: str,
    ) -> None:
        """Records one read-back-verified way to operate a non-sensitive control."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO learned_form_recipes
                    (host, question_key, role, options_signature, action, method,
                     successful_uses, failed_uses, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?, ?)
                ON CONFLICT (host, question_key, role, options_signature, action) DO UPDATE
                    SET method = excluded.method,
                        successful_uses = learned_form_recipes.successful_uses + 1,
                        updated_at = excluded.updated_at
                """,
                (host, question_key, role, options_signature, action, method, now, now),
            )

    def recall_form_recipe(
        self,
        host: str,
        question_key: str,
        role: str,
        options_signature: str,
        action: str,
    ) -> Optional[dict[str, Any]]:
        """Returns a recipe only while successes outnumber later failures."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT host, question_key, role, options_signature, action, method,
                       successful_uses, failed_uses, updated_at
                FROM learned_form_recipes
                WHERE host = ? AND question_key = ? AND role = ?
                  AND options_signature = ? AND action = ?
                  AND successful_uses > failed_uses
                """,
                (host, question_key, role, options_signature, action),
            ).fetchone()
        return dict(row) if row else None

    def mark_form_recipe_failed(
        self,
        host: str,
        question_key: str,
        role: str,
        options_signature: str,
        action: str,
    ) -> None:
        """Prevents an obsolete interaction method from being blindly retried."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE learned_form_recipes
                SET failed_uses = failed_uses + 1, updated_at = ?
                WHERE host = ? AND question_key = ? AND role = ?
                  AND options_signature = ? AND action = ?
                """,
                (now, host, question_key, role, options_signature, action),
            )
