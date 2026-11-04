"""
Autonomous ATS Discovery Engine.

Continuously discovers open positions directly from public career APIs
(Greenhouse, Lever, Ashby, Workday CXS) without browser overhead.
Applies target titles, location, and visa sponsorship criteria to score
and ingest high-fit opportunities into data/applications.db.
"""

from __future__ import annotations

import diagnostics
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import tracking
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

import discovery_sources as sources

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
    # Sales and management are not what the owner applies for: "Senior Security Sales Engineer" and "Sr Manager,
    # Infrastructure Sales" scored 70 on a tech word, "remote" and "senior" alone (26 October 2026).
    r"\bsales\b",
    r"\bmanager\b",
    r"\bdirector\b",
    r"\bvice president\b",
    r"\bvp\b",
    r"\bhead of\b",
    r"\bcustomer success\b",
    r"\bdesigner\b",
]

# What a place other than the owner's is worth when the owner is open to relocating (their own place or remote: 30).
RELOCATION_CREDIT = 15
#: Stored countries were first parsed by f312's reader (1); f318's reader (cities, two-letter states, alpha-2 codes)
#: recomputes them once, tracked by a sentinel row so the refresh runs a single time.
COUNTRY_PARSER_VERSION = 2
#: Jobs at least this good get their application form pre-read during the scan (the same bar that notifies);
#: the rest offer the check on their card instead.
READINESS_SCORE = 85


def assess_job_readiness(job: dict, user_prof) -> str:
    """Readiness assessment JSON for a Greenhouse job, or "" when it cannot be pre-read (another system, no job
    id in the address, no published form, or no profile to answer from). Never raises: readiness must not fail
    a scan."""
    try:
        import readiness
        if job.get("ats_type") != "greenhouse" or not user_prof:
            return ""
        job_id = readiness.greenhouse_job_id(job.get("url") or "")
        if not job_id:
            return ""
        questions = readiness.fetch_greenhouse_questions(job.get("company_token") or "", job_id)
        if not questions:
            return ""
        return json.dumps(readiness.assess(questions, user_prof, job.get("company_name") or ""))
    except Exception as exc:
        logger.debug("No readiness for %s: %s", job.get("url"), diagnostics.short_exc(exc, 100))
        return ""


def refresh_readiness(job_id: int, db_path=None):
    """Assesses one stored job's form on demand (the card's button) and stores it; the assessment, or None when
    the posting cannot be pre-read."""
    import readiness
    path = Path(db_path) if db_path is not None else DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    ensure_discovery_schema(conn)
    try:
        row = conn.execute("SELECT * FROM discovered_jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        try:
            import config
            user_prof = config.get_user_profile()
        except Exception as _swallowed:
            logger.debug("readiness profile: %s", diagnostics.short_exc(_swallowed, 100))
            return None
        text = assess_job_readiness(dict(row), user_prof)
        if not text:
            return None
        conn.execute("UPDATE discovered_jobs SET readiness_json = ? WHERE id = ?", (text, job_id))
        conn.commit()
        return json.loads(text)
    finally:
        conn.close()
#: The scan keeps anything this good even when the caller asked for more: suggestions filter at the display cutoff
#: at read time, and the band between the floor and the cutoff is the near-miss list.
STORE_FLOOR = 30


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
    existing = {row[1] for row in conn.execute("PRAGMA table_info(discovered_jobs)")}
    for column, kind in (("resume_score", "INTEGER"), ("resume_matched", "TEXT DEFAULT '[]'"),
                         ("resume_missing", "TEXT DEFAULT '[]'"),
                         ("posted_at", "TEXT"), ("posted_precision", "TEXT"),
                         ("country_code", "TEXT"), ("readiness_json", "TEXT")):
        if column not in existing:
            conn.execute(f"ALTER TABLE discovered_jobs ADD COLUMN {column} {kind}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_discovered_status ON discovered_jobs(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_discovered_score ON discovered_jobs(match_score DESC)"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS board_state (
            identity TEXT PRIMARY KEY, ats TEXT, token TEXT,
            last_scan TEXT, last_error TEXT, jobs_seen INTEGER)"""
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(discovered_jobs)")}
    if "country_code" in columns:
        missing = conn.execute(
            "SELECT rowid, location FROM discovered_jobs WHERE country_code IS NULL").fetchall()
        if missing:
            import geo_reference
            for rowid, location in missing:
                conn.execute("UPDATE discovered_jobs SET country_code = ? WHERE rowid = ?",
                             (geo_reference.primary_country(location or ""), rowid))
        version = conn.execute(
            "SELECT jobs_seen FROM board_state WHERE identity = '__country_parser__'").fetchone()
        if (version[0] if version else 0) < COUNTRY_PARSER_VERSION:
            import geo_reference
            for rowid, location, stored in conn.execute(
                    "SELECT rowid, location, country_code FROM discovered_jobs"):
                named = geo_reference.location_countries(location or "")
                if (stored or "") not in named:
                    fresh = next(iter(named)) if len(named) == 1 else ""
                    conn.execute("UPDATE discovered_jobs SET country_code = ? WHERE rowid = ?",
                                 (fresh, rowid))
            conn.execute(
                "INSERT INTO board_state (identity, ats, token, last_scan, last_error, jobs_seen)"
                " VALUES ('__country_parser__', '', '', '', '', ?)"
                " ON CONFLICT(identity) DO UPDATE SET jobs_seen = excluded.jobs_seen",
                (COUNTRY_PARSER_VERSION,))
    conn.commit()


def html_to_text(raw: str) -> str:
    """A board's posting text as plain words. Greenhouse sends its HTML escaped, so it is unescaped first."""
    import html as _html
    text = _html.unescape(str(raw or ""))
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return " ".join(_html.unescape(text).split())


_RESUME_CACHE: dict = {}


def load_resume_text(path=None) -> str:
    """The owner's resume as text, for scoring a posting against it. Empty when there is no readable resume."""
    try:
        if path is None:
            import config
            path = config.get_app_config().resume_path
        target = Path(path)
        stat = target.stat()
        key = (str(target), stat.st_mtime, stat.st_size)
        if key in _RESUME_CACHE:
            return _RESUME_CACHE[key]
        if target.suffix.lower() == ".pdf":
            import pypdf
            text = " ".join((page.extract_text() or "") for page in pypdf.PdfReader(str(target)).pages)
        else:
            text = target.read_text(encoding="utf-8", errors="ignore")
        text = " ".join(text.split())
        _RESUME_CACHE.clear()
        _RESUME_CACHE[key] = text
        return text
    except Exception as _swallowed:
        logger.debug("load_resume_text: %s", diagnostics.short_exc(_swallowed, 100))
        return ""


def compute_job_hash(company: str, title: str, location: str, url: str) -> str:
    """Generates a stable SHA256 hex digest for deduplication."""
    clean_url = url.split("?")[0].rstrip("/")
    norm = f"{company.strip().lower()}|{title.strip().lower()}|{location.strip().lower()}|{clean_url.strip().lower()}"
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _home_country(name: str) -> str:
    """The country code of the owner's country as the profile spells it, or "" when it is not known."""
    try:
        import geo_reference
        return geo_reference.country_code(name or "") or ""
    except Exception as _swallowed:
        logger.debug("_home_country: %s", diagnostics.short_exc(_swallowed, 100))
        return ""


def _accepted_countries(home_name: str, available_places: str) -> list[str]:
    """Every country the owner can work in: home first, then each available place that resolves -- a code, a
    country name, or a known city ("CA", "Canada", "Toronto"). Unresolvable extras are skipped, never guessed."""
    import geo_reference
    accepted = []
    home = _home_country(home_name)
    if home:
        accepted.append(home)
    for chunk in re.split(r"[,;]", available_places or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        code = geo_reference.country_code(chunk) or geo_reference.city_country(chunk)
        if code and code not in accepted:
            accepted.append(code)
    return accepted


def _in_reach_anywhere(location: str, accepted) -> bool:
    """False when the location names countries and none is accepted; True when it names none or nothing is set."""
    if not accepted:
        return True
    import geo_reference
    named = geo_reference.location_countries(location)
    return not named or bool(named & set(accepted))


def _in_reach(location: str, home: str) -> bool:
    """False when the location names countries and the owner's is not among them ("Remote - India", "Bengaluru, India"
    for someone living elsewhere); True when it names none ("Remote", "Palo Alto") or no home country is known.
    A US state names the US ("Austin, TX" is in reach for a US owner)."""
    if not home:
        return True
    import geo_reference
    named = geo_reference.location_countries(location)
    return not named or home in named


def load_profile() -> dict[str, Any]:
    """Loads candidate profile preferences and targets."""
    import config
    try:
        user_prof = config.get_user_profile()
        return {
            # The person's own titles (profile_setup drafts them from the resume). With none there is nothing to
            # match, so nothing is suggested -- no one's job titles are built into the code.
            "target_titles": list(getattr(user_prof, "target_titles", ()) or ()),
            "avoid_title_words": list(getattr(user_prof, "avoid_title_words", ()) or ()),
            "locations": getattr(user_prof, "locations", []) or [
                "Dallas, TX", "Austin, TX", "Irving, TX", "Plano, TX", "Ashburn, VA", "Remote"
            ],
            "requires_visa_sponsorship": getattr(user_prof, "requires_visa_sponsorship", True),
            # Where the owner lives, from the profile: "Remote - India" is not remote work for someone living elsewhere.
            "country_code": _home_country(getattr(user_prof, "country", "")),
            # Every country the owner can work in: home plus the profile's extras ("Other countries you can work in").
            "accepted_country_codes": _accepted_countries(getattr(user_prof, "country", ""),
                                                          getattr(user_prof, "available_work_locations", "")),
            # Whether the owner will move for a job: a place other than theirs then still counts, a little less.
            "open_to_relocation": bool(getattr(user_prof, "open_to_relocation", False)),
        }
    except Exception:
        return {
            "target_titles": [],
            "avoid_title_words": [],
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


# Disciplines that are a different career from most people's: a title naming one is left out unless the person's own
# titles name it too (a software engineer's "Software Engineer" is theirs; a network engineer's is not -- "Software
# Engineer, Starlink Network" matched "Network Engineer" on two words).
OTHER_DISCIPLINES = (
    "software", "backend", "back-end", "frontend", "front-end", "full stack", "full-stack", "mobile", "ios", "android",
    "web developer", "data engineer", "data scientist", "data analyst", "machine learning", "ml", "research scientist",
    "qa", "quality assurance", "game", "firmware", "hardware", "mechanical", "technical writer", "solutions architect",
)


def _words_in(title: str, words) -> list[str]:
    return [w for w in words if w and re.search(rf"(?<![\w-]){re.escape(w.lower())}(?![\w-])", title.lower())]


def is_other_discipline(title: str, profile: dict[str, Any]) -> bool:
    """True when the title names a discipline or a word the person does not want.

    A person whose own titles name a discipline (a software engineer, a data engineer) works in that field, so the
    built-in list does not apply to them; the words they say they avoid always do, unless their own titles use them."""
    own = " ".join(profile.get("target_titles") or [])
    disciplines = () if _words_in(own, OTHER_DISCIPLINES) else OTHER_DISCIPLINES
    named = _words_in(title, (*disciplines, *(profile.get("avoid_title_words") or ())))
    return any(not _words_in(own, [w]) for w in named)


def calculate_match_score(
    title: str, location: str, profile: dict[str, Any]
) -> tuple[int, list[str]]:
    """
    Computes candidate match score (0-100) based on title relevance, location,
    and engineering keywords.
    """
    if is_negative_title(title) or is_other_discipline(title, profile):
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

    # No title of the person's: not a suggestion. (A loose "engineer plus a technical word" match used to let
    # "Staff Application Security Engineer" and "Senior Software Engineer, Backend" score 60 on a network profile.)
    if score == 0:
        return 0, []

    # 2. Location match (up to 30 points)
    is_remote = bool(re.search(r"\bremote\b|\banywhere\b|\bvirtual\b|\bwork from home\b", f"{loc_lower} {title_lower}"))
    accepted = [c for c in (profile.get("accepted_country_codes") or [profile.get("country_code", "")]) if c]
    if not _in_reach_anywhere(location, accepted):
        return 0, []            # a posting in another country is not a suggestion, remote or not
    if is_remote:
        score += 30
        matched_skills.append("Remote")
    else:
        placed = False
        for pref_loc in profile.get("locations", []):
            pref_clean = pref_loc.lower().split(",")[0].strip()
            if re.fullmatch(r"remote|anywhere|virtual", pref_clean):
                continue            # "Remote" is the remote rule above, which knows which country it is remote in
            if pref_clean and pref_clean in loc_lower:
                score += 30
                matched_skills.append(pref_loc)
                placed = True
                break
        if not placed and profile.get("open_to_relocation"):
            # Place is a bonus, not a gate, for someone who will move: a wanted title in another place in their own
            # country counts (a partial title match, 50, reaches the 60 cut-off), though less than their own place or
            # remote. A loose match (a tech word alone, 20) still does not.
            score += RELOCATION_CREDIT
            matched_skills.append("Open to relocation")

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
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    try:
        resp = sources.http_get(url, headers=DEFAULT_HEADERS, timeout=timeout)
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
                "description": html_to_text(raw.get("content") or ""),
                "updated_at": raw.get("updated_at") or datetime.now(timezone.utc).isoformat(),
                # When it was first published, not when it was last edited: an old posting touched up yesterday
                # is not a new job.
                **sources._stamp({}, sources.parse_posted(raw.get("first_published"))),
            })
        return jobs
    except Exception as exc:
        logger.warning("Error fetching Greenhouse jobs for %s: %s", token, exc)
        return []


def fetch_lever_feed(token: str, company: str, timeout: float = 8.0) -> list[dict[str, Any]]:
    """Fetches active jobs from Lever postings API."""
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    try:
        resp = sources.http_get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        if resp.status_code == 404:
            # EU-hosted boards live on another host and 404 on the main one.
            url = url.replace("api.lever.co", "api.eu.lever.co")
            resp = sources.http_get(url, headers=DEFAULT_HEADERS, timeout=timeout)
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
                **sources._stamp({}, sources.parse_posted(raw.get("createdAt"))),
            })
        return jobs
    except Exception as exc:
        logger.warning("Error fetching Lever jobs for %s: %s", token, exc)
        return []


def fetch_jobs_for_company(company_entry: dict[str, str], timeout: float = 8.0) -> list[dict[str, Any]]:
    """Dispatches to the matching ATS fetcher. The entry's "titles" are the roles wanted: the systems that search by
    title (Workday, iCIMS) are asked for each."""
    ats = company_entry.get("ats", "").lower()
    token = company_entry.get("token", "")
    name = company_entry.get("company", token)
    wanted = list(company_entry.get("titles") or ["Network Engineer"])
    if ats == "greenhouse":
        return fetch_greenhouse_feed(token, name, timeout=timeout)
    elif ats == "lever":
        return fetch_lever_feed(token, name, timeout=timeout)
    elif ats == "ashby":
        return sources.fetch_ashby_feed(token, name, timeout=timeout)
    elif ats == "workday":
        return sources.fetch_workday_feed(company_entry, wanted)
    elif ats == "icims":
        return sources.fetch_icims_feed(company_entry, wanted)
    elif ats == "smartrecruiters":
        return sources.fetch_smartrecruiters_feed(token, name, titles=wanted, timeout=timeout)
    elif ats == "workable":
        return sources.fetch_workable_feed(token, name, timeout=timeout)
    return []


# -----------------------------------------------------------------------------
# Discovery Engine Controller
# -----------------------------------------------------------------------------

def dismiss_discovered_job(job_id: int, db_path=None) -> bool:
    """Marks one suggestion dismissed with one UPDATE and no engine: no profile, no resume (the dashboard's
    Pass button answers at once instead of paying a full engine construction for a single row)."""
    path = Path(db_path) if db_path is not None else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    ensure_discovery_schema(conn)
    try:
        cur = conn.execute("UPDATE discovered_jobs SET status = 'dismissed' WHERE id = ?", (job_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _submitted_cover(tracker) -> tuple[set, set]:
    """The postings the live tracker already holds a submitted application for: urls, and company/title pairs."""
    urls, pairs = set(), set()
    for app in tracker.list_all():
        if getattr(app, "status", "") not in ("submitted", "already_applied"):
            continue
        if (getattr(app, "url", "") or "").strip():
            urls.add((getattr(app, "url", "") or "").strip())
        pairs.add((str(getattr(app, "company", "") or "").strip().lower(),
                   str(getattr(app, "title", "") or "").strip().lower()))
    return urls, pairs


def _covered_by(url, company, title, cover) -> bool:
    """Whether a posting falls under a submitted cover: same url, or same company and title."""
    urls, pairs = cover
    if (url or "").strip() and (url or "").strip() in urls:
        return True
    return (str(company or "").strip().lower(), str(title or "").strip().lower()) in pairs


def _ingest_status(job, cover) -> str:
    """A scanned job's initial feed status: 'submitted' when the live tracker already covers it."""
    if _covered_by(job.get("url"), job.get("company_name"), job.get("job_title"), cover):
        return "submitted"
    return "discovered"


def _live_submitted(tracker, url, company, title) -> bool:
    """Whether the live tracker already holds a submitted application for this posting."""
    return _covered_by(url, company, title, _submitted_cover(tracker))


def queue_discovered_job(job_id, db_path=None):
    """Marks one suggestion queued and registers its application row: no profile, no resume.

    Returns the job's details (with ``already_submitted`` when a submitted application already covers it),
    or None when the id is not listed. The dashboard's Apply button and bulk Queue share this; the engine
    method below delegates to it so the notification path queues identically.

    The suggestion feed lives in SQLite, but the guard and the registration go to the live tracker
    (tracking.open_tracker): runs and the dashboard read only the live copy, so a row registered in the
    feed file's shadow applications table would be re-queued after submitting and never shown.
    """
    path = Path(db_path) if db_path is not None else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    ensure_discovery_schema(conn)
    try:
        row = conn.execute("SELECT * FROM discovered_jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        tracker = tracking.open_tracker(path)
        if _live_submitted(tracker, row["url"], row["company_name"], row["job_title"]):
            conn.execute("UPDATE discovered_jobs SET status = 'submitted' WHERE id = ?", (job_id,))
            conn.commit()
            item = dict(row)
            item["already_submitted"] = True
            return item
        conn.execute("UPDATE discovered_jobs SET status = 'queued' WHERE id = ?", (job_id,))
        conn.commit()
        # The row carries the run's own key (sha256 of the URL, apply_flow:2006): a discovered-hash key
        # would leave every handoff, note and checkpoint the run writes under a key with no row.
        from jd_analyzer import dedup_key_for_url
        app_key = dedup_key_for_url(row["url"]) if row["url"] else row["dedup_key"]
        tracker.create(dedup_key=app_key, title=row["job_title"] or "", company=row["company_name"] or "",
                       location=row["location"] or "", source_site=row["ats_type"] or "", url=row["url"] or "")
        current = tracker.get(app_key)
        # create() leaves an existing row's status alone; only a freshly prepared row becomes queued --
        # re-queueing an application the owner is already reviewing must not drag it back.
        if current is not None and (current.status or "") == "prepared":
            tracker.update_status(app_key, "ready_to_apply")
        item = dict(row)
        try:
            item["matched_skills"] = json.loads(item.get("matched_skills") or "[]")
        except Exception:
            item["matched_skills"] = []
        return item
    finally:
        conn.close()


def get_discovered_countries(db_path=None) -> list[str]:
    """ISO codes of the countries naming live suggestions, sorted: the filter row lists only these.

    Only suggestions at the feed's default floor (60+) count, so every option can show a card.
    """
    path = Path(db_path) if db_path is not None else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    ensure_discovery_schema(conn)
    try:
        rows = conn.execute(
            "SELECT DISTINCT country_code FROM discovered_jobs"
            " WHERE status = 'discovered' AND match_score >= 60"
            " AND country_code IS NOT NULL AND country_code <> ''"
            " ORDER BY country_code"
        ).fetchall()
        return [row[0] for row in rows if row[0]]
    finally:
        conn.close()

def _materialize_rows(rows) -> list:
    """Row dicts with their skill lists parsed; unparseable lists read as empty."""
    results = []
    for r in rows:
        item = dict(r)
        try:
            item["matched_skills"] = json.loads(item.get("matched_skills") or "[]")
        except Exception:
            item["matched_skills"] = []
        for key in ("resume_matched", "resume_missing"):
            try:
                item[key] = json.loads(item.get(key) or "[]")
            except Exception:
                item[key] = []
        results.append(item)
    return results

class JobDiscoveryEngine:
    """Manages periodic scraping, filtering, and database persistence."""

    resume_text = ""      # the owner's resume as text, for scoring postings against it; __init__ reads it
    history: tuple = ()   # (company, address) of each application already made: those employers are scanned too

    def __init__(self, db_path: Optional[Path] = None, config_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.config_path = config_path or COMPANIES_CONFIG_PATH
        self.profile = load_profile()
        self.resume_text = load_resume_text()

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        ensure_discovery_schema(conn)
        return conn

    @property
    def _live_tracker(self):
        """The applications tracker runs and the dashboard read (lazily opened: tests point it at a temp file)."""
        tracker = getattr(self, "_tracker", None)
        if tracker is None:
            tracker = tracking.open_tracker(self.db_path)
            self._tracker = tracker
        return tracker

    def get_target_companies(self) -> list[dict[str, str]]:
        """The owner's own list, then the multinational employers the project ships with (reference/employers.json),
        then every employer an application was already made through (Workday, Ashby, iCIMS, Greenhouse and Lever
        ones: the systems whose lists can be read), each once."""
        listed = [
            {"company": "Cloudflare", "token": "cloudflare", "ats": "greenhouse"},
            {"company": "Datadog", "token": "datadog", "ats": "greenhouse"},
            {"company": "Stripe", "token": "stripe", "ats": "greenhouse"},
        ]
        if self.config_path.exists():
            try:
                listed = json.loads(self.config_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Could not read %s: %s", self.config_path, exc)
        return sources.merge_companies(listed, sources.reference_employers(),
                                       sources.companies_from_history(self.history))

    def scan_all(self, min_match_threshold: int = 50) -> dict[str, int]:
        """Scans all configured target companies and ingests matching roles."""
        companies = self.get_target_companies()
        conn = self._get_connection()
        now_iso = datetime.now(timezone.utc).isoformat()
        stats = {"companies_scanned": 0, "total_jobs_seen": 0, "relevant_jobs_saved": 0, "expired": 0,
                 "boards_failed": 0}
        still_listed: dict[str, set[str]] = {}      # board token -> keys that listed and qualified in this scan
        # What the live tracker already holds submitted, read once: a scanned job it covers is stored
        # 'submitted', never re-offered. The feed file's own shadow applications table is not consulted.
        submitted_cover = _submitted_cover(self._live_tracker)

        titles = list(self.profile.get("target_titles") or [])
        home_country = self.profile.get("country_code", "") or ""
        store_at = min(min_match_threshold, STORE_FLOOR) if min_match_threshold > 0 else 0
        user_prof = None      # the answering profile, loaded once, only when a top job needs its form read
        # Every employer's list is read at the same time (each is a separate site); the rows are then saved one by one.
        from concurrent.futures import ThreadPoolExecutor

        def read(entry):
            try:
                return fetch_jobs_for_company({**entry, "titles": titles}), ""
            except Exception as exc:
                short = diagnostics.short_exc(exc, 100)
                logger.warning("Could not read %s: %s", entry.get("company"), short)
                return [], short
        with ThreadPoolExecutor(max_workers=16) as pool:
            listings = list(pool.map(read, companies))

        try:
            import geo_reference
            for entry, (jobs, error) in zip(companies, listings):
                stats["companies_scanned"] += 1
                stats["total_jobs_seen"] += len(jobs)
                if error:
                    stats["boards_failed"] += 1
                conn.execute(
                    "INSERT INTO board_state (identity, ats, token, last_scan, last_error, jobs_seen)"
                    " VALUES (?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(identity) DO UPDATE SET last_scan = excluded.last_scan,"
                    " last_error = excluded.last_error, jobs_seen = excluded.jobs_seen",
                    ("|".join(sources._identity(entry)), entry.get("ats"),
                     entry.get("token") or entry.get("tenant") or entry.get("host") or "",
                     now_iso, error, len(jobs)))
                if jobs:        # a board that failed to load (empty list) says nothing about its old rows
                    still_listed.setdefault(entry.get("token") or entry.get("tenant") or entry.get("host") or "", set())

                for job in jobs:
                    score, matched_skills = calculate_match_score(
                        job["job_title"], job["location"], self.profile
                    )
                    if score < store_at:
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

                    resume_score, resume_matched, resume_missing = None, [], []
                    if job.get("description") and self.resume_text:
                        import matching_engine
                        fit = matching_engine.evaluate(job["description"], self.profile, self.resume_text,
                                                       job_location=job["location"])
                        if fit.is_eligible:
                            resume_score = fit.overall_score
                            resume_matched, resume_missing = list(fit.matched_skills), list(fit.missing_skills)

                    job["country_code"] = geo_reference.primary_country(job["location"], home_country)
                    if score >= READINESS_SCORE and "readiness_json" not in job:
                        if user_prof is None:
                            try:
                                import config
                                user_prof = config.get_user_profile()
                            except Exception as _swallowed:
                                logger.debug("scan readiness profile: %s", diagnostics.short_exc(_swallowed, 100))
                                user_prof = False
                        if user_prof:
                            job["readiness_json"] = assess_job_readiness(job, user_prof)
                    dedup_key = compute_job_hash(
                        job["company_name"], job["job_title"], job["location"], job["url"]
                    )
                    still_listed.setdefault(job["company_token"], set()).add(dedup_key)

                    init_status = _ingest_status(job, submitted_cover)

                    cur = conn.execute("""
                        INSERT INTO discovered_jobs (
                            dedup_key, company_name, company_token, ats_type,
                            job_title, location, is_remote, url, match_score,
                            matched_skills, status, discovered_at, updated_at,
                            resume_score, resume_matched, resume_missing, posted_at, posted_precision,
                            country_code, readiness_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(dedup_key) DO UPDATE SET
                            match_score = excluded.match_score,
                            matched_skills = excluded.matched_skills,
                            resume_score = excluded.resume_score,
                            resume_matched = excluded.resume_matched,
                            resume_missing = excluded.resume_missing,
                            country_code = excluded.country_code,
                            readiness_json = CASE WHEN excluded.readiness_json != ''
                                THEN excluded.readiness_json ELSE readiness_json END,
                            posted_at = CASE WHEN excluded.posted_at != '' THEN excluded.posted_at
                                             ELSE discovered_jobs.posted_at END,
                            posted_precision = CASE WHEN excluded.posted_at != '' THEN excluded.posted_precision
                                                    ELSE discovered_jobs.posted_precision END,
                            status = CASE 
                                WHEN discovered_jobs.status IN ('submitted', 'queued', 'dismissed') THEN discovered_jobs.status
                                ELSE excluded.status
                            END,
                            updated_at = excluded.updated_at
                        WHERE status = 'discovered' OR status = 'submitted'
                    """, (
                        dedup_key, job["company_name"], job["company_token"], job["ats_type"],
                        job["job_title"], job["location"], job["is_remote"], job["url"],
                        score, json.dumps(matched_skills), init_status, now_iso, now_iso,
                        resume_score, json.dumps(resume_matched), json.dumps(resume_missing),
                        job.get("posted_at") or "", job.get("posted_precision") or "",
                        job.get("country_code") or "", job.get("readiness_json") or ""
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
                            logger.debug("scan_all: %s", diagnostics.short_exc(_swallowed, 100))
            # A suggestion the board no longer lists, or that the rules no longer pass, would keep its old score and be
            # shown on every refresh. Only suggestions still waiting for the owner expire; queued, submitted and
            # dismissed ones are theirs.
            for token, keys in still_listed.items():
                if not token:
                    continue
                marks = ",".join("?" * len(keys)) or "''"
                cur = conn.execute(
                    f"UPDATE discovered_jobs SET status = 'expired' WHERE company_token = ? AND status = 'discovered' "
                    f"AND dedup_key NOT IN ({marks})", (token, *keys))
                stats["expired"] += cur.rowcount
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
        self, min_score: int = 50, status: str = "discovered", limit: int = 50, hide_applied: bool = True,
        within_hours: Optional[float] = None, country_code: Optional[str] = None,
        company_like: Optional[str] = None, offset: int = 0,
        remote_only: Optional[bool] = None,
    ) -> list[dict[str, Any]]:
        """Returns discovered jobs matching filters, ordered by highest match score -- or, with `within_hours`, only
        the jobs certainly posted inside that window (a job with no known posting date is never inside it), newest
        first. With `country_code`, only jobs whose location named that country (a job whose location named no
        country is not fetched for any country)."""
        conn = self._get_connection()
        try:
            # Already-submitted postings hide by the LIVE tracker's cover, filtered in Python after the
            # query (like within_hours): the feed file's shadow applications table is not consulted.
            hide_cover = _submitted_cover(self._live_tracker) if hide_applied else None
            country_filter, query_params = "", [min_score, status]
            if country_code:
                country_filter = "AND country_code = ?"
                query_params.append(country_code)
            company_filter = ""
            if company_like:
                company_filter = "AND company_name LIKE ? ESCAPE '\\'"
                escaped = str(company_like).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                query_params.append(f"%{escaped}%")
            remote_filter = ""
            if remote_only is True:
                remote_filter = "AND is_remote = 1"
            elif remote_only is False:
                remote_filter = "AND is_remote = 0"
            unbounded = within_hours is not None or hide_cover is not None
            query_params.extend([-1 if unbounded else limit, 0 if hide_cover is not None else offset])
            rows = conn.execute(f"""
                SELECT id, dedup_key, company_name, ats_type, job_title, location,
                       is_remote, url, match_score, matched_skills, status, discovered_at,
                       resume_score, resume_matched, resume_missing, posted_at, posted_precision,
                       country_code, readiness_json
                FROM discovered_jobs
                WHERE match_score >= ? AND status = ?
                {country_filter}
                {company_filter}
                {remote_filter}
                ORDER BY (resume_score IS NULL), resume_score DESC, match_score DESC, discovered_at DESC
                LIMIT ? OFFSET ?
            """, query_params).fetchall()
            if hide_cover is not None:
                rows = [r for r in rows
                        if not _covered_by(r["url"], r["company_name"], r["job_title"], hide_cover)]
                if within_hours is None:
                    rows = rows[offset:offset + limit]

            if within_hours is not None:
                # Only jobs certainly posted inside the window, newest first: a job with no known date is never in it.
                kept = []
                for r in rows:
                    posted = sources.parse_posted(r["posted_at"])
                    if posted is not None and r["posted_precision"] == "day":
                        posted = sources.Posted(posted.at, "day")
                    if sources.within(posted, within_hours):
                        kept.append((posted.at, r))
                kept.sort(key=lambda pair: pair[0], reverse=True)
                rows = [r for _at, r in kept][:limit]

            return _materialize_rows(rows)
        finally:
            conn.close()

    def get_near_misses(self, display_threshold: int = 60, limit: int = 20,
                      country_code: Optional[str] = None) -> list[dict[str, Any]]:
        """Stored jobs below the display cutoff but worth a glance (STORE_FLOOR and up): the band the suggestions
        hide, best first."""
        conn = self._get_connection()
        try:
            params: list = [display_threshold, limit]
            country_filter = ""
            if country_code:
                country_filter = "AND country_code = ?"
                params.insert(1, country_code)
            rows = conn.execute(f"""
                SELECT id, dedup_key, company_name, ats_type, job_title, location,
                       is_remote, url, match_score, matched_skills, status, discovered_at,
                       resume_score, resume_matched, resume_missing, posted_at, posted_precision,
                       country_code, readiness_json
                FROM discovered_jobs
                WHERE match_score >= {STORE_FLOOR} AND match_score < ? AND status = 'discovered'
                {country_filter}
                ORDER BY match_score DESC, discovered_at DESC
                LIMIT ?
            """, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def dismiss_job(self, job_id: int) -> bool:
        """Marks a discovered opportunity as dismissed."""
        return dismiss_discovered_job(job_id, self.db_path)

    def queue_for_application(self, job_id: int) -> Optional[dict[str, Any]]:
        """Marks a discovered job as queued, adds it to applications, and returns its details."""
        return queue_discovered_job(job_id, self.db_path)

