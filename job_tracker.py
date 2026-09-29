"""
SQLite-backed application tracker: everything the Postgres tracker keeps, in one file, with no database server.

The agent is for anyone who runs it on their own computer (the owner's decision of 29 September 2026), and needing
a Postgres server (Docker) before the first application was a wall. This tracker has the Postgres tracker's whole
surface -- applications, their documents (the bytes), the run history, employer accounts, answers and learned form
recipes -- so the dashboard and the agent work the same on either. tracking.open_tracker() picks the one to use.

An older applications.db (from when SQLite was only a stand-in during a Postgres outage) is brought up to date in
place: missing columns and tables are added, nothing is removed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

logger = logging.getLogger(__name__)

_SCHEMA = """
PRAGMA foreign_keys = ON;
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

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    content BLOB NOT NULL,
    content_text TEXT,
    sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (application_id, kind, sha256)
);
CREATE INDEX IF NOT EXISTS idx_documents_application ON documents(application_id);

CREATE TABLE IF NOT EXISTS application_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER REFERENCES applications(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    message TEXT,
    screenshot_path TEXT,
    html_path TEXT,
    payload TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_application ON application_events(application_id);

CREATE TABLE IF NOT EXISTS ats_accounts (
    employer TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    login_host TEXT,
    method TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

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

# Columns added since the first version of this file; an older applications.db gets them on open.
_ADDED_COLUMNS = {"applications": (("job_description", "TEXT"), ("last_page_url", "TEXT"))}

# Valid application lifecycle states (the same as the Postgres tracker's).
STATUS_PREPARED = "prepared"                    # materials generated
STATUS_FORM_FILLED = "form_filled"              # being filled in
STATUS_READY_TO_SUBMIT = "ready_to_submit"      # filled + validated; the human clicks Submit
STATUS_NEEDS_USER_REVIEW = "needs_user_review"  # something needs a person: blanks, errors,
                                                # a CAPTCHA, an attestation, or a submission
                                                # we could not verify
STATUS_SUBMITTED = "submitted"                  # confirmed by the site or a confirmation email
STATUS_SKIPPED = "skipped"
STATUS_DISQUALIFIED_POLICY_MISMATCH = "DISQUALIFIED_POLICY_MISMATCH"
STATUS_BLOCKED_VALIDATION_LOOP = "BLOCKED_VALIDATION_LOOP"


@dataclass
class ApplicationRecord:
    id: int
    dedup_key: str
    title: str
    company: str
    location: Optional[str]
    source_site: Optional[str]
    url: Optional[str]
    status: str
    resume_path: Optional[str]
    cover_letter_path: Optional[str]
    notes: Optional[str]
    job_description: Optional[str]
    last_page_url: Optional[str]
    created_at: datetime
    updated_at: datetime


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _when(text: Optional[str]) -> Optional[datetime]:
    """A stored time as the datetime the dashboard formats (Postgres gives datetimes)."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text))
    except ValueError:
        return None


def _strip_tracking(url: str) -> str:
    """A posting URL without tracking parameters or a trailing slash (as the Postgres tracker compares it)."""
    parsed = urlparse((url or "").strip().lower())
    query = [(k, v) for k, v in parse_qsl(parsed.query) if not k.startswith(("utm_", "gh_src"))
             and k not in {"src", "source", "ref", "referrer", "trackingid"}]
    return urlunparse(parsed._replace(query=urlencode(query), path=parsed.path.rstrip("/"), fragment=""))


class JobTracker:
    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            for table, columns in _ADDED_COLUMNS.items():
                have = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                for name, kind in columns:
                    if name not in have:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self._db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _app_id(self, conn: sqlite3.Connection, dedup_key: str) -> Optional[int]:
        row = conn.execute("SELECT id FROM applications WHERE dedup_key = ?", (dedup_key,)).fetchone()
        return row["id"] if row else None

    @staticmethod
    def _record(row: sqlite3.Row) -> ApplicationRecord:
        data = dict(row)
        data.setdefault("job_description", None)
        data.setdefault("last_page_url", None)
        data["created_at"], data["updated_at"] = _when(data["created_at"]), _when(data["updated_at"])
        return ApplicationRecord(**{k: data.get(k) for k in ApplicationRecord.__dataclass_fields__})

    # ------------------------------------------------------------------
    # Run history
    # ------------------------------------------------------------------
    def record_event(self, dedup_key: str, kind: str, message: str = "",
                     screenshot_path: str = "", html_path: str = "", payload: Optional[dict] = None) -> None:
        with self._connect() as conn:
            app = self._app_id(conn, dedup_key)
            if app is None:
                return
            conn.execute(
                """INSERT INTO application_events
                       (application_id, kind, message, screenshot_path, html_path, payload, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (app, kind, message[:4000] if message else "", screenshot_path or None, html_path or None,
                 json.dumps(payload) if payload is not None else None, _now()))

    def events(self, dedup_key: str, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT e.* FROM application_events e JOIN applications a ON a.id = e.application_id
                   WHERE a.dedup_key = ? ORDER BY e.created_at DESC, e.id DESC LIMIT ?""",
                (dedup_key, limit)).fetchall()
        found = []
        for row in rows:
            item = dict(row)
            item["created_at"] = _when(item.get("created_at"))
            if item.get("payload"):
                try:
                    item["payload"] = json.loads(item["payload"])
                except (TypeError, ValueError):
                    pass
            found.append(item)
        return found

    def document_matches(self, dedup_key: str, kind: str, file_path) -> bool:
        path = Path(file_path)
        if not path.is_file():
            return False
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with self._connect() as conn:
            row = conn.execute(
                """SELECT 1 FROM documents d JOIN applications a ON a.id = d.application_id
                   WHERE a.dedup_key = ? AND d.kind = ? AND d.sha256 = ? LIMIT 1""",
                (dedup_key, kind, digest)).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # Employer accounts
    # ------------------------------------------------------------------
    def get_ats_account(self, employer: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM ats_accounts WHERE employer = ?",
                               (employer.strip().lower(),)).fetchone()
        return dict(row) if row else None

    def record_ats_account(self, employer: str, email: str, login_host: str, method: str) -> None:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO ats_accounts (employer, email, login_host, method, created_at, last_login_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (employer) DO UPDATE SET last_login_at = excluded.last_login_at,
                                                        login_host = excluded.login_host""",
                (employer.strip().lower(), email, login_host, method, now, now))
        logger.info("Recorded %s account for %s (%s)", method, employer, email)

    # ------------------------------------------------------------------
    # Applications
    # ------------------------------------------------------------------
    def is_duplicate(self, dedup_key: str) -> bool:
        with self._connect() as conn:
            return self._app_id(conn, dedup_key) is not None

    def find_submitted(self, url: str = "", company: str = "", title: str = "") -> Optional[ApplicationRecord]:
        """An already-submitted application for the same posting (URL without tracking, or company+title)."""
        bare = _strip_tracking(url)
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM applications WHERE status = ?", (STATUS_SUBMITTED,)).fetchall()
        for row in rows:
            record = self._record(row)
            if bare and _strip_tracking(record.url or "") == bare:
                return record
            if company and title and (record.company or "").strip().lower() == company.strip().lower() \
                    and (record.title or "").strip().lower() == title.strip().lower():
                return record
        return None

    def create(self, *, dedup_key: str, title: str, company: str, location: str = "", source_site: str = "",
               url: str = "", resume_path: Optional[str] = None, cover_letter_path: Optional[str] = None,
               notes: Optional[str] = None, job_description: Optional[str] = None) -> int:
        """Inserts, or updates the title/company of the existing record for this posting and returns its id."""
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO applications
                       (dedup_key, title, company, location, source_site, url, status, resume_path,
                        cover_letter_path, notes, job_description, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(dedup_key) DO UPDATE SET
                       title = excluded.title,
                       company = excluded.company,
                       location = COALESCE(NULLIF(excluded.location, ''), applications.location),
                       updated_at = excluded.updated_at""",
                (dedup_key, title, company, location, source_site, url, STATUS_PREPARED, resume_path,
                 cover_letter_path, notes, job_description, now, now))
            row_id = self._app_id(conn, dedup_key) or 0
        logger.info("Recorded application: %s @ %s (id=%s)", title, company, row_id)
        return row_id

    def update_materials(self, dedup_key: str, resume_path: Optional[str], cover_letter_path: Optional[str]) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE applications SET resume_path = ?, cover_letter_path = ?, updated_at = ? "
                         "WHERE dedup_key = ?", (resume_path, cover_letter_path, _now(), dedup_key))

    def update_last_page(self, dedup_key: str, url: str) -> None:
        if not url:
            return
        with self._connect() as conn:
            conn.execute("UPDATE applications SET last_page_url = ?, updated_at = ? WHERE dedup_key = ?",
                         (url, _now(), dedup_key))

    def delete(self, dedup_key: str) -> bool:
        """Removes an application and everything filed under it (asked for on the dashboard)."""
        with self._connect() as conn:
            app = self._app_id(conn, dedup_key)
            if app is None:
                return False
            for table in ("documents", "form_answers", "application_events"):
                conn.execute(f"DELETE FROM {table} WHERE application_id = ?", (app,))
            conn.execute("DELETE FROM applications WHERE id = ?", (app,))
        return True

    def update_status(self, dedup_key: str, status: str, notes: Optional[str] = None) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE applications SET status = ?, notes = COALESCE(?, notes), updated_at = ? "
                         "WHERE dedup_key = ?", (status, notes, _now(), dedup_key))
        logger.info("Updated application %s -> status=%s", dedup_key[:12], status)

    def get(self, dedup_key: str) -> Optional[ApplicationRecord]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM applications WHERE dedup_key = ?", (dedup_key,)).fetchone()
        return self._record(row) if row else None

    def list_all(self, status: Optional[str] = None) -> list[ApplicationRecord]:
        query, params = "SELECT * FROM applications", ()
        if status:
            query, params = query + " WHERE status = ?", (status,)
        with self._connect() as conn:
            rows = conn.execute(query + " ORDER BY created_at DESC, id DESC", params).fetchall()
        return [self._record(r) for r in rows]

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------
    def latest_document(self, dedup_key: str, kind: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT d.filename, d.content, d.content_text, d.created_at
                   FROM documents d JOIN applications a ON a.id = d.application_id
                   WHERE a.dedup_key = ? AND d.kind = ? ORDER BY d.created_at DESC, d.id DESC LIMIT 1""",
                (dedup_key, kind)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["content"] = bytes(item["content"]) if item.get("content") is not None else b""
        item["created_at"] = _when(item.get("created_at"))
        return item

    def store_document(self, dedup_key: str, kind: str, file_path: Path | str,
                       content_text: Optional[str] = None) -> Optional[int]:
        """Stores the file's bytes against an application: what was actually sent survives the file moving."""
        path = Path(file_path)
        if not path.is_file():
            logger.warning("Cannot store %s: %s does not exist", kind, path)
            return None
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        content_type = {".pdf": "application/pdf", ".txt": "text/plain",
                        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        }.get(path.suffix.lower(), "application/octet-stream")
        with self._connect() as conn:
            app = self._app_id(conn, dedup_key)
            if app is None:
                logger.warning("Cannot store %s: no application for %s", kind, dedup_key[:12])
                return None
            conn.execute(
                """INSERT INTO documents (application_id, kind, filename, content_type, content, content_text,
                                          sha256, byte_size, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (application_id, kind, sha256) DO UPDATE SET filename = excluded.filename""",
                (app, kind, path.name, content_type, content, content_text, digest, len(content), _now()))
            row = conn.execute("SELECT id FROM documents WHERE application_id = ? AND kind = ? AND sha256 = ?",
                               (app, kind, digest)).fetchone()
        logger.info("Stored %s (%d bytes) for %s", kind, len(content), dedup_key[:12])
        return row["id"] if row else None

    def documents_for(self, application_id: int) -> list[dict]:
        """An application's stored documents, newest first (without their bytes), for the dashboard."""
        with self._connect() as conn:
            rows = conn.execute("SELECT id, kind, filename, byte_size, created_at FROM documents "
                                "WHERE application_id = ? ORDER BY created_at DESC, id DESC",
                                (application_id,)).fetchall()
        return [{**dict(r), "created_at": _when(r["created_at"])} for r in rows]

    def document(self, document_id: int) -> Optional[dict]:
        """One stored document: filename, content_type and its bytes."""
        with self._connect() as conn:
            row = conn.execute("SELECT filename, content_type, content FROM documents WHERE id = ?",
                               (document_id,)).fetchone()
        return {**dict(row), "content": bytes(row["content"])} if row else None

    def answers_for(self, application_id: int) -> list[dict]:
        """What was answered on an application's forms, by question, for the dashboard."""
        with self._connect() as conn:
            rows = conn.execute("SELECT question, answer, host, answered_by FROM form_answers "
                                "WHERE application_id = ? ORDER BY question", (application_id,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Form memory
    # ------------------------------------------------------------------
    def record_answer(self, dedup_key: str, host: str, question: str, answer: Optional[str],
                      options: Optional[list[str]] = None, answered_by: str = "agent") -> None:
        """Stores an answer against one application for later owner-approved recall."""
        with self._connect() as conn:
            app = self._app_id(conn, dedup_key)
            if app is None:
                raise KeyError(f"Unknown application dedup_key: {dedup_key}")
            conn.execute(
                """INSERT INTO form_answers (application_id, host, question, answer, options, answered_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (application_id, question) DO UPDATE
                       SET answer = excluded.answer, options = excluded.options,
                           answered_by = excluded.answered_by, created_at = excluded.created_at""",
                (app, host, question, answer, json.dumps(options) if options is not None else None,
                 answered_by, _now()))

    def recall_answer(self, question: str, limit: int = 5) -> list[dict[str, Any]]:
        """The closest prior questions by shared words, newest first when tied (Postgres ranks by full text;
        callers require the same question before replaying a value either way)."""
        wanted = set(re.findall(r"[a-z0-9]+", (question or "").lower()))
        if not wanted:
            return []
        with self._connect() as conn:
            rows = conn.execute("""SELECT question, answer, options, host, answered_by, created_at
                                   FROM form_answers WHERE answer IS NOT NULL ORDER BY created_at DESC""").fetchall()
        scored: list[tuple[int, dict[str, Any]]] = []
        normalised_question = " ".join(sorted(wanted))
        for row in rows:
            item = dict(row)
            candidate = set(re.findall(r"[a-z0-9]+", (item.get("question") or "").lower()))
            overlap = len(wanted & candidate)
            if not overlap:
                continue
            score = overlap + (1000 if " ".join(sorted(candidate)) == normalised_question else 0)
            if item.get("options"):
                try:
                    item["options"] = json.loads(item["options"])
                except (TypeError, json.JSONDecodeError):
                    item["options"] = None
            scored.append((score, item))
        scored.sort(key=lambda entry: (entry[0], entry[1].get("created_at") or ""), reverse=True)
        return [item for _score, item in scored[:limit]]

    def record_form_recipe(self, host: str, question_key: str, role: str, options_signature: str,
                           action: str, method: str) -> None:
        """Records one read-back-verified way to operate a non-sensitive control."""
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO learned_form_recipes
                       (host, question_key, role, options_signature, action, method,
                        successful_uses, failed_uses, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?, ?)
                   ON CONFLICT (host, question_key, role, options_signature, action) DO UPDATE
                       SET method = excluded.method,
                           successful_uses = learned_form_recipes.successful_uses + 1,
                           updated_at = excluded.updated_at""",
                (host, question_key, role, options_signature, action, method, now, now))

    def recall_form_recipe(self, host: str, question_key: str, role: str, options_signature: str,
                           action: str) -> Optional[dict[str, Any]]:
        """Returns a recipe only while successes outnumber later failures."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT host, question_key, role, options_signature, action, method,
                          successful_uses, failed_uses, updated_at
                   FROM learned_form_recipes
                   WHERE host = ? AND question_key = ? AND role = ? AND options_signature = ? AND action = ?
                     AND successful_uses > failed_uses""",
                (host, question_key, role, options_signature, action)).fetchone()
        return dict(row) if row else None

    def mark_form_recipe_failed(self, host: str, question_key: str, role: str, options_signature: str,
                                action: str) -> None:
        """Prevents an obsolete interaction method from being blindly retried."""
        with self._connect() as conn:
            conn.execute(
                """UPDATE learned_form_recipes SET failed_uses = failed_uses + 1, updated_at = ?
                   WHERE host = ? AND question_key = ? AND role = ? AND options_signature = ? AND action = ?""",
                (_now(), host, question_key, role, options_signature, action))
