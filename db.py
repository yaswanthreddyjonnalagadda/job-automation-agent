"""
Postgres-backed storage for the job application assistant.

Holds three things:

  applications  one row per job applied to, keyed by dedup_key so the same
                posting is never applied to twice.
  documents     the actual resume/cover-letter BYTES, so a record of what was
                sent survives the files being moved or regenerated.
  form_answers  what was answered to each employer-specific question, with the
                options that employer offered. This is the reason for Postgres
                rather than SQLite: every ATS words its questions differently
                (one tenant's degree list is 'Masters', another's is 'Masters of
                Science'), so the shape is genuinely variable -- JSONB plus a
                full-text index lets the agent look up what was answered to a
                similar question before instead of asking the user again.

Connection settings come from POSTGRES_* in .env. Start the server with
`docker compose up -d`.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse
import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

import psycopg
from psycopg.types.json import Json
from psycopg.rows import dict_row

import application_status
from jd_analyzer import is_tracking_query_param, submission_identity_url

logger = logging.getLogger(__name__)


def is_transient_connection_error(exc: BaseException) -> bool:
    """Whether SQLite fallback is safe for this PostgreSQL failure."""
    if not isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)):
        return False
    message = str(exc).lower()
    return not re.search(r"authentication|password|role .* does not exist|database .* does not exist|"
                         r"schema|relation .* does not exist|permission denied|syntax error", message)

# Application lifecycle states (kept identical to the SQLite tracker so the
# rest of the codebase doesn't care which backend is in use).
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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id                BIGSERIAL PRIMARY KEY,
    dedup_key         TEXT NOT NULL UNIQUE,
    title             TEXT NOT NULL,
    company           TEXT NOT NULL,
    location          TEXT,
    source_site       TEXT,
    url               TEXT,
    status            TEXT NOT NULL DEFAULT 'prepared',
    resume_path       TEXT,
    cover_letter_path TEXT,
    notes             TEXT,
    job_description   TEXT,
    last_page_url     TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE applications ADD COLUMN IF NOT EXISTS last_page_url TEXT;
CREATE INDEX IF NOT EXISTS idx_applications_status  ON applications(status);
CREATE INDEX IF NOT EXISTS idx_applications_company ON applications(company);

CREATE TABLE IF NOT EXISTS documents (
    id             BIGSERIAL PRIMARY KEY,
    application_id BIGINT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL,
    filename       TEXT NOT NULL,
    content_type   TEXT NOT NULL,
    content        BYTEA NOT NULL,
    content_text   TEXT,
    sha256         TEXT NOT NULL,
    byte_size      INTEGER NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Re-storing identical bytes is a no-op rather than a duplicate row, so
    -- re-running a flow doesn't pile up copies of the same PDF.
    UNIQUE (application_id, kind, sha256)
);
CREATE INDEX IF NOT EXISTS idx_documents_application ON documents(application_id);

CREATE TABLE IF NOT EXISTS form_answers (
    id             BIGSERIAL PRIMARY KEY,
    application_id BIGINT REFERENCES applications(id) ON DELETE CASCADE,
    host           TEXT NOT NULL,
    question       TEXT NOT NULL,
    answer         TEXT,
    options        JSONB,
    answered_by    TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (application_id, question)
);
CREATE INDEX IF NOT EXISTS idx_form_answers_question_fts
    ON form_answers USING GIN (to_tsvector('english', question));
CREATE INDEX IF NOT EXISTS idx_form_answers_options ON form_answers USING GIN (options);
CREATE INDEX IF NOT EXISTS idx_form_answers_host ON form_answers(host);

CREATE TABLE IF NOT EXISTS learned_form_recipes (
    host              TEXT NOT NULL,
    question_key      TEXT NOT NULL,
    role              TEXT NOT NULL,
    options_signature TEXT NOT NULL,
    action            TEXT NOT NULL,
    method            TEXT NOT NULL,
    successful_uses   INTEGER NOT NULL DEFAULT 1,
    failed_uses       INTEGER NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (host, question_key, role, options_signature, action)
);
CREATE INDEX IF NOT EXISTS idx_learned_form_recipes_host
    ON learned_form_recipes(host, updated_at DESC);
"""


def _strip_tracking(url: str) -> str:
    """A posting URL without tracking parameters or a trailing slash."""
    parsed = urlparse((url or "").strip().lower())
    query = sorted((k, v) for k, v in parse_qsl(parsed.query) if not is_tracking_query_param(k))
    return urlunparse(parsed._replace(query=urlencode(query), path=parsed.path.rstrip("/"), fragment=""))


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


class PostgresTracker:
    """Same surface as the SQLite JobTracker, plus document and answer storage."""

    def __init__(self, dsn: str):
        self._dsn = dsn
        with self._connect() as conn:
            conn.execute(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection]:
        conn = psycopg.connect(self._dsn, row_factory=dict_row)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Run history: events, evidence and auto-submit audits
    # ------------------------------------------------------------------
    _EVENTS_DDL = """
        CREATE TABLE IF NOT EXISTS application_events (
            id             BIGSERIAL PRIMARY KEY,
            application_id BIGINT REFERENCES applications(id) ON DELETE CASCADE,
            kind           TEXT NOT NULL,      -- status | error | evidence | note | auto_submit
            message        TEXT,
            screenshot_path TEXT,
            html_path      TEXT,
            payload        JSONB,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_events_application ON application_events(application_id);
    """

    def record_event(self, dedup_key: str, kind: str, message: str = "",
                     screenshot_path: str = "", html_path: str = "", payload: Optional[dict] = None) -> None:
        """Appends to an application's history: a status change, an error, a
        screenshot/HTML snapshot, or an auto-submit decision. This is the audit
        trail the dashboard reads."""
        with self._connect() as conn:
            conn.execute(self._EVENTS_DDL)
            app = conn.execute("SELECT id FROM applications WHERE dedup_key = %s", (dedup_key,)).fetchone()
            if not app:
                return
            conn.execute(
                """INSERT INTO application_events
                       (application_id, kind, message, screenshot_path, html_path, payload)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (app["id"], kind, message[:4000] if message else "", screenshot_path or None,
                 html_path or None, Json(payload) if payload is not None else None),
            )

    def _local_submission_tracker(self):
        from config import DB_PATH
        from job_tracker import JobTracker
        return JobTracker(DB_PATH)

    def get_submission_effect_state(self, dedup_key: str) -> Optional[str]:
        return self._local_submission_tracker().get_submission_effect_state(dedup_key)

    def begin_submission_dispatch(self, dedup_key: str, aliases: tuple[str, ...] | list[str] = ()) -> str:
        return self._local_submission_tracker().begin_submission_dispatch(dedup_key, aliases)

    def finish_submission_effect(
        self, dedup_key: str, state: str, *, reconciled: bool = False,
        evidence_kind: Optional[str] = None,
    ) -> None:
        return self._local_submission_tracker().finish_submission_effect(
            dedup_key, state, reconciled=reconciled, evidence_kind=evidence_kind
        )

    def record_submission_safety_event(self, dedup_key: str, kind: str, payload: Optional[dict] = None) -> None:
        return self._local_submission_tracker().record_submission_safety_event(dedup_key, kind, payload)

    def submission_safety_events(self, dedup_key: str) -> list[dict]:
        return self._local_submission_tracker().submission_safety_events(dedup_key)

    def events(self, dedup_key: str, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            conn.execute(self._EVENTS_DDL)
            rows = conn.execute(
                """SELECT e.* FROM application_events e JOIN applications a ON a.id = e.application_id
                   WHERE a.dedup_key = %s ORDER BY e.created_at DESC LIMIT %s""",
                (dedup_key, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def document_matches(self, dedup_key: str, kind: str, file_path) -> bool:
        """True when the local file is byte-for-byte a document stored for this
        application. Verified auto-submit requires this before it accepts that
        the right resume/cover letter is attached."""
        path = Path(file_path)
        if not path.is_file():
            return False
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with self._connect() as conn:
            row = conn.execute(
                """SELECT 1 FROM documents d JOIN applications a ON a.id = d.application_id
                   WHERE a.dedup_key = %s AND d.kind = %s AND d.sha256 = %s LIMIT 1""",
                (dedup_key, kind, digest),
            ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # Employer accounts
    # ------------------------------------------------------------------
    # Which employers the candidate actually has a careers-site account with.
    # Signing in is only attempted where one is recorded; elsewhere the agent
    # creates the account first. Keyed by employer, not host: many employers
    # share one login host (career2.successfactors.eu, myworkdayjobs.com).
    _ACCOUNTS_DDL = """
        CREATE TABLE IF NOT EXISTS ats_accounts (
            employer      TEXT PRIMARY KEY,
            email         TEXT NOT NULL,
            login_host    TEXT,
            method        TEXT NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_login_at TIMESTAMPTZ
        )"""

    def get_ats_account(self, employer: str) -> Optional[dict]:
        with self._connect() as conn:
            conn.execute(self._ACCOUNTS_DDL)
            row = conn.execute(
                "SELECT * FROM ats_accounts WHERE employer = %s", (employer.strip().lower(),)
            ).fetchone()
        return dict(row) if row else None

    def record_ats_account(self, employer: str, email: str, login_host: str, method: str) -> None:
        with self._connect() as conn:
            conn.execute(self._ACCOUNTS_DDL)
            conn.execute(
                """
                INSERT INTO ats_accounts (employer, email, login_host, method, last_login_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (employer) DO UPDATE
                    SET last_login_at = now(), login_host = EXCLUDED.login_host
                """,
                (employer.strip().lower(), email, login_host, method),
            )
        logger.info("Recorded %s account for %s (%s)", method, employer, email)

    # ------------------------------------------------------------------
    # Applications
    # ------------------------------------------------------------------
    def is_duplicate(self, dedup_key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM applications WHERE dedup_key = %s", (dedup_key,)
            ).fetchone()
        return row is not None

    def find_submitted(self, url: str = "", company: str = "", title: str = "") -> Optional[ApplicationRecord]:
        """An already-submitted application for the same posting, or None.

        Matched on the URL with its tracking parameters stripped (the same job
        arrives as ...?utm_source=LinkedIn and bare), and on company+title, so
        a second run cannot send a duplicate application to an employer."""
        bare = _strip_tracking(url)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM applications WHERE status = %s", (STATUS_SUBMITTED,)
            ).fetchall()
        for row in rows:
            record = ApplicationRecord(**row)
            if bare and _strip_tracking(record.url or "") == bare:
                return record
            if company and title and (record.company or "").strip().lower() == company.strip().lower() \
                    and (record.title or "").strip().lower() == title.strip().lower():
                return record
        return None

    def submission_keys_for_url(self, url: str) -> list[str]:
        """Existing application keys for canonical URL-equivalent postings."""
        identity = submission_identity_url(url)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT dedup_key, url FROM applications WHERE url IS NOT NULL AND url != ''"
            ).fetchall()
        matches = []
        for row in rows:
            try:
                if submission_identity_url(row["url"]) == identity:
                    matches.append(row["dedup_key"])
            except ValueError:
                continue
        return matches

    def create(
        self,
        *,
        dedup_key: str,
        title: str,
        company: str,
        location: str = "",
        source_site: str = "",
        url: str = "",
        resume_path: Optional[str] = None,
        cover_letter_path: Optional[str] = None,
        notes: Optional[str] = None,
        job_description: Optional[str] = None,
    ) -> int:
        """Inserts, or returns the existing id if this posting is already
        recorded -- re-running a flow for the same job must not fail."""
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO applications
                    (dedup_key, title, company, location, source_site, url,
                     status, resume_path, cover_letter_path, notes, job_description)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (dedup_key) DO UPDATE SET
                    title = EXCLUDED.title,
                    company = EXCLUDED.company,
                    location = COALESCE(NULLIF(EXCLUDED.location, ''), applications.location),
                    updated_at = now()
                RETURNING id
                """,
                (
                    dedup_key, title, company, location, source_site, url,
                    STATUS_PREPARED, resume_path, cover_letter_path, notes, job_description,
                ),
            ).fetchone()
        logger.info("Recorded application: %s @ %s (id=%s)", title, company, row["id"])
        return row["id"]

    def update_materials(
        self, dedup_key: str, resume_path: Optional[str], cover_letter_path: Optional[str]
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE applications
                SET resume_path = %s, cover_letter_path = %s, updated_at = now()
                WHERE dedup_key = %s
                """,
                (resume_path, cover_letter_path, dedup_key),
            )

    def update_last_page(self, dedup_key: str, url: str) -> None:
        """Where the application actually is in the employer's site.

        Resuming from the posting meant walking the whole wizard again; this is
        the page to reopen instead -- the part-filled form itself.
        """
        if not url:
            return
        with self._connect() as conn:
            conn.execute(
                "UPDATE applications SET last_page_url = %s, updated_at = now() WHERE dedup_key = %s",
                (url, dedup_key),
            )

    def delete(self, dedup_key: str) -> bool:
        """Removes an application and everything filed under it: its documents,
        answers and event history. Used only from the dashboard, where the user
        asks for it explicitly."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM applications WHERE dedup_key = %s", (dedup_key,)
            ).fetchone()
            if not row:
                return False
            application_id = row["id"]
            # application_events keys on application_id like the rest, not on
            # dedup_key -- deleting by the wrong column failed the whole
            # request with an Internal Server Error.
            for table in ("documents", "form_answers", "application_events"):
                try:
                    conn.execute(f"DELETE FROM {table} WHERE application_id = %s", (application_id,))
                except Exception as exc:
                    # A table a given install has never created is not a reason
                    # to refuse to delete the application itself.
                    logger.debug("Nothing to remove from %s: %s", table, exc)
                    conn.rollback()
            conn.execute("DELETE FROM applications WHERE id = %s", (application_id,))
        return True

    def update_status(self, dedup_key: str, status: str, notes: Optional[str] = None,
                      by_owner: bool = False) -> None:
        current = self.get(dedup_key)
        if current is not None and not application_status.may_replace(current.status, status, by_owner):
            logger.info("KEPT: %s stays %s -- a run does not move an application that has gone back to %s",
                        dedup_key[:12], current.status, status)
            return
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE applications
                SET status = %s, notes = COALESCE(%s, notes), updated_at = now()
                WHERE dedup_key = %s
                """,
                (status, notes, dedup_key),
            )
        logger.info("Updated application %s -> status=%s", dedup_key[:12], status)

    def get(self, dedup_key: str) -> Optional[ApplicationRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM applications WHERE dedup_key = %s", (dedup_key,)
            ).fetchone()
        return ApplicationRecord(**row) if row else None

    def list_all(self, status: Optional[str] = None) -> list[ApplicationRecord]:
        query = "SELECT * FROM applications"
        params: tuple = ()
        if status:
            query += " WHERE status = %s"
            params = (status,)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [ApplicationRecord(**r) for r in rows]

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------
    def latest_document(self, dedup_key: str, kind: str) -> Optional[dict]:
        """The most recently stored document of this kind for a posting
        (filename, content bytes, content_text), or None."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT d.filename, d.content, d.content_text, d.created_at
                FROM documents d JOIN applications a ON a.id = d.application_id
                WHERE a.dedup_key = %s AND d.kind = %s
                ORDER BY d.created_at DESC
                LIMIT 1
                """,
                (dedup_key, kind),
            ).fetchone()
        return dict(row) if row else None

    def store_document(
        self,
        dedup_key: str,
        kind: str,
        file_path: Path | str,
        content_text: Optional[str] = None,
    ) -> Optional[int]:
        """Stores the file's bytes against an application. Returns the row id,
        or None if the file is missing.

        The bytes are kept, not just the path: the point of the record is to
        show what was actually sent to an employer, and paths go stale when
        output/ is regenerated or cleaned."""
        path = Path(file_path)
        if not path.is_file():
            logger.warning("Cannot store %s: %s does not exist", kind, path)
            return None

        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        content_type = {
            ".pdf": "application/pdf",
            ".txt": "text/plain",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }.get(path.suffix.lower(), "application/octet-stream")

        with self._connect() as conn:
            app = conn.execute(
                "SELECT id FROM applications WHERE dedup_key = %s", (dedup_key,)
            ).fetchone()
            if not app:
                logger.warning("Cannot store %s: no application for %s", kind, dedup_key[:12])
                return None
            row = conn.execute(
                """
                INSERT INTO documents
                    (application_id, kind, filename, content_type, content,
                     content_text, sha256, byte_size)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (application_id, kind, sha256) DO UPDATE
                    SET filename = EXCLUDED.filename
                RETURNING id
                """,
                (
                    app["id"], kind, path.name, content_type, content,
                    content_text, digest, len(content),
                ),
            ).fetchone()
        logger.info("Stored %s (%d bytes) for %s", kind, len(content), dedup_key[:12])
        return row["id"]


    def documents_for(self, application_id: int) -> list[dict]:
        """An application's stored documents, newest first (without their bytes), for the dashboard."""
        with self._connect() as conn:
            rows = conn.execute("SELECT id, kind, filename, byte_size, created_at FROM documents "
                                "WHERE application_id = %s ORDER BY created_at DESC", (application_id,)).fetchall()
        return [dict(r) for r in rows]

    def document(self, document_id: int) -> Optional[dict]:
        """One stored document: filename, content_type and its bytes."""
        with self._connect() as conn:
            row = conn.execute("SELECT filename, content_type, content FROM documents WHERE id = %s",
                               (document_id,)).fetchone()
        return dict(row) if row else None

    def answers_for(self, application_id: int) -> list[dict]:
        """What was answered on an application's forms, by question, for the dashboard."""
        with self._connect() as conn:
            rows = conn.execute("SELECT question, answer, host, answered_by FROM form_answers "
                                "WHERE application_id = %s ORDER BY question", (application_id,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Form answers -- the reusable memory of what was answered where
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
        with self._connect() as conn:
            app = conn.execute(
                "SELECT id FROM applications WHERE dedup_key = %s", (dedup_key,)
            ).fetchone()
            if not app:
                raise KeyError(f"Unknown application dedup_key: {dedup_key}")
            conn.execute(
                """
                INSERT INTO form_answers
                    (application_id, host, question, answer, options, answered_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (application_id, question) DO UPDATE
                    SET answer = EXCLUDED.answer,
                        options = EXCLUDED.options,
                        answered_by = EXCLUDED.answered_by
                """,
                (
                    app["id"], host, question, answer,
                    json.dumps(options) if options is not None else None,
                    answered_by,
                ),
            )

    def recall_answer(self, question: str, limit: int = 5) -> list[dict[str, Any]]:
        """Finds answers previously given to similar questions, most recent
        first. Employers word the same question differently, so this matches on
        full-text rank rather than exact string equality -- it's what lets the
        agent reuse an answer instead of asking the user again."""
        # OR the probe's lexemes rather than using plainto_tsquery, which ANDs
        # them: 'How much are you willing to travel?' shares only 'willing' and
        # 'travel' with 'Are you willing and able to travel for this position?',
        # so requiring every word finds nothing. ts_rank then puts the best
        # overlap first, which is the behaviour that makes recall useful.
        with self._connect() as conn:
            rows = conn.execute(
                """
                WITH q AS (
                    SELECT NULLIF(
                        array_to_string(
                            tsvector_to_array(to_tsvector('english', %s)), ' | '
                        ), ''
                    )::tsquery AS query
                )
                SELECT f.question, f.answer, f.options, f.host, f.answered_by,
                       f.created_at,
                       ts_rank(to_tsvector('english', f.question), q.query) AS rank
                FROM form_answers f, q
                WHERE f.answer IS NOT NULL
                  AND q.query IS NOT NULL
                  AND to_tsvector('english', f.question) @@ q.query
                ORDER BY rank DESC, f.created_at DESC
                LIMIT %s
                """,
                (question, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Read-back-verified form interaction recipes
    # ------------------------------------------------------------------
    def record_form_recipe(
        self,
        host: str,
        question_key: str,
        role: str,
        options_signature: str,
        action: str,
        method: str,
    ) -> None:
        """Records a proven control interaction without storing its answer."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO learned_form_recipes
                    (host, question_key, role, options_signature, action, method)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (host, question_key, role, options_signature, action) DO UPDATE
                    SET method = EXCLUDED.method,
                        successful_uses = learned_form_recipes.successful_uses + 1,
                        updated_at = now()
                """,
                (host, question_key, role, options_signature, action, method),
            )

    def recall_form_recipe(
        self,
        host: str,
        question_key: str,
        role: str,
        options_signature: str,
        action: str,
    ) -> Optional[dict[str, Any]]:
        """Returns only a recipe with more verified successes than failures."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT host, question_key, role, options_signature, action, method,
                       successful_uses, failed_uses, updated_at
                FROM learned_form_recipes
                WHERE host = %s AND question_key = %s AND role = %s
                  AND options_signature = %s AND action = %s
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
        """Disables an obsolete recipe after it no longer produces the value."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE learned_form_recipes
                SET failed_uses = failed_uses + 1, updated_at = now()
                WHERE host = %s AND question_key = %s AND role = %s
                  AND options_signature = %s AND action = %s
                """,
                (host, question_key, role, options_signature, action),
            )


def dsn_from_env() -> str:
    """Builds the connection string from POSTGRES_* in .env."""
    import os

    from dotenv import load_dotenv

    load_dotenv()
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = os.getenv("POSTGRES_PORT", "5432")
    name = os.getenv("POSTGRES_DB", "job_agent")
    user = os.getenv("POSTGRES_USER", "job_agent")
    password = os.getenv("POSTGRES_PASSWORD", "")
    if not password:
        raise RuntimeError(
            "POSTGRES_PASSWORD is not set in .env -- run the setup or start the "
            "container with `docker compose up -d`."
        )
    return "postgresql://{}:{}@{}:{}/{}?connect_timeout=2".format(
        quote(user, safe=""), quote(password, safe=""), host, port, quote(name, safe="")
    )


def get_tracker() -> PostgresTracker:
    return PostgresTracker(dsn_from_env())
