"""Read-only client for the job-source service's `open_jobs` view.

Used by `web_ui._suggested_jobs` when the `JOB_SOURCE` setting is `"service"`
(see Card 7: "switch the agent over, behind a switch that stays OFF"). This
module never writes to the job-source database -- it opens a read-only
connection and runs exactly one SELECT. A DSN that is unset or unreachable,
or any other read failure, logs a warning and returns `[]`; the dashboard
must never crash because the job-source database is unavailable.

Real use: `JOB_SOURCE_DSN` points at the job-source Postgres instance (port
5433, separate from the legacy `job-agent-db` on 5432). Tests point it at a
SQLite temp file instead.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("job_source_client")


def _iso(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _row_to_job(row: Dict[str, Any]) -> Dict[str, Any]:
    """Maps one `open_jobs` row to the dict shape `web_ui._suggested_jobs` expects from
    `job_discovery.get_discovered_jobs` -- see `discovered_view.prepare` and the job-card
    template in `web_ui.py` (`INDEX_HTML`) for the keys this must carry. The id is prefixed
    `js:` so it can never collide with a legacy `discovered_jobs.id`.
    """
    ats_platform = row.get("ats_platform") or ""
    external_id = row.get("external_id") or ""
    return {
        "id": f"js:{row.get('id')}",
        "dedup_key": f"{ats_platform}:{external_id}",
        "company_name": row.get("company_name") or row.get("company_slug") or "",
        "ats_type": ats_platform,
        "job_title": row.get("title") or "",
        "location": row.get("location") or "",
        "is_remote": bool(row.get("remote")),
        "url": row.get("apply_url") or "",
        "match_score": row.get("match_score"),
        "matched_skills": [],
        "status": "discovered",
        "discovered_at": row.get("first_seen_at"),
        "resume_score": None,
        "resume_matched": [],
        "resume_missing": [],
        "posted_at": row.get("posted_at"),
        "posted_precision": None,
        "country_code": None,
        "readiness_json": None,
    }


def _open_readonly_connection(dsn: str):
    """Returns (connection, is_sqlite). Raises on any connection failure -- callers decide
    how to handle that (here: log and return [])."""
    if dsn.startswith("sqlite"):
        db_path = dsn.replace("sqlite:///", "").replace("sqlite://", "").replace("\\", "/")
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn, True
    import psycopg
    from psycopg.rows import dict_row
    conn = psycopg.connect(dsn, row_factory=dict_row)
    conn.read_only = True
    return conn, False


def get_open_jobs(min_score: int = 60, days: int = 7, limit: int = 40) -> List[Dict[str, Any]]:
    """Open jobs from the job-source service: active, with a description (the `open_jobs`
    view's own definition), scored at least `min_score`, first seen within the last `days`
    days. Best score first, newest-first (by `first_seen_at`) within a tie. Read-only;
    returns `[]` (with a logged warning) rather than raising on any failure to reach or
    read the database.
    """
    dsn = os.getenv("JOB_SOURCE_DSN")
    if not dsn:
        logger.warning("JOB_SOURCE_DSN is not set; returning no job-source jobs")
        return []

    cutoff = _iso(datetime.now(timezone.utc) - timedelta(days=days))

    try:
        conn, is_sqlite = _open_readonly_connection(dsn)
    except Exception as exc:
        logger.warning("Could not reach job-source database (%s): %s", dsn, exc)
        return []

    try:
        sql = (
            "SELECT * FROM open_jobs WHERE match_score >= ? AND first_seen_at >= ? "
            "ORDER BY match_score DESC, first_seen_at DESC LIMIT ?"
        )
        if not is_sqlite:
            sql = sql.replace("?", "%s")
        cur = conn.cursor()
        cur.execute(sql, (min_score, cutoff, limit))
        rows = cur.fetchall()
        return [_row_to_job(dict(row)) for row in rows]
    except Exception as exc:
        logger.warning("Could not read job-source open_jobs view (%s): %s", dsn, exc)
        return []
    finally:
        try:
            conn.close()
        except Exception as close_exc:
            logger.debug("job_source_client: error closing connection: %s", close_exc)
