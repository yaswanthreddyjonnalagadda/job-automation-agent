"""Database storage abstraction for company directory, jobs, and applications.
Supports PostgreSQL (via psycopg2/psycopg) with automatic SQLite fallback for lightweight testing/local usage.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set


class JobStorage:
    def __init__(self, dsn: Optional[str] = None):
        self.dsn = dsn or os.getenv("DATABASE_URL", "sqlite:///data/jobs_platform.db")
        self.is_sqlite = self.dsn.startswith("sqlite")
        if self.is_sqlite:
            db_path = self.dsn.replace("sqlite:///", "").replace("sqlite://", "")
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._init_sqlite()
        else:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            self._conn = psycopg2.connect(self.dsn, cursor_factory=RealDictCursor)
            self._init_postgres()

    def _init_sqlite(self):
        with self._conn:
            self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS company_directory (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name    TEXT NOT NULL,
                domain          TEXT,
                ats_platform    TEXT NOT NULL,
                board_slug      TEXT NOT NULL,
                board_url       TEXT NOT NULL,
                api_endpoint    TEXT NOT NULL,
                is_active       INTEGER DEFAULT 1,
                last_verified   TEXT,
                last_polled     TEXT,
                total_jobs_last INTEGER DEFAULT 0,
                discovered_via  TEXT,
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(ats_platform, board_slug)
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                external_id     TEXT NOT NULL,
                ats_platform    TEXT NOT NULL,
                company_slug    TEXT NOT NULL,
                company_name    TEXT,
                title           TEXT NOT NULL,
                location        TEXT,
                department      TEXT,
                description     TEXT,
                description_html TEXT,
                apply_url       TEXT NOT NULL,
                salary_min      REAL,
                salary_max      REAL,
                salary_currency TEXT DEFAULT 'USD',
                employment_type TEXT,
                remote          INTEGER DEFAULT 0,
                posted_at       TEXT,
                first_seen_at   TEXT DEFAULT CURRENT_TIMESTAMP,
                last_seen_at    TEXT DEFAULT CURRENT_TIMESTAMP,
                removed_at      TEXT,
                status          TEXT DEFAULT 'active',
                match_score     REAL,
                matched_at      TEXT,
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(ats_platform, external_id)
            );

            CREATE TABLE IF NOT EXISTS applications (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id              INTEGER REFERENCES jobs(id),
                match_score         REAL,
                resume_version      TEXT,
                cover_letter        TEXT,
                tailoring_prompt    TEXT,
                claude_tokens_used  INTEGER,
                claude_cost_usd     REAL,
                status              TEXT DEFAULT 'pending',
                submitted_at        TEXT,
                portal_used         TEXT,
                confirmation_id     TEXT,
                error_log           TEXT,
                follow_up_date      TEXT,
                response_received   INTEGER DEFAULT 0,
                response_type       TEXT,
                response_date       TEXT,
                notes               TEXT,
                created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """)

    def _init_postgres(self):
        with self._conn.cursor() as cur:
            migration_path = os.path.join(os.path.dirname(__file__), "..", "..", "migrations", "001_initial_schema.sql")
            if os.path.exists(migration_path):
                with open(migration_path, "r", encoding="utf-8") as f:
                    cur.execute(f.read())
            self._conn.commit()

    def get_active_job_ids(self, ats_platform: str, company_slug: str) -> Set[str]:
        query = "SELECT external_id FROM jobs WHERE ats_platform = ? AND company_slug = ? AND status = 'active'"
        if not self.is_sqlite:
            query = query.replace("?", "%s")
        cur = self._conn.cursor()
        cur.execute(query, (ats_platform, company_slug))
        rows = cur.fetchall()
        return {r["external_id"] if isinstance(r, dict) or hasattr(r, "keys") else r[0] for r in rows}

    def insert_job(self, job: Dict[str, Any]) -> int:
        if not job.get("apply_url"):
            job["apply_url"] = f"https://{job.get('company_slug', 'company')}.com/jobs/{job.get('external_id', '')}"
        keys = [
            "external_id", "ats_platform", "company_slug", "company_name",
            "title", "location", "department", "description", "description_html",
            "apply_url", "salary_min", "salary_max", "salary_currency",
            "employment_type", "remote", "posted_at", "status"
        ]
        values = [job.get(k) for k in keys]
        if self.is_sqlite:
            placeholders = ", ".join(["?"] * len(keys))
            sql = f"INSERT INTO jobs ({', '.join(keys)}) VALUES ({placeholders})"
            with self._conn:
                cur = self._conn.cursor()
                cur.execute(sql, values)
                return cur.lastrowid
        else:
            placeholders = ", ".join(["%s"] * len(keys))
            sql = f"INSERT INTO jobs ({', '.join(keys)}) VALUES ({placeholders}) RETURNING id"
            with self._conn.cursor() as cur:
                cur.execute(sql, values)
                row = cur.fetchone()
                self._conn.commit()
                return row["id"] if isinstance(row, dict) else row[0]

    def mark_removed(self, external_id: str, ats_platform: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        sql = "UPDATE jobs SET status = 'removed', removed_at = ?, updated_at = ? WHERE external_id = ? AND ats_platform = ?"
        if not self.is_sqlite:
            sql = sql.replace("?", "%s")
        with self._conn:
            cur = self._conn.cursor()
            cur.execute(sql, (now, now, external_id, ats_platform))

    def bulk_update_last_seen(self, external_ids: Set[str], ats_platform: str) -> None:
        if not external_ids:
            return
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.cursor()
        for ext_id in external_ids:
            sql = "UPDATE jobs SET last_seen_at = ?, updated_at = ? WHERE external_id = ? AND ats_platform = ?"
            if not self.is_sqlite:
                sql = sql.replace("?", "%s")
            cur.execute(sql, (now, now, ext_id, ats_platform))
        if not self.is_sqlite:
            self._conn.commit()

    def update_match_score(self, job_id: int, score: float) -> None:
        now = datetime.now(timezone.utc).isoformat()
        sql = "UPDATE jobs SET match_score = ?, matched_at = ?, updated_at = ? WHERE id = ?"
        if not self.is_sqlite:
            sql = sql.replace("?", "%s")
        with self._conn:
            cur = self._conn.cursor()
            cur.execute(sql, (score, now, now, job_id))

    def get_unmatched_jobs(self, limit: int = 50) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM jobs WHERE match_score IS NULL AND status = 'active' ORDER BY id ASC LIMIT ?"
        if not self.is_sqlite:
            sql = sql.replace("?", "%s")
        cur = self._conn.cursor()
        cur.execute(sql, (limit,))
        rows = cur.fetchall()
        return [dict(r) for r in rows]

    def register_company(self, company: Dict[str, Any]) -> int:
        keys = ["company_name", "domain", "ats_platform", "board_slug", "board_url", "api_endpoint", "discovered_via"]
        values = [company.get(k) for k in keys]
        if self.is_sqlite:
            placeholders = ", ".join(["?"] * len(keys))
            sql = f"INSERT OR IGNORE INTO company_directory ({', '.join(keys)}) VALUES ({placeholders})"
            with self._conn:
                cur = self._conn.cursor()
                cur.execute(sql, values)
                return cur.lastrowid
        else:
            placeholders = ", ".join(["%s"] * len(keys))
            sql = f"INSERT INTO company_directory ({', '.join(keys)}) VALUES ({placeholders}) ON CONFLICT DO NOTHING RETURNING id"
            with self._conn.cursor() as cur:
                cur.execute(sql, values)
                row = cur.fetchone()
                self._conn.commit()
                return (row["id"] if isinstance(row, dict) else row[0]) if row else 0

    def get_companies_to_poll(self, ats_platform: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM company_directory WHERE is_active = 1"
        params: List[Any] = []
        if ats_platform:
            sql += " AND ats_platform = ?"
            params.append(ats_platform)
        if not self.is_sqlite:
            sql = sql.replace("?", "%s").replace("is_active = 1", "is_active = TRUE")
        cur = self._conn.cursor()
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        return [dict(r) for r in rows]
