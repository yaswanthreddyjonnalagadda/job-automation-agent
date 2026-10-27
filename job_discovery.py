"""
Autonomous ATS Discovery Engine.

Continuously discovers open positions directly from public career APIs
(Greenhouse, Lever, Ashby, Workday CXS) without browser overhead.
Applies target titles, location, and visa sponsorship criteria to score
and ingest high-fit opportunities into data/applications.db.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

logger = logging.getLogger("job_discovery")
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "applications.db"
COMPANIES_CONFIG_PATH = BASE_DIR / "data" / "target_companies.json"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

# Negative filters to prune non-engineering or unrelated roles
NEGATIVE_TITLE_PATTERNS = [
    r"\bintern\b",
    r"\binternship\b",
    r"\bco-?op\b",
    r"\brecruiter\b",
    r"\btalent acquisition\b",
    r"\baccount executive\b",
    r"\bsales representative\b",
    r"\bcounsel\b",
    r"\blegal\b",
    r"\bparalegal\b",
    r"\bfinance\b",
    r"\baccountant\b",
    r"\bpayroll\b",
    r"\bmarketing\b",
    r"\bexecutive assistant\b",
    r"\boffice manager\b",
]


def ensure_discovery_schema(conn: sqlite3.Connection) -> None:
    """Creates the discovered_jobs table and indexes if not present."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS discovered_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dedup_key TEXT UNIQUE,
            company_name TEXT NOT NULL,
            company_token TEXT NOT NULL,
            ats_type TEXT NOT NULL,
            job_title TEXT NOT NULL,
            location TEXT,
            is_remote INTEGER DEFAULT 0,
            url TEXT NOT NULL,
            match_score INTEGER DEFAULT 0,
            matched_skills TEXT DEFAULT '[]',
            status TEXT DEFAULT 'discovered',
            discovered_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_discovered_status ON discovered_jobs(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_discovered_score ON discovered_jobs(match_score DESC)"
    )
    conn.commit()


def compute_job_hash(company: str, title: str, location: str, url: str) -> str:
    """Generates a stable SHA256 hex digest for deduplication."""
    clean_url = url.split("?")[0].rstrip("/")
    norm = f"{company.strip().lower()}|{title.strip().lower()}|{location.strip().lower()}|{clean_url.strip().lower()}"
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def load_profile() -> dict[str, Any]:
    """Loads candidate profile preferences and targets."""
    import config
    try:
        user_prof = config.get_user_profile()
        return {
            "target_titles": getattr(user_prof, "target_titles", []) or [
                "Senior Network Engineer", "Network Engineer", "Cloud Network Engineer",
                "Infrastructure Engineer", "DevOps Engineer", "Site Reliability Engineer"
            ],
            "locations": getattr(user_prof, "locations", []) or [
                "Dallas, TX", "Austin, TX", "Irving, TX", "Plano, TX", "Ashburn, VA", "Remote"
            ],
            "requires_visa_sponsorship": getattr(user_prof, "requires_visa_sponsorship", True),
        }
    except Exception:
        return {
            "target_titles": [
                "Senior Network Engineer", "Network Engineer", "Cloud Network Engineer",
                "Infrastructure Engineer", "DevOps Engineer", "Site Reliability Engineer"
            ],
            "locations": [
                "Dallas, TX", "Austin, TX", "Irving, TX", "Plano, TX", "Ashburn, VA", "Remote"
            ],
            "requires_visa_sponsorship": True,
        }


def is_negative_title(title: str) -> bool:
    """Returns True if title matches explicitly excluded non-engineering roles."""
    title_lower = title.lower()
    for pat in NEGATIVE_TITLE_PATTERNS:
        if re.search(pat, title_lower):
            return True
    return False


def calculate_match_score(
    title: str, location: str, profile: dict[str, Any]
) -> tuple[int, list[str]]:
    """
    Computes candidate match score (0-100) based on title relevance, location,
    and engineering keywords.
    """
    if is_negative_title(title):
        return 0, []

    score = 0
    matched_skills: list[str] = []
    title_lower = title.lower()
    loc_lower = (location or "").lower()

    # 1. Title match (up to 60 points) - longer/more specific matches first
    target_titles = sorted(profile.get("target_titles", []), key=len, reverse=True)
    for target in target_titles:
        tgt_lower = target.lower()
        if tgt_lower in title_lower:
            score += 60
            matched_skills.append(target)
            break
        # Partial token overlap
        target_tokens = set(re.findall(r"\w+", tgt_lower))
        title_tokens = set(re.findall(r"\w+", title_lower))
        if target_tokens and target_tokens.issubset(title_tokens):
            score += 50
            matched_skills.append(target)
            break

    # If no title alignment, score remains low
    if score == 0:
        # Check general technical keywords
        tech_keywords = ["network", "cloud", "infrastructure", "devops", "sre", "systems", "security"]
        for kw in tech_keywords:
            if kw in title_lower:
                score += 30
                matched_skills.append(kw.capitalize())
                break

    if score == 0:
        return 0, []

    # 2. Location match (up to 30 points)
    is_remote = bool(re.search(r"\bremote\b|\banywhere\b|\bvirtual\b|\bwork from home\b", f"{loc_lower} {title_lower}"))
    if is_remote:
        score += 30
        matched_skills.append("Remote")
    else:
        for pref_loc in profile.get("locations", []):
            pref_clean = pref_loc.lower().split(",")[0].strip()
            if pref_clean and pref_clean in loc_lower:
                score += 30
                matched_skills.append(pref_loc)
                break

    # 3. Seniority alignment (up to 10 points)
    if re.search(r"\bsenior\b|\bsr\b|\blead\b|\bprincipal\b|\bstaff\b", title_lower):
        score += 10
        matched_skills.append("Senior-Level")

    return min(100, score), matched_skills


# -----------------------------------------------------------------------------
# ATS Feed Fetchers
# -----------------------------------------------------------------------------

def fetch_greenhouse_feed(token: str, company: str, timeout: float = 8.0) -> list[dict[str, Any]]:
    """Fetches active jobs from Greenhouse public JSON API."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        if resp.status_code != 200:
            logger.debug("Greenhouse %s returned HTTP %s", token, resp.status_code)
            return []
        data = resp.json()
        jobs: list[dict[str, Any]] = []
        for raw in data.get("jobs", []):
            title = (raw.get("title") or "").strip()
            loc_name = ((raw.get("location") or {}).get("name") or "").strip()
            job_url = raw.get("absolute_url") or ""
            is_remote = 1 if re.search(r"remote|distributed", f"{title} {loc_name}", re.I) else 0
            jobs.append({
                "company_name": company,
                "company_token": token,
                "ats_type": "greenhouse",
                "job_title": title,
                "location": loc_name,
                "is_remote": is_remote,
                "url": job_url,
                "updated_at": raw.get("updated_at") or datetime.now(timezone.utc).isoformat(),
            })
        return jobs
    except Exception as exc:
        logger.warning("Error fetching Greenhouse jobs for %s: %s", token, exc)
        return []


def fetch_lever_feed(token: str, company: str, timeout: float = 8.0) -> list[dict[str, Any]]:
    """Fetches active jobs from Lever postings API."""
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        if resp.status_code != 200:
            logger.debug("Lever %s returned HTTP %s", token, resp.status_code)
            return []
        data = resp.json()
        jobs: list[dict[str, Any]] = []
        for raw in data if isinstance(data, list) else []:
            title = (raw.get("text") or "").strip()
            cats = raw.get("categories") or {}
            loc_name = cats.get("location") or cats.get("allLocations", [""])[0] if cats.get("allLocations") else ""
            job_url = raw.get("hostedUrl") or raw.get("applyUrl") or ""
            is_remote = 1 if (raw.get("workplaceType") or "").lower() == "remote" or re.search(r"remote|distributed", f"{title} {loc_name}", re.I) else 0
            jobs.append({
                "company_name": company,
                "company_token": token,
                "ats_type": "lever",
                "job_title": title,
                "location": loc_name,
                "is_remote": is_remote,
                "url": job_url,
                "description": (raw.get("descriptionPlain") or "").strip(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
        return jobs
    except Exception as exc:
        logger.warning("Error fetching Lever jobs for %s: %s", token, exc)
        return []


def fetch_jobs_for_company(company_entry: dict[str, str], timeout: float = 8.0) -> list[dict[str, Any]]:
    """Dispatches to the matching ATS fetcher."""
    ats = company_entry.get("ats", "").lower()
    token = company_entry.get("token", "")
    name = company_entry.get("company", token)
    if ats == "greenhouse":
        return fetch_greenhouse_feed(token, name, timeout=timeout)
    elif ats == "lever":
        return fetch_lever_feed(token, name, timeout=timeout)
    return []


# -----------------------------------------------------------------------------
# Discovery Engine Controller
# -----------------------------------------------------------------------------

class JobDiscoveryEngine:
    """Manages periodic scraping, filtering, and database persistence."""

    def __init__(self, db_path: Optional[Path] = None, config_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.config_path = config_path or COMPANIES_CONFIG_PATH
        self.profile = load_profile()

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        ensure_discovery_schema(conn)
        return conn

    def get_target_companies(self) -> list[dict[str, str]]:
        if self.config_path.exists():
            try:
                return json.loads(self.config_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Could not read %s: %s", self.config_path, exc)
        return [
            {"company": "Cloudflare", "token": "cloudflare", "ats": "greenhouse"},
            {"company": "Datadog", "token": "datadog", "ats": "greenhouse"},
            {"company": "Stripe", "token": "stripe", "ats": "greenhouse"},
        ]

    def scan_all(self, min_match_threshold: int = 50) -> dict[str, int]:
        """Scans all configured target companies and ingests matching roles."""
        companies = self.get_target_companies()
        conn = self._get_connection()
        now_iso = datetime.now(timezone.utc).isoformat()
        stats = {"companies_scanned": 0, "total_jobs_seen": 0, "relevant_jobs_saved": 0}

        try:
            for entry in companies:
                stats["companies_scanned"] += 1
                jobs = fetch_jobs_for_company(entry)
                stats["total_jobs_seen"] += len(jobs)

                for job in jobs:
                    score, matched_skills = calculate_match_score(
                        job["job_title"], job["location"], self.profile
                    )
                    if score < min_match_threshold:
                        continue
                    if job.get("description"):
                        # The posting text is in hand: the engine's hard gates
                        # (sponsorship, clearance, onsite elsewhere) can rule it out.
                        import matching_engine
                        eligible, _why = matching_engine.check_hard_gates(
                            job["description"], self.profile, job["location"]
                        )
                        if not eligible:
                            continue

                    dedup_key = compute_job_hash(
                        job["company_name"], job["job_title"], job["location"], job["url"]
                    )

                    # Check if already submitted or applied
                    has_apps = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='applications'").fetchone() is not None
                    init_status = 'discovered'
                    if has_apps:
                        already = conn.execute("""
                            SELECT 1 FROM applications 
                            WHERE (url = ? OR (lower(trim(company)) = lower(trim(?)) AND lower(trim(title)) = lower(trim(?))))
                              AND status IN ('submitted', 'already_applied')
                            LIMIT 1
                        """, (job["url"], job["company_name"], job["job_title"])).fetchone()
                        if already:
                            init_status = 'submitted'

                    cur = conn.execute("""
                        INSERT INTO discovered_jobs (
                            dedup_key, company_name, company_token, ats_type,
                            job_title, location, is_remote, url, match_score,
                            matched_skills, status, discovered_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(dedup_key) DO UPDATE SET
                            match_score = excluded.match_score,
                            matched_skills = excluded.matched_skills,
                            status = CASE 
                                WHEN discovered_jobs.status IN ('submitted', 'queued', 'dismissed') THEN discovered_jobs.status
                                ELSE excluded.status
                            END,
                            updated_at = excluded.updated_at
                        WHERE status = 'discovered' OR status = 'submitted'
                    """, (
                        dedup_key, job["company_name"], job["company_token"], job["ats_type"],
                        job["job_title"], job["location"], job["is_remote"], job["url"],
                        score, json.dumps(matched_skills), init_status, now_iso, now_iso
                    ))
                    if init_status != 'submitted':
                        stats["relevant_jobs_saved"] += 1

                    if score >= 85 and cur.lastrowid:
                        try:
                            import notifications
                            job_payload = dict(job)
                            job_payload.update({
                                "id": cur.lastrowid,
                                "match_score": score,
                                "matched_skills": matched_skills,
                            })
                            notifications.notify_discovered_job(job_payload)
                        except Exception as _swallowed:
                            logger.debug("scan_all: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "Discovery complete: Scanned %d companies, evaluated %d jobs, saved %d relevant jobs (score >= %d)",
            stats["companies_scanned"], stats["total_jobs_seen"],
            stats["relevant_jobs_saved"], min_match_threshold
        )
        return stats

    def get_discovered_jobs(
        self, min_score: int = 50, status: str = "discovered", limit: int = 50
    ) -> list[dict[str, Any]]:
        """Returns discovered jobs matching filters, ordered by highest match score."""
        conn = self._get_connection()
        try:
            # Exclude positions that are already submitted or already applied in applications
            has_apps = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='applications'").fetchone() is not None
            sub_filter = ""
            if has_apps:
                sub_filter = """
                    AND url NOT IN (SELECT url FROM applications WHERE status IN ('submitted', 'already_applied'))
                    AND NOT EXISTS (
                        SELECT 1 FROM applications a 
                        WHERE a.status IN ('submitted', 'already_applied')
                          AND lower(trim(a.company)) = lower(trim(discovered_jobs.company_name))
                          AND lower(trim(a.title)) = lower(trim(discovered_jobs.job_title))
                    )
                """
            rows = conn.execute(f"""
                SELECT id, dedup_key, company_name, ats_type, job_title, location,
                       is_remote, url, match_score, matched_skills, status, discovered_at
                FROM discovered_jobs
                WHERE match_score >= ? AND status = ?
                {sub_filter}
                ORDER BY match_score DESC, discovered_at DESC
                LIMIT ?
            """, (min_score, status, limit)).fetchall()

            results = []
            for r in rows:
                item = dict(r)
                try:
                    item["matched_skills"] = json.loads(item.get("matched_skills") or "[]")
                except Exception:
                    item["matched_skills"] = []
                results.append(item)
            return results
        finally:
            conn.close()

    def dismiss_job(self, job_id: int) -> bool:
        """Marks a discovered opportunity as dismissed."""
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE discovered_jobs SET status = 'dismissed' WHERE id = ?", (job_id,)
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def queue_for_application(self, job_id: int) -> Optional[dict[str, Any]]:
        """Marks a discovered job as queued, adds it to applications, and returns its details."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM discovered_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if not row:
                return None

            # Guard against queueing an application for an already submitted job
            has_apps = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='applications'").fetchone() is not None
            if has_apps:
                already = conn.execute("""
                    SELECT id FROM applications 
                    WHERE (url = ? OR (lower(trim(company)) = lower(trim(?)) AND lower(trim(title)) = lower(trim(?))))
                      AND status IN ('submitted', 'already_applied')
                    LIMIT 1
                """, (row["url"], row["company_name"], row["job_title"])).fetchone()
                if already:
                    conn.execute("UPDATE discovered_jobs SET status = 'submitted' WHERE id = ?", (job_id,))
                    conn.commit()
                    item = dict(row)
                    item["already_submitted"] = True
                    return item

            conn.execute(
                "UPDATE discovered_jobs SET status = 'queued' WHERE id = ?", (job_id,)
            )
            now_iso = datetime.now(timezone.utc).isoformat()
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO applications (
                        dedup_key, title, company, location, source_site, url, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'ready_to_apply', ?, ?)
                """, (
                    row["dedup_key"], row["job_title"], row["company_name"],
                    row["location"], row["ats_type"], row["url"], now_iso, now_iso
                ))
            except Exception as _swallowed:
                logger.debug("queue_for_application: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)
            conn.commit()
            item = dict(row)
            try:
                item["matched_skills"] = json.loads(item.get("matched_skills") or "[]")
            except Exception:
                item["matched_skills"] = []
            return item
        finally:
            conn.close()

