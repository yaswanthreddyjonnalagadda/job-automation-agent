"""How a suggested job is shown on the dashboard.

Display only: nothing here changes a job, a tracker record or a decision. The tracker is read, never written.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable, Optional

NEW_WITHIN_DAYS = 3
_SPLIT_LOCATIONS = re.compile(r"\s*(?:;|\||\s/\s|\sor\s)\s*", re.IGNORECASE)
_REMOTE_ONLY = re.compile(r"^\s*(?:fully\s+)?remote(?:\s*[-,(].*)?\s*$", re.IGNORECASE)


def _key(text) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _url_key(url) -> str:
    """A posting's address without its tracking noise: scheme, www, query, fragment and a trailing slash."""
    text = re.sub(r"^https?://(?:www\.)?", "", str(url or "").strip().lower())
    return re.split(r"[?#]", text, maxsplit=1)[0].rstrip("/")


def mark_applied(jobs: Iterable[dict], applications: Iterable) -> list[dict]:
    """Flag each job the owner already applied to, by posting address or by company and title.

    `applications` are tracker records; only those that were really sent count (status group 'done').
    """
    import ui_shell
    sent = [a for a in applications if ui_shell.status_group(getattr(a, "status", "")) == "done"]
    urls = {_url_key(a.url) for a in sent if getattr(a, "url", "")}
    pairs = {(_key(a.company), _key(a.title)) for a in sent if getattr(a, "company", "") and getattr(a, "title", "")}
    out = []
    for job in jobs:
        item = dict(job)
        item["already_applied"] = bool(
            _url_key(item.get("url")) in urls
            or (_key(item.get("company_name")), _key(item.get("job_title"))) in pairs)
        out.append(item)
    return out


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


def prepare(jobs: Iterable[dict], applications: Iterable, now: Optional[datetime] = None) -> list[dict]:
    """The suggested jobs as the page shows them: applied ones marked (and last), newest first within a score."""
    shown = []
    for job in _one_per_role(mark_applied(jobs, applications)):
        job.setdefault("resume_score", None)
        job.setdefault("resume_matched", [])
        job.setdefault("resume_missing", [])
        job["age"], job["is_new"] = posting_age(job.get("discovered_at"), now)
        job["place"], job["more_places"] = short_location(job.get("location"), job.get("is_remote"))
        shown.append(job)
    shown.sort(key=lambda j: str(j.get("discovered_at") or ""), reverse=True)
    shown.sort(key=lambda j: -(j.get("match_score") or 0))
    shown.sort(key=lambda j: j["already_applied"])                     # stable: applied ones after the rest
    return shown
