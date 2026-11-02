"""Where fresh jobs come from: each system's own public list, read the way its careers page reads it (f299).

Every reader returns the same plain shape (company, title, place, address, description, and when it was posted) so the
scan treats a Workday job like a Greenhouse one. Nothing here writes anything and nothing needs a login. A system that
does not answer gives an empty list, never an error: one slow employer must not stop the rest of a scan.

When a job was posted is kept as exactly as the system says it: an exact time where there is one, a day where the
system only says "Posted 3 Days Ago". A day-level date is held as the earliest it could have been posted, so a job is
only ever called recent when it certainly is ("within the last hour" is never claimed for a job dated only by its day).
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from urllib.parse import quote, urlparse

import requests

logger = logging.getLogger("discovery_sources")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/128.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
}
_REMOTE = re.compile(r"\b(remote|work from home|anywhere|distributed|virtual)\b", re.IGNORECASE)


# -- when a job was posted ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Posted:
    at: datetime            # an exact time, or for a day-level date the earliest moment it could have been posted
    precision: str          # "minute" (an exact time) or "day"


def _utc(moment: datetime) -> datetime:
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


_RELATIVE = re.compile(r"^\s*(?:re)?posted\s+(today|yesterday|(\d+)\s+days?\s+ago)\s*$", re.IGNORECASE)
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_posted(value: Any, now: Optional[datetime] = None) -> Optional[Posted]:
    """When a posting says it was posted, or None when it does not say (never a guess)."""
    now = _utc(now or datetime.now(timezone.utc))
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        number = float(value)
        if number <= 0:
            return None
        seconds = number / 1000 if number > 1e11 else number          # milliseconds (Lever) or seconds
        return Posted(datetime.fromtimestamp(seconds, tz=timezone.utc), "minute")
    text = str(value).strip()
    if not text:
        return None
    relative = _RELATIVE.match(text)
    if relative:
        word = relative.group(1).lower()
        days = 0 if word == "today" else 1 if word == "yesterday" else int(relative.group(2))
        # "Posted N Days Ago" means 0 to 1 day more than N: held as the earliest, N + 1 days back.
        return Posted(now - timedelta(days=days + 1), "day")
    if _DATE_ONLY.match(text):
        return Posted(datetime.fromisoformat(text).replace(tzinfo=timezone.utc), "day")
    try:
        return Posted(_utc(datetime.fromisoformat(text.replace("Z", "+00:00"))), "minute")
    except ValueError:
        return None


def within(posted: Optional[Posted], hours: float, now: Optional[datetime] = None) -> bool:
    """True when the job was certainly posted inside the last `hours`. No known date is never inside."""
    if posted is None:
        return False
    now = _utc(now or datetime.now(timezone.utc))
    return (now - posted.at).total_seconds() / 3600 <= hours


def _stamp(job: dict, posted: Optional[Posted]) -> dict:
    job["posted_at"] = posted.at.isoformat() if posted else ""
    job["posted_precision"] = posted.precision if posted else ""
    return job


def _plain(raw: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html_lib.unescape(raw or ""), flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(html_lib.unescape(text).split())


def _job(company: str, token: str, ats: str, title: str, place: str, url: str, description: str,
         posted: Optional[Posted], remote: bool = False) -> dict:
    job = {"company_name": company, "company_token": token, "ats_type": ats, "job_title": (title or "").strip(),
           "location": (place or "").strip(), "is_remote": 1 if remote or _REMOTE.search(f"{title} {place}") else 0,
           "url": url, "description": description or "",
           "updated_at": datetime.now(timezone.utc).isoformat()}
    return _stamp(job, posted)


# -- the readers -------------------------------------------------------------------------------------------------

def fetch_ashby_feed(token: str, company: str, timeout: float = 8.0) -> list[dict]:
    """Ashby's public job board: every listed job with the time it was published."""
    try:
        resp = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{token}", headers=HEADERS, timeout=timeout)
        if resp.status_code != 200:
            return []
        jobs = []
        for raw in resp.json().get("jobs", []):
            url = raw.get("jobUrl") or raw.get("applyUrl") or ""
            if not url or raw.get("isListed") is False:
                continue
            jobs.append(_job(company, token, "ashby", raw.get("title"), raw.get("location"), url,
                             (raw.get("descriptionPlain") or "").strip(), parse_posted(raw.get("publishedAt")),
                             remote=bool(raw.get("isRemote"))))
        return jobs
    except Exception as exc:
        logger.warning("Could not read Ashby jobs for %s: %s", token, str(exc).splitlines()[0][:100])
        return []


WORKDAY_PAGE = 20          # the most Workday returns for one request
WORKDAY_MAX_PAGES = 5      # so a title is read to its first hundred results at most


def fetch_workday_feed(entry: dict, titles: Iterable[str], timeout: float = 15.0, now: Optional[datetime] = None,
                       max_age_days: int = 7, max_details: int = 40) -> list[dict]:
    """A Workday employer's jobs, searched by each wanted title the way its careers page searches.

    Workday's list says only "Posted N Days Ago", so only jobs inside `max_age_days` are opened (for their text,
    which the resume score and the sponsorship check need); older ones are dropped here.
    """
    host, tenant, site = entry.get("host", ""), entry.get("tenant", ""), entry.get("site", "")
    if not (host and tenant and site):
        return []
    company = entry.get("company") or tenant
    found: dict[str, dict] = {}
    for title in titles:
        # Workday sorts a search by relevance, not by date, so a fresh job can sit on any page: read page by page until
        # a short page says the list has ended (Rackspace's second page held as many posted this week as its first).
        for offset in range(0, WORKDAY_PAGE * WORKDAY_MAX_PAGES, WORKDAY_PAGE):
            try:
                resp = requests.post(f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
                                     headers={**HEADERS, "Content-Type": "application/json"},
                                     json={"appliedFacets": {}, "limit": WORKDAY_PAGE, "offset": offset,
                                           "searchText": title},
                                     timeout=timeout)
                if resp.status_code != 200:
                    break
                postings = resp.json().get("jobPostings", [])
                for item in postings:
                    path = item.get("externalPath") or ""
                    posted = parse_posted(item.get("postedOn"), now)
                    if path and posted and within(posted, max_age_days * 24 + 24, now):
                        found.setdefault(path, {"item": item, "posted": posted})
                if len(postings) < WORKDAY_PAGE:
                    break
            except Exception as exc:
                logger.warning("Could not search %s: %s", host, str(exc).splitlines()[0][:100])
                break
    jobs = []
    for path, hit in list(found.items())[:max_details]:
        item, description, url = hit["item"], "", f"https://{host}/{site}{path}"
        try:
            detail = requests.get(f"https://{host}/wday/cxs/{tenant}/{site}{path}", headers=HEADERS, timeout=timeout)
            if detail.status_code == 200:
                info = detail.json().get("jobPostingInfo", {})
                description = _plain(info.get("jobDescription") or "")
                url = info.get("externalUrl") or url
        except Exception as exc:
            logger.debug("No detail for %s: %s", path, str(exc).splitlines()[0][:100])
        jobs.append(_job(company, tenant, "workday", item.get("title"), item.get("locationsText"), url, description,
                         hit["posted"], remote=(item.get("remoteType") or "").lower() == "remote"))
    return jobs


_ICIMS_CARD = re.compile(r'<li class="iCIMS_JobCardItem">(.*?)</li>', re.S)
_ICIMS_LINK = re.compile(r'<a href="(https://[^"]+?/jobs/\d+/[^"]*?/job)[^"]*"[^>]*>.*?<h3[^>]*>\s*(.*?)\s*</h3>', re.S)
_ICIMS_PLACE = re.compile(r'iCIMS_JobHeaderData"[^>]*>\s*<span[^>]*>\s*([^<]+?)\s*</span>', re.S)
_ICIMS_DATE = re.compile(r'"datePosted"\s*:\s*"([^"]+)"')


def fetch_icims_feed(entry: dict, titles: Iterable[str], timeout: float = 15.0, max_details: int = 15) -> list[dict]:
    """An iCIMS portal's jobs matching each wanted title. The list shows no dates, so each match is opened for its own."""
    host = entry.get("host", "")
    if not host:
        return []
    company = entry.get("company") or host
    cards: dict[str, tuple[str, str]] = {}
    for title in titles:
        try:
            resp = requests.get(f"https://{host}/jobs/search", headers=HEADERS, timeout=timeout,
                                params={"ss": 1, "searchKeyword": title, "in_iframe": 1})
            if resp.status_code != 200:
                continue
            for card in _ICIMS_CARD.findall(resp.text):
                link = _ICIMS_LINK.search(card)
                if link:
                    place = _ICIMS_PLACE.search(card)
                    cards.setdefault(link.group(1), (_plain(link.group(2)), _plain(place.group(1)) if place else ""))
        except Exception as exc:
            logger.warning("Could not search %s: %s", host, str(exc).splitlines()[0][:100])
    jobs = []
    for url, (title, place) in list(cards.items())[:max_details]:
        posted, description = None, ""
        try:
            page = requests.get(url, headers=HEADERS, timeout=timeout, params={"in_iframe": 1})
            if page.status_code == 200:
                date = _ICIMS_DATE.search(page.text)
                posted = parse_posted(date.group(1)) if date else None
                try:
                    block = re.search(r'"description"\s*:\s*"(.*?)"\s*[,}]', page.text, re.S)
                    description = _plain(json.loads('"' + block.group(1) + '"')) if block else ""
                except ValueError:
                    description = ""
        except Exception as exc:
            logger.debug("No detail for %s: %s", url, str(exc).splitlines()[0][:100])
        jobs.append(_job(company, host, "icims", title, place, url, description, posted))
    return jobs


# -- which companies ---------------------------------------------------------------------------------------------

_WORKDAY = re.compile(r"^https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(.+)$", re.IGNORECASE)
_LOCALE = re.compile(r"^[a-z]{2}(?:-[A-Za-z]{2})?$")


def _entry(history_company: str, url: str) -> Optional[dict]:
    """The company a past application's address belongs to, when its system publishes a list this module reads."""
    parsed = urlparse(url or "")
    host, parts = (parsed.netloc or "").lower(), [p for p in parsed.path.split("/") if p]
    workday = _WORKDAY.match(url or "")
    if workday and "job" in parts:
        site_parts = [p for p in parts[:parts.index("job")] if not _LOCALE.match(p)]
        if site_parts:
            return {"company": history_company, "ats": "workday", "host": host, "tenant": workday.group(1).lower(),
                    "site": site_parts[-1]}
    if host == "jobs.ashbyhq.com" and parts:
        return {"company": history_company, "ats": "ashby", "token": parts[0].lower()}
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io") and parts:
        token = parts[0] if parts[0] != "embed" else (re.search(r"[?&]for=([^&]+)", url) or [None, ""])[1]
        if token:
            return {"company": history_company, "ats": "greenhouse", "token": token.lower()}
    if host == "jobs.lever.co" and parts:
        return {"company": history_company, "ats": "lever", "token": parts[0].lower()}
    if host.endswith(".icims.com"):
        return {"company": history_company, "ats": "icims", "host": host}
    return None


def _identity(entry: dict) -> tuple:
    ats = entry.get("ats", "")
    return (ats, (entry.get("tenant") or entry.get("token") or entry.get("host") or "").lower())


def companies_from_history(history: Iterable[tuple[str, str]]) -> list[dict]:
    """One entry per employer the owner has applied through, from the (company, address) of each past application."""
    seen: dict[tuple, dict] = {}
    for company, url in history:
        entry = _entry(company, url)
        if entry:
            seen.setdefault(_identity(entry), entry)
    return list(seen.values())


def merge_companies(listed: Iterable[dict], from_history: Iterable[dict]) -> list[dict]:
    """The owner's own list first, then the employers from history that are not already on it."""
    merged: dict[tuple, dict] = {}
    for entry in [*listed, *from_history]:
        merged.setdefault(_identity(entry), entry)
    return list(merged.values())
