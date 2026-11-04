"""How a suggested job is shown on the dashboard.

Display only: nothing here changes a job, a tracker record or a decision. The tracker is read, never written.
"""
from __future__ import annotations

import diagnostics
import json
import logging
import math
import re
from datetime import datetime, timezone
from typing import Iterable, Optional

import readiness

logger = logging.getLogger(__name__)

NEW_WITHIN_DAYS = 3
_SPLIT_LOCATIONS = re.compile(r"\s*(?:;|\||\s/\s|\sor\s)\s*", re.IGNORECASE)
_REMOTE_ONLY = re.compile(r"^\s*(?:fully\s+)?remote(?:\s*[-,(].*)?\s*$", re.IGNORECASE)


def _key(text) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _url_key(url) -> str:
    """A posting's address without its tracking noise: scheme, www, query, fragment and a trailing slash."""
    text = re.sub(r"^https?://(?:www\.)?", "", str(url or "").strip().lower())
    return re.split(r"[?#]", text, maxsplit=1)[0].rstrip("/")


def drop_handled(jobs: Iterable[dict], applications: Iterable) -> list[dict]:
    """The jobs the owner has not dealt with: any job with a record in the history -- sent, skipped, disqualified, in
    progress or failed -- is left out, matched by posting address or by company and title (2 November 2026: "if they
    are already applied or rejected they should not show"). The tracker is only read."""
    records = list(applications)
    urls = {_url_key(getattr(a, "url", "")) for a in records if getattr(a, "url", "")}
    pairs = {(_key(a.company), _key(a.title)) for a in records if getattr(a, "company", "") and getattr(a, "title", "")}
    return [dict(job) for job in jobs
            if _url_key(job.get("url")) not in urls
            and (_key(job.get("company_name")), _key(job.get("job_title"))) not in pairs]


def _parsed(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def posting_age(discovered_at, now: Optional[datetime] = None) -> tuple[str, bool]:
    """('3 days ago', False) -- how long ago we found it, and whether that is recent enough to call it new."""
    moment = _parsed(discovered_at)
    if moment is None:
        return "", False
    days = max(0, ((now or datetime.now(timezone.utc)) - moment).days)
    label = "today" if days == 0 else "yesterday" if days == 1 else f"{days} days ago"
    return f"found {label}", days < NEW_WITHIN_DAYS


def posted_label(posted_at, precision, now: Optional[datetime] = None) -> str:
    """How long ago a job was posted, saying only as much as the system dated it: an exact time reads "posted 5 hours
    ago"; a day-level date ("Posted 3 Days Ago") reads "posted within the last 4 days" and never claims an hour."""
    moment = _parsed(posted_at) if posted_at else None
    if moment is None:
        return ""
    hours = max(0.0, ((now or datetime.now(timezone.utc)) - moment).total_seconds() / 3600)
    if precision == "day":
        days = max(1, math.ceil(hours / 24))
        return "posted within the last day" if days == 1 else f"posted within the last {days} days"
    if hours < 0.05:
        return "posted just now"
    if hours < 1:
        return f"posted {round(hours * 60)} minutes ago"
    if hours < 2:
        return "posted 1 hour ago"
    if hours < 24:
        return f"posted {int(hours)} hours ago"
    if hours < 48:
        return "posted yesterday"
    return f"posted {int(hours // 24)} days ago"


def _day(moment) -> str:
    return f"{moment.day} {moment.strftime('%b %Y')}"


def age_title(discovered_at, posted_at) -> str:
    """The card's age tooltip: the day the scan found the job and, when the site dated it, the day it was
    posted.
    """
    found = _parsed(discovered_at) if discovered_at else None
    posted = _parsed(posted_at) if posted_at else None
    if found is None and posted is None:
        return ""
    if found is not None and posted is not None and found.date() == posted.date():
        return f"Found {_day(found)}"
    bits = []
    if found is not None:
        bits.append(f"Found {_day(found)}")
    if posted is not None:
        bits.append(f"Posted {_day(posted)}")
    return " \u00b7 ".join(bits)


def short_location(location, is_remote=False) -> tuple[str, int]:
    """(first place, how many more) for a long list; a bare 'Remote' entry is dropped when the Remote tag shows."""
    places = [p for p in _SPLIT_LOCATIONS.split(str(location or "")) if p.strip()]
    if is_remote:
        places = [p for p in places if not _REMOTE_ONLY.match(p)]
    if not places:
        return "", 0
    return places[0].strip(), max(0, len(places) - 1)


def _one_per_role(jobs: Iterable[dict]) -> list[dict]:
    """The same role open in several places is one card (the best scored opening) with a count of the others."""
    best: dict = {}
    counts: dict = {}
    for job in jobs:
        key = (_key(job.get("company_name")), _key(job.get("job_title")))
        counts[key] = counts.get(key, 0) + 1
        rank = (job.get("resume_score") if job.get("resume_score") is not None else -1, job.get("match_score") or 0)
        if key not in best or rank > best[key][0]:
            best[key] = (rank, job)
    out = []
    for key, (_rank, job) in best.items():
        job = dict(job)
        job["other_openings"] = counts[key] - 1
        out.append(job)
    return out


def readiness_line(stored) -> Optional[str]:
    """The card's readiness line from the stored assessment, or None when unchecked."""
    if not stored:
        return None
    try:
        return readiness.summarize(json.loads(stored))
    except (ValueError, TypeError) as _swallowed:
        logger.debug("readiness_line: %s", diagnostics.short_exc(_swallowed, 100))
        return None


def prepare(jobs: Iterable[dict], applications: Iterable, now: Optional[datetime] = None,
            newest_first: bool = False) -> list[dict]:
    """The suggested jobs as the page shows them: nothing the owner has already dealt with, one card per role, the best
    match first -- or, with `newest_first`, the most recently posted first."""
    shown = []
    for job in _one_per_role(drop_handled(jobs, applications)):
        job.setdefault("resume_score", None)
        job.setdefault("resume_matched", [])
        job.setdefault("resume_missing", [])
        found, job["is_new"] = posting_age(job.get("discovered_at"), now)
        job["age"] = posted_label(job.get("posted_at"), job.get("posted_precision"), now) or found
        job["age_title"] = age_title(job.get("discovered_at"), job.get("posted_at"))
        job["place"], job["more_places"] = short_location(job.get("location"), job.get("is_remote"))
        job["readiness"] = readiness_line(job.get("readiness_json"))
        shown.append(job)
    if newest_first:
        shown.sort(key=lambda j: str(j.get("posted_at") or ""), reverse=True)
    else:
        shown.sort(key=lambda j: str(j.get("discovered_at") or ""), reverse=True)
        shown.sort(key=lambda j: -(j.get("match_score") or 0))
    return shown
