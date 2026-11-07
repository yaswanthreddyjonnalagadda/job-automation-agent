"""Database storage abstraction for company directory, jobs, and applications.
Supports PostgreSQL (via psycopg 3) with automatic SQLite fallback for lightweight testing/local usage.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple


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
            import psycopg
            from psycopg.rows import dict_row
            self._conn = psycopg.connect(self.dsn, row_factory=dict_row)
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

            CREATE TABLE IF NOT EXISTS search_specs (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                version             INTEGER NOT NULL,
                spec_json           TEXT NOT NULL,
                raw_instructions    TEXT NOT NULL,
                is_active           INTEGER DEFAULT 1,
                created_at          TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS job_sources (
                job_id              INTEGER NOT NULL,
                source_name         TEXT NOT NULL,
                seen_at             TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (job_id, source_name)
            );

            CREATE TABLE IF NOT EXISTS crawl_runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                provider        TEXT NOT NULL,
                started_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at     TEXT,
                boards_total    INTEGER DEFAULT 0,
                boards_complete INTEGER DEFAULT 0,
                boards_failed   INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS board_runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id          INTEGER NOT NULL,
                ats_platform    TEXT NOT NULL,
                board           TEXT NOT NULL,
                complete        INTEGER NOT NULL DEFAULT 0,
                postings_seen   INTEGER DEFAULT 0,
                new_count       INTEGER DEFAULT 0,
                reopened_count  INTEGER DEFAULT 0,
                closed_count    INTEGER DEFAULT 0,
                error           TEXT,
                finished_at     TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE VIEW IF NOT EXISTS open_jobs AS
            SELECT * FROM jobs
            WHERE status = 'active' AND detail_status = 'done';
            """)
            self._add_column_if_missing("jobs", "detail_status", "TEXT DEFAULT 'done'")
            self._add_column_if_missing("jobs", "liveness_strikes", "INTEGER DEFAULT 0")
            self._add_column_if_missing("company_directory", "status", "TEXT DEFAULT 'live'")
            self._add_column_if_missing("company_directory", "last_crawl_at", "TEXT")
            self._add_column_if_missing("company_directory", "last_error", "TEXT")
            self._add_column_if_missing("company_directory", "failures_in_row", "INTEGER DEFAULT 0")

    def _add_column_if_missing(self, table: str, column: str, decl: str) -> None:
        cur = self._conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in cur.fetchall()}
        if column not in existing:
            with self._conn:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    def _init_postgres(self):
        with self._conn.cursor() as cur:
            for migration_file in ("001_initial_schema.sql", "002_job_source.sql"):
                migration_path = os.path.join(os.path.dirname(__file__), "..", "..", "migrations", migration_file)
                if os.path.exists(migration_path):
                    with open(migration_path, "r", encoding="utf-8") as f:
                        cur.execute(f.read())
            cur.execute("""
            CREATE TABLE IF NOT EXISTS search_specs (
                id                  BIGSERIAL PRIMARY KEY,
                version             INTEGER NOT NULL,
                spec_json           JSONB NOT NULL,
                raw_instructions    TEXT NOT NULL,
                is_active           BOOLEAN DEFAULT true,
                created_at          TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS job_sources (
                job_id              BIGINT NOT NULL,
                source_name         TEXT NOT NULL,
                seen_at             TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (job_id, source_name)
            );
            """)
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
        if self.is_sqlite:
            values = [int(bool(job.get("remote"))) if k == "remote" else job.get(k) for k in keys]
            placeholders = ", ".join(["?"] * len(keys))
            sql = f"INSERT OR REPLACE INTO jobs ({', '.join(keys)}) VALUES ({placeholders})"
            with self._conn:
                cur = self._conn.cursor()
                cur.execute(sql, values)
                return cur.lastrowid
        else:
            values = [bool(job.get(k)) if k == "remote" else job.get(k) for k in keys]
            placeholders = ", ".join(["%s"] * len(keys))
            sql = f"INSERT INTO jobs ({', '.join(keys)}) VALUES ({placeholders}) ON CONFLICT (ats_platform, external_id) DO UPDATE SET updated_at = NOW(), last_seen_at = NOW() RETURNING id"
            try:
                with self._conn.cursor() as cur:
                    cur.execute(sql, values)
                    row = cur.fetchone()
                    self._conn.commit()
                    return row["id"] if isinstance(row, dict) else row[0]
            except Exception:
                self._conn.rollback()
                raise

    def mark_removed(self, external_id: str, ats_platform: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        sql = "UPDATE jobs SET status = 'removed', removed_at = ?, updated_at = ? WHERE external_id = ? AND ats_platform = ?"
        if self.is_sqlite:
            with self._conn:
                cur = self._conn.cursor()
                cur.execute(sql, (now, now, external_id, ats_platform))
        else:
            sql = sql.replace("?", "%s")
            with self._conn.cursor() as cur:
                cur.execute(sql, (now, now, external_id, ats_platform))
            self._conn.commit()

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
        if self.is_sqlite:
            with self._conn:
                cur = self._conn.cursor()
                cur.execute(sql, (score, now, now, job_id))
        else:
            sql = sql.replace("?", "%s")
            with self._conn.cursor() as cur:
                cur.execute(sql, (score, now, now, job_id))
            self._conn.commit()

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

    def create_application(self, app_data: Dict[str, Any]) -> int:
        keys = [
            "job_id", "match_score", "resume_version", "cover_letter",
            "tailoring_prompt", "claude_tokens_used", "claude_cost_usd",
            "status", "portal_used"
        ]
        values = [app_data.get(k) for k in keys]
        if self.is_sqlite:
            placeholders = ", ".join(["?"] * len(keys))
            sql = f"INSERT INTO applications ({', '.join(keys)}) VALUES ({placeholders})"
            with self._conn:
                cur = self._conn.cursor()
                cur.execute(sql, values)
                return cur.lastrowid
        else:
            placeholders = ", ".join(["%s"] * len(keys))
            sql = f"INSERT INTO applications ({', '.join(keys)}) VALUES ({placeholders}) RETURNING id"
            with self._conn.cursor() as cur:
                cur.execute(sql, values)
                row = cur.fetchone()
                self._conn.commit()
                return row["id"] if isinstance(row, dict) else row[0]

    def get_job_by_id(self, job_id: int) -> Optional[Dict[str, Any]]:
        sql = "SELECT * FROM jobs WHERE id = ?"
        if not self.is_sqlite:
            sql = sql.replace("?", "%s")
        cur = self.storage_cursor() if hasattr(self, 'storage_cursor') else self._conn.cursor()
        cur.execute(sql, (job_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def update_application_status(self, app_id: int, status: str, notes: Optional[str] = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        sql = "UPDATE applications SET status = ?, notes = ?, updated_at = ?"
        params = [status, notes, now]
        if status == "submitted":
            sql += ", submitted_at = ?"
            params.append(now)
        sql += " WHERE id = ?"
        params.append(app_id)
        if self.is_sqlite:
            with self._conn:
                cur = self._conn.cursor()
                cur.execute(sql, tuple(params))
        else:
            sql = sql.replace("?", "%s")
            with self._conn.cursor() as cur:
                cur.execute(sql, tuple(params))
            self._conn.commit()

    def update_job_status(self, job_id: int, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        sql = "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?"
        params = [status, now, job_id]
        if self.is_sqlite:
            with self._conn:
                cur = self._conn.cursor()
                cur.execute(sql, tuple(params))
        else:
            sql = sql.replace("?", "%s")
            with self._conn.cursor() as cur:
                cur.execute(sql, tuple(params))
            self._conn.commit()

    def save_search_spec(self, spec: Any) -> int:
        """Saves a new SearchSpec, marks older versions as inactive, and returns the new ID."""
        import json
        from services.matcher.search_spec import SearchSpec
        if not isinstance(spec, SearchSpec):
            spec = SearchSpec.model_validate(spec)

        spec_data = spec.model_dump(mode="json")
        spec_json_str = json.dumps(spec_data)
        now = datetime.now(timezone.utc).isoformat()

        if self.is_sqlite:
            with self._conn:
                cur = self._conn.cursor()
                # Deactivate older specs
                cur.execute("UPDATE search_specs SET is_active = 0 WHERE is_active = 1")
                cur.execute(
                    "INSERT INTO search_specs (version, spec_json, raw_instructions, is_active, created_at) "
                    "VALUES (?, ?, ?, 1, ?)",
                    (spec.version, spec_json_str, spec.raw_instructions, now),
                )
                return cur.lastrowid
        else:
            with self._conn.cursor() as cur:
                cur.execute("UPDATE search_specs SET is_active = false WHERE is_active = true")
                cur.execute(
                    "INSERT INTO search_specs (version, spec_json, raw_instructions, is_active, created_at) "
                    "VALUES (%s, %s, %s, true, %s) RETURNING id",
                    (spec.version, spec_json_str, spec.raw_instructions, now),
                )
                row = cur.fetchone()
                self._conn.commit()
                return row["id"] if isinstance(row, dict) else row[0]

    def get_active_search_spec(self) -> Optional[Any]:
        """Loads the current active SearchSpec from database storage."""
        import json
        from services.matcher.search_spec import SearchSpec
        query = "SELECT spec_json FROM search_specs WHERE is_active = ? ORDER BY version DESC LIMIT 1"
        if self.is_sqlite:
            cur = self._conn.cursor()
            cur.execute(query, (1,))
            row = cur.fetchone()
            if not row:
                return None
            data = json.loads(row["spec_json"] if isinstance(row, dict) or hasattr(row, "keys") else row[0])
            return SearchSpec.model_validate(data)
        else:
            query = "SELECT spec_json FROM search_specs WHERE is_active = true ORDER BY version DESC LIMIT 1"
            with self._conn.cursor() as cur:
                cur.execute(query)
                row = cur.fetchone()
                if not row:
                    return None
                raw_json = row["spec_json"] if isinstance(row, dict) else row[0]
                data = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
                return SearchSpec.model_validate(data)

    def list_search_spec_versions(self) -> List[Dict[str, Any]]:
        """Returns all historical SearchSpec versions for auditing."""
        query = "SELECT id, version, raw_instructions, is_active, created_at FROM search_specs ORDER BY version DESC"
        cur = self._conn.cursor() if self.is_sqlite else self._conn.cursor()
        cur.execute(query)
        rows = cur.fetchall()
        return [dict(r) for r in rows]

    def insert_or_merge_job(self, job: Dict[str, Any], source: str = "direct") -> int:
        """Inserts a job or merges it if the same (ats_platform, external_id) already exists,
        updating source tracking. Never merges on company/title/location: two distinct real
        postings can legitimately share all three.
        """
        cur = self._conn.cursor()
        find_sql = "SELECT id FROM jobs WHERE ats_platform = ? AND external_id = ? LIMIT 1"
        if not self.is_sqlite:
            find_sql = find_sql.replace("?", "%s")
        cur.execute(find_sql, (
            job.get("ats_platform", ""),
            str(job.get("external_id", "")),
        ))
        row = cur.fetchone()
        if row:
            job_id = row["id"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
        else:
            job_id = self.insert_job(job)

        src_sql = "INSERT OR IGNORE INTO job_sources (job_id, source_name) VALUES (?, ?)"
        if not self.is_sqlite:
            src_sql = "INSERT INTO job_sources (job_id, source_name) VALUES (%s, %s) ON CONFLICT DO NOTHING"
        if self.is_sqlite:
            with self._conn:
                cur.execute(src_sql, (job_id, source))
        else:
            with self._conn.cursor() as cur:
                cur.execute(src_sql, (job_id, source))
            self._conn.commit()

        return job_id

    def get_job_sources(self, job_id: int) -> List[str]:
        """Returns all distinct sources that yielded this job posting."""
        sql = "SELECT source_name FROM job_sources WHERE job_id = ?"
        if not self.is_sqlite:
            sql = sql.replace("?", "%s")
        cur = self._conn.cursor()
        cur.execute(sql, (job_id,))
        rows = cur.fetchall()
        return [r["source_name"] if isinstance(r, dict) or hasattr(r, "keys") else r[0] for r in rows]

    # -- Card 1: store jobs and close them safely --------------------------

    @staticmethod
    def _iso(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    def upsert_posting(self, provider: str, board: str, posting: Dict[str, Any], now: Any) -> Tuple[int, str]:
        """Inserts or refreshes one posting for (provider, board).

        Returns (job_id, label) where label is "new", "seen", or "reopened".
        Never overwrites a stored description/description_html with an empty one.
        """
        now_str = self._iso(now)
        ext_id = str(posting.get("external_id", ""))
        description = posting.get("description") or ""
        description_html = posting.get("description_html") or ""
        detail_status = "done" if description else "pending"
        apply_url = posting.get("apply_url") or f"https://{board}.com/jobs/{ext_id}"

        cur = self._conn.cursor()
        find_sql = "SELECT id, status FROM jobs WHERE ats_platform = ? AND external_id = ?"
        if not self.is_sqlite:
            find_sql = find_sql.replace("?", "%s")
        cur.execute(find_sql, (provider, ext_id))
        existing = cur.fetchone()

        if existing:
            prior_status = existing["status"] if isinstance(existing, dict) or hasattr(existing, "keys") else existing[1]
            existing_id = existing["id"] if isinstance(existing, dict) or hasattr(existing, "keys") else existing[0]
            label = "seen" if prior_status == "active" else "reopened"
        else:
            existing_id = None
            label = "new"

        remote_val = posting.get("remote")
        remote_param = int(bool(remote_val)) if self.is_sqlite else bool(remote_val)

        upsert_sql = """
        INSERT INTO jobs (
            external_id, ats_platform, company_slug, company_name, title, location,
            department, description, description_html, apply_url, salary_min, salary_max,
            salary_currency, employment_type, remote, posted_at, status,
            first_seen_at, last_seen_at, detail_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
        ON CONFLICT (ats_platform, external_id) DO UPDATE SET
            last_seen_at = excluded.last_seen_at,
            status = 'active',
            removed_at = NULL,
            title = excluded.title,
            location = excluded.location,
            apply_url = excluded.apply_url,
            description = COALESCE(NULLIF(excluded.description, ''), jobs.description),
            description_html = COALESCE(NULLIF(excluded.description_html, ''), jobs.description_html),
            detail_status = CASE WHEN COALESCE(NULLIF(excluded.description, ''), jobs.description, '') = ''
                                 THEN 'pending' ELSE 'done' END
        """
        values = [
            ext_id, provider, board, posting.get("company_name"), posting.get("title"), posting.get("location"),
            posting.get("department"), description, description_html, apply_url,
            posting.get("salary_min"), posting.get("salary_max"), posting.get("salary_currency", "USD"),
            posting.get("employment_type"), remote_param,
            posting.get("posted_at"), now_str, now_str, detail_status,
        ]
        if not self.is_sqlite:
            upsert_sql = upsert_sql.replace("?", "%s")

        if self.is_sqlite:
            with self._conn:
                cur.execute(upsert_sql, values)
            job_id = existing_id if existing_id is not None else cur.lastrowid
        else:
            with self._conn.cursor() as cur2:
                cur2.execute(upsert_sql, values)
            self._conn.commit()
            if existing_id is not None:
                job_id = existing_id
            else:
                cur.execute(find_sql, (provider, ext_id))
                row = cur.fetchone()
                job_id = row["id"] if isinstance(row, dict) else row[0]

        return job_id, label

    def close_unseen(self, provider: str, board: str, older_than: Any, now: Any = None) -> int:
        """Marks active jobs for (provider, board) removed when last_seen_at < older_than.
        Returns the number of jobs closed.
        """
        cutoff = self._iso(older_than)
        removed_at = self._iso(now) if now is not None else datetime.now(timezone.utc).isoformat()
        sql = """
        UPDATE jobs SET status = 'removed', removed_at = ?, updated_at = ?
        WHERE ats_platform = ? AND company_slug = ? AND status = 'active' AND last_seen_at < ?
        """
        params = (removed_at, removed_at, provider, board, cutoff)
        if not self.is_sqlite:
            sql = sql.replace("?", "%s")
        if self.is_sqlite:
            with self._conn:
                cur = self._conn.cursor()
                cur.execute(sql, params)
                return cur.rowcount
        else:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                count = cur.rowcount
            self._conn.commit()
            return count

    def get_board(self, provider: str, board: str) -> Optional[Dict[str, Any]]:
        sql = "SELECT * FROM company_directory WHERE ats_platform = ? AND board_slug = ?"
        if not self.is_sqlite:
            sql = sql.replace("?", "%s")
        cur = self._conn.cursor()
        cur.execute(sql, (provider, board))
        row = cur.fetchone()
        return dict(row) if row else None

    def record_board_failure(self, provider: str, board: str, error: Optional[str]) -> int:
        """Increments failures_in_row and records last_error. Pauses the board at 3 in a row."""
        row = self.get_board(provider, board) or {}
        failures = int(row.get("failures_in_row") or 0) + 1
        status = "paused" if failures >= 3 else row.get("status", "live")
        sql = """
        UPDATE company_directory SET last_error = ?, failures_in_row = ?, status = ?
        WHERE ats_platform = ? AND board_slug = ?
        """
        params = (error, failures, status, provider, board)
        if not self.is_sqlite:
            sql = sql.replace("?", "%s")
        if self.is_sqlite:
            with self._conn:
                self._conn.execute(sql, params)
        else:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
            self._conn.commit()
        return failures

    def record_board_success(self, provider: str, board: str, now: Any) -> None:
        """Resets the failure streak after a clean complete read."""
        now_str = self._iso(now)
        sql = """
        UPDATE company_directory SET failures_in_row = 0, last_error = NULL, last_crawl_at = ?
        WHERE ats_platform = ? AND board_slug = ?
        """
        params = (now_str, provider, board)
        if not self.is_sqlite:
            sql = sql.replace("?", "%s")
        if self.is_sqlite:
            with self._conn:
                self._conn.execute(sql, params)
        else:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
            self._conn.commit()

    def record_board_suspicious_empty(self, provider: str, board: str, now: Any) -> None:
        """Marks a complete-but-empty read as suspicious without closing anything."""
        now_str = self._iso(now)
        sql = """
        UPDATE company_directory SET last_error = 'suspicious_empty', last_crawl_at = ?
        WHERE ats_platform = ? AND board_slug = ?
        """
        params = (now_str, provider, board)
        if not self.is_sqlite:
            sql = sql.replace("?", "%s")
        if self.is_sqlite:
            with self._conn:
                self._conn.execute(sql, params)
        else:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
            self._conn.commit()

    def start_crawl_run(self, provider: str, now: Any = None) -> int:
        now_str = self._iso(now) if now is not None else datetime.now(timezone.utc).isoformat()
        if self.is_sqlite:
            sql = "INSERT INTO crawl_runs (provider, started_at) VALUES (?, ?)"
            with self._conn:
                cur = self._conn.cursor()
                cur.execute(sql, (provider, now_str))
                return cur.lastrowid
        else:
            sql = "INSERT INTO crawl_runs (provider, started_at) VALUES (%s, %s) RETURNING id"
            with self._conn.cursor() as cur:
                cur.execute(sql, (provider, now_str))
                row = cur.fetchone()
                self._conn.commit()
                return row["id"] if isinstance(row, dict) else row[0]

    def finish_crawl_run(
        self, run_id: int, boards_total: int, boards_complete: int, boards_failed: int, now: Any = None
    ) -> None:
        now_str = self._iso(now) if now is not None else datetime.now(timezone.utc).isoformat()
        sql = """
        UPDATE crawl_runs SET finished_at = ?, boards_total = ?, boards_complete = ?, boards_failed = ?
        WHERE id = ?
        """
        params = (now_str, boards_total, boards_complete, boards_failed, run_id)
        if not self.is_sqlite:
            sql = sql.replace("?", "%s")
        if self.is_sqlite:
            with self._conn:
                self._conn.execute(sql, params)
        else:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
            self._conn.commit()

    def record_board_run(
        self,
        run_id: Optional[int],
        ats_platform: str,
        board: str,
        complete: bool,
        postings_seen: int,
        new_count: int,
        reopened_count: int,
        closed_count: int,
        error: Optional[str],
        finished_at: Any,
    ) -> int:
        finished_at_str = self._iso(finished_at)
        complete_param = int(bool(complete)) if self.is_sqlite else bool(complete)
        sql = """
        INSERT INTO board_runs (
            run_id, ats_platform, board, complete, postings_seen,
            new_count, reopened_count, closed_count, error, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            run_id, ats_platform, board, complete_param, postings_seen,
            new_count, reopened_count, closed_count, error, finished_at_str,
        )
        if self.is_sqlite:
            with self._conn:
                cur = self._conn.cursor()
                cur.execute(sql, params)
                return cur.lastrowid
        else:
            sql = sql.replace("?", "%s") + " RETURNING id"
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                self._conn.commit()
                return row["id"] if isinstance(row, dict) else row[0]

    def has_description(self, provider: str, external_id: str) -> bool:
        sql = "SELECT description FROM jobs WHERE ats_platform = ? AND external_id = ?"
        if not self.is_sqlite:
            sql = sql.replace("?", "%s")
        cur = self._conn.cursor()
        cur.execute(sql, (provider, external_id))
        row = cur.fetchone()
        if not row:
            return False
        desc = row["description"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
        return bool(desc)

    def boards(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM company_directory"
        params: Tuple[Any, ...] = ()
        if status:
            sql += " WHERE status = ?"
            params = (status,)
        if not self.is_sqlite:
            sql = sql.replace("?", "%s")
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    def set_board_status(self, board_id: int, status: str) -> None:
        sql = "UPDATE company_directory SET status = ? WHERE id = ?"
        params = (status, board_id)
        if not self.is_sqlite:
            sql = sql.replace("?", "%s")
        if self.is_sqlite:
            with self._conn:
                self._conn.execute(sql, params)
        else:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
            self._conn.commit()


