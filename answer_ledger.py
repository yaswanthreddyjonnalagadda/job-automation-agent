"""The answer transparency ledger: every answer the agent gave on an application, where it came from, and the
owner's overrides from the cockpit. A small SQLite table in data/applications.db.

Moved out of web_ui.py (code-quality audit 26 October 2026). web_ui re-exports the functions and passes its own
tracker to `update_answer`, so the dashboard behaves as before."""
from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent / "data" / "applications.db"
_LOCK = threading.RLock()


def init_answer_ledger() -> None:
    """Initializes the answer transparency ledger SQLite schema."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS application_answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_key TEXT NOT NULL,
                question_text TEXT NOT NULL,
                final_answer TEXT NOT NULL,
                source TEXT NOT NULL,
                fact_reference TEXT DEFAULT '',
                timestamp TEXT NOT NULL,
                verification_result TEXT DEFAULT '',
                previous_value TEXT DEFAULT '',
                correction_reason TEXT DEFAULT '',
                status TEXT DEFAULT 'COMMITTED'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_answers_app ON application_answers(application_key)")
        conn.commit()


def record_answer_ledger(
    application_key: str,
    question_text: str,
    final_answer: str,
    source: str = "PROFILE",
    fact_reference: str = "",
    verification_result: str = "ACCEPTED",
    previous_value: str = "",
    correction_reason: str = "",
    status: str = "COMMITTED",
) -> int:
    """Records or updates an answer entry in the transparency ledger."""
    init_answer_ledger()
    now_iso = datetime.now(timezone.utc).isoformat()
    with _LOCK, sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, final_answer FROM application_answers WHERE application_key = ? AND question_text = ? ORDER BY id DESC LIMIT 1",
            (application_key, question_text)
        )
        row = cursor.fetchone()
        if row:
            row_id, old_val = row
            if previous_value or (old_val != final_answer):
                prev = previous_value or old_val
                cursor.execute("""
                    UPDATE application_answers
                    SET final_answer = ?, source = ?, fact_reference = ?, timestamp = ?,
                        verification_result = ?, previous_value = ?, correction_reason = ?, status = ?
                    WHERE id = ?
                """, (final_answer, source, fact_reference, now_iso, verification_result, prev, correction_reason, status, row_id))
                conn.commit()
                return row_id
            return row_id

        cursor.execute("""
            INSERT INTO application_answers
            (application_key, question_text, final_answer, source, fact_reference, timestamp, verification_result, previous_value, correction_reason, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (application_key, question_text, final_answer, source, fact_reference, now_iso, verification_result, previous_value, correction_reason, status))
        conn.commit()
        return cursor.lastrowid or 0


def get_answers_for_application(application_key: str) -> list[dict]:
    """Retrieves all transparency ledger answers for a given application key."""
    init_answer_ledger()
    with _LOCK, sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM application_answers WHERE application_key = ? ORDER BY id ASC",
            (application_key,)
        )
        return [dict(r) for r in cursor.fetchall()]


def update_answer(answer_id: int, new_value: str, tracker_factory: Callable[[], Any]) -> bool:
    """Updates an existing answer entry (user override) and updates the answer bank.

    `tracker_factory` opens the tracker the override is mirrored into; it is called inside the guarded block, so a
    tracker that cannot be opened costs the mirror, never the saved override."""
    init_answer_ledger()
    now_iso = datetime.now(timezone.utc).isoformat()
    with _LOCK, sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM application_answers WHERE id = ?", (answer_id,))
        row = cursor.fetchone()
        if not row:
            return False
        old_val = row["final_answer"]
        q_text = row["question_text"]
        app_key = row["application_key"]
        cursor.execute("""
            UPDATE application_answers
            SET final_answer = ?, source = 'USER_OVERRIDE', previous_value = ?,
                correction_reason = 'User manual override from Cockpit', timestamp = ?, status = 'OVERRIDDEN'
            WHERE id = ?
        """, (new_value, old_val, now_iso, answer_id))
        conn.commit()

    try:
        tracker = tracker_factory()
        if hasattr(tracker, "record_answer"):
            from urllib.parse import urlparse
            application = tracker.get(app_key)
            host = (urlparse(application.url).hostname or "") if application else ""
            tracker.record_answer(app_key, host, q_text, new_value, answered_by="user")
    except Exception as exc:
        logger.warning("Cockpit override saved locally but tracker synchronization failed (%s)", type(exc).__name__)
    return True
