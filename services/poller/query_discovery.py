"""
Query-Driven Real-Time Job Discovery.

Provides search-first discovery matching titles and locations directly across
live job boards (LinkedIn public guest search API, keyless aggregators like
Arbeitnow and Jobicy, and Google Jobs / JSearch wrappers) filtered by freshness
(posted today / past 24 hours).
"""

from __future__ import annotations

import json
import logging
import os
import re
import ssl
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("query_discovery")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

LINKEDIN_GUEST_SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
ARBEITNOW_API_URL = "https://www.arbeitnow.com/api/job-board-api"
JOBICY_API_URL = "https://jobicy.com/api/v2/remote-jobs"
JSEARCH_RAPIDAPI_URL = "https://jsearch.p.rapidapi.com/search"


@dataclass
class DiscoveredQueryJob:
    """Standard representation of a job discovered via a search query."""
    title: str
    company_name: str
    location: str
    url: str
    source: str
    posted_date: str = ""
    is_remote: bool = False
    description: str = ""
    job_id: str = ""
    extra_metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "company_name": self.company_name,
            "location": self.location,
            "url": self.url,
            "source": self.source,
            "posted_date": self.posted_date,
            "is_remote": self.is_remote,
            "description": self.description,
            "job_id": self.job_id,
            "extra_metadata": self.extra_metadata,
        }


def _http_get_resilient(url: str, params: Optional[Dict[str, Any]] = None, timeout: float = 12.0) -> str:
    """
    Fetches URL content using urllib with SSL context, falling back to curl.exe
    if SSL negotiation or handshake fails on Windows.
    """
    full_url = url
    if params:
        encoded_params = urllib.parse.urlencode(params)
        full_url = f"{url}?{encoded_params}" if "?" not in url else f"{url}&{encoded_params}"

    # 1. Primary: urllib with default SSL context
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(full_url, headers=DEFAULT_HEADERS)
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.debug("urllib fetch failed for %s (%s); trying curl.exe fallback", full_url, exc)

    # 2. Secondary: curl.exe on Windows (bypasses OpenSSL cipher incompatibilities)
    try:
        ua = DEFAULT_HEADERS["User-Agent"]
        cmd = ["curl.exe", "-s", "-L", "--max-time", str(int(timeout)), "-H", f"User-Agent: {ua}", full_url]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
        if res.returncode == 0 and res.stdout:
            return res.stdout
    except Exception as exc:
        logger.debug("curl fallback failed: %s", exc)

    # 3. Tertiary: requests
    try:
        resp = requests.get(full_url, headers=DEFAULT_HEADERS, timeout=timeout)
        if resp.status_code == 200:
            return resp.text
    except Exception as exc:
        logger.warning("All HTTP fetch strategies failed for %s: %s", full_url, exc)

    return ""


def clean_location_string(loc_raw: str) -> str:
    """Strips LinkedIn badges like 'Be an early applicant', 'Actively Hiring', etc."""
    cleaned = loc_raw
    noise_patterns = [
        r"Be an early applicant",
        r"Actively Hiring",
        r"Medical insurance",
        r"\+\d+\s+benefits",
        r"\d+\s+(?:hours?|days?|weeks?|minutes?)\s+ago",
        r"Easy Apply",
    ]
    for pat in noise_patterns:
        cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(", ").strip()
    return cleaned


def search_linkedin_guest_jobs(
    keywords: str,
    location: str = "",
    past_24_hours: bool = True,
    start: int = 0,
    max_results: int = 25,
    session: Optional[requests.Session] = None,
    timeout: float = 12.0,
) -> List[DiscoveredQueryJob]:
    """
    Queries LinkedIn's public guest job search endpoint.

    Args:
        keywords: Job title or keywords (e.g. "Network Engineer").
        location: Geographic location (e.g. "United States" or "Fairfax, VA").
        past_24_hours: When True, filters strictly to jobs posted in the past 24 hours (f_TPR=r86400).
        start: Offset for pagination (0, 25, 50, etc.).
        max_results: Maximum jobs to parse.
        session: Optional requests session (unused in resilient fetch).
        timeout: HTTP request timeout.
    """
    params: Dict[str, Any] = {
        "keywords": keywords,
        "location": location,
        "start": start,
    }
    if past_24_hours:
        # f_TPR=r86400 filters to jobs posted within the last 86,400 seconds (24 hours)
        params["f_TPR"] = "r86400"

    html = _http_get_resilient(LINKEDIN_GUEST_SEARCH_URL, params=params, timeout=timeout)
    if not html:
        return []

    return parse_linkedin_guest_html(html, max_results=max_results)


def parse_linkedin_guest_html(html_content: str, max_results: int = 25) -> List[DiscoveredQueryJob]:
    """Parses raw HTML job cards returned by LinkedIn's guest search."""
    soup = BeautifulSoup(html_content, "html.parser")
    cards = soup.find_all("li")
    if not cards:
        cards = soup.find_all("div", class_=re.compile(r"base-card|job-search-card"))

    jobs: List[DiscoveredQueryJob] = []

    for card in cards:
        if len(jobs) >= max_results:
            break

        # 1. Job Title
        title_el = card.find(class_=re.compile(r"base-search-card__title|job-search-card__title"))
        if not title_el:
            title_el = card.find(["h3", "h4", "strong"])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if not title:
            continue

        # 2. Company Name
        company_el = card.find(class_=re.compile(r"base-search-card__subtitle|job-search-card__subtitle"))
        if not company_el:
            company_el = card.find("a", class_=re.compile(r"hidden-nested-link"))
        company = company_el.get_text(strip=True) if company_el else "Unknown Company"

        # 3. Location
        loc_el = card.find(class_=re.compile(r"job-search-card__location|base-search-card__metadata"))
        raw_loc = loc_el.get_text(strip=True) if loc_el else ""
        location = clean_location_string(raw_loc)

        # 4. Job URL / Link
        link_el = card.find("a", class_=re.compile(r"base-card__full-link|job-search-card__url"))
        if not link_el:
            link_el = card.find("a", href=True)
        url = ""
        if link_el and link_el.get("href"):
            url = link_el["href"].split("?")[0]  # Strip tracking query params

        # 5. Posted Date / Time
        time_el = card.find("time")
        posted_date = ""
        if time_el:
            posted_date = time_el.get("datetime") or time_el.get_text(strip=True)
        if not posted_date or not re.match(r"^\d{4}-\d{2}-\d{2}", posted_date):
            posted_date = datetime.now(timezone.utc).date().isoformat()

        # Remote detection
        is_remote = bool(
            re.search(r"\b(remote|work from home|telecommute|anywhere)\b", f"{title} {raw_loc}", re.I)
        )

        job_id = ""
        urn = card.get("data-entity-urn") or ""
        if "urn:li:jobPosting:" in urn:
            job_id = urn.split("urn:li:jobPosting:")[-1]
        elif "/view/" in url:
            m = re.search(r"/view/[^/]*?-?(\d+)", url)
            if m:
                job_id = m.group(1)

        jobs.append(DiscoveredQueryJob(
            title=title,
            company_name=company,
            location=location,
            url=url,
            source="linkedin_guest",
            posted_date=posted_date,
            is_remote=is_remote,
            job_id=job_id,
        ))

    return jobs


def search_arbeitnow_jobs(
    query: str = "",
    session: Optional[requests.Session] = None,
    timeout: float = 10.0,
    max_results: int = 50,
) -> List[DiscoveredQueryJob]:
    """Fetches jobs from Arbeitnow's public keyless API."""
    params = {}
    try:
        text = _http_get_resilient(ARBEITNOW_API_URL, params=params, timeout=timeout)
        if not text:
            return []

        data = json.loads(text).get("data", [])
        jobs: List[DiscoveredQueryJob] = []
        q_lower = query.lower() if query else ""

        for item in data:
            title = item.get("title", "").strip()
            desc = item.get("description", "")
            company = item.get("company_name", "").strip()
            loc = item.get("location", "").strip()
            remote = bool(item.get("remote", False))
            url = item.get("url", "")
            created_at = item.get("created_at")
            posted_str = ""
            if created_at:
                try:
                    posted_str = datetime.fromtimestamp(created_at, tz=timezone.utc).date().isoformat()
                except Exception:
                    posted_str = str(created_at)

            combined = f"{title} {company} {desc}".lower()
            if q_lower and q_lower not in combined:
                continue

            jobs.append(DiscoveredQueryJob(
                title=title,
                company_name=company,
                location=clean_location_string(loc) if loc else ("Remote" if remote else ""),
                url=url,
                source="arbeitnow",
                posted_date=posted_str,
                is_remote=remote,
                description=desc[:500],
            ))
            if len(jobs) >= max_results:
                break

        return jobs
    except Exception as exc:
        logger.warning("Arbeitnow search failed: %s", exc)
        return []


def search_jobicy_jobs(
    query: str = "",
    session: Optional[requests.Session] = None,
    timeout: float = 10.0,
    max_results: int = 50,
) -> List[DiscoveredQueryJob]:
    """Fetches remote tech jobs from Jobicy's public keyless API."""
    params: Dict[str, Any] = {"count": max_results}
    if query:
        params["tag"] = query

    try:
        text = _http_get_resilient(JOBICY_API_URL, params=params, timeout=timeout)
        if not text:
            return []

        data = json.loads(text).get("jobs", [])
        jobs: List[DiscoveredQueryJob] = []

        for item in data:
            title = item.get("jobTitle", "").strip()
            company = item.get("companyName", "").strip()
            url = item.get("url", "")
            loc = item.get("jobGeo", "Remote")
            posted = item.get("pubDate", "")
            desc = item.get("jobExcerpt", "")

            jobs.append(DiscoveredQueryJob(
                title=title,
                company_name=company,
                location=clean_location_string(loc),
                url=url,
                source="jobicy",
                posted_date=posted[:10] if len(posted) >= 10 else posted,
                is_remote=True,
                description=desc,
                job_id=str(item.get("id", "")),
            ))

        return jobs
    except Exception as exc:
        logger.warning("Jobicy search failed: %s", exc)
        return []


def search_jsearch_rapidapi(
    query: str,
    rapidapi_key: Optional[str] = None,
    date_posted: str = "today",
    num_pages: int = 1,
    session: Optional[requests.Session] = None,
    timeout: float = 12.0,
) -> List[DiscoveredQueryJob]:
    """
    Fetches real-time Google Jobs via JSearch on RapidAPI.
    Uses RAPIDAPI_KEY from env if not passed explicitly.
    """
    key = rapidapi_key or os.environ.get("RAPIDAPI_KEY", "").strip()
    if not key:
        return []

    s = session or requests.Session()
    headers = {
        "X-RapidAPI-Key": key,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    }
    params = {
        "query": query,
        "page": "1",
        "num_pages": str(num_pages),
        "date_posted": date_posted,
    }

    try:
        resp = s.get(JSEARCH_RAPIDAPI_URL, headers=headers, params=params, timeout=timeout)
        if resp.status_code != 200:
            logger.warning("JSearch API returned HTTP %d", resp.status_code)
            return []

        data = resp.json().get("data", [])
        jobs: List[DiscoveredQueryJob] = []

        for item in data:
            title = item.get("job_title", "").strip()
            company = item.get("employer_name", "").strip()
            city = item.get("job_city", "")
            state = item.get("job_state", "")
            country = item.get("job_country", "")
            loc_parts = [p for p in (city, state, country) if p]
            loc = ", ".join(loc_parts) or "United States"
            is_remote = bool(item.get("job_is_remote", False))
            apply_url = item.get("job_apply_link", "") or item.get("job_google_link", "")
            desc = item.get("job_description", "")
            posted = item.get("job_posted_at_datetime_utc", "")

            jobs.append(DiscoveredQueryJob(
                title=title,
                company_name=company,
                location="Remote" if is_remote and not loc else loc,
                url=apply_url,
                source="jsearch_google_jobs",
                posted_date=posted[:10] if len(posted) >= 10 else posted,
                is_remote=is_remote,
                description=desc[:1000],
                job_id=item.get("job_id", ""),
                extra_metadata={"apply_options": item.get("apply_options", [])},
            ))

        return jobs
    except Exception as exc:
        logger.warning("JSearch API query failed: %s", exc)
        return []


def search_all_fresh_jobs(
    keywords: str,
    location: str = "United States",
    past_24_hours: bool = True,
    rapidapi_key: Optional[str] = None,
    limit: int = 50,
) -> List[DiscoveredQueryJob]:
    """
    Combined query across all available fresh sources (LinkedIn Guest Search,
    Arbeitnow, Jobicy, and optional JSearch).
    Deduplicates results by title and company.
    """
    all_jobs: List[DiscoveredQueryJob] = []
    seen: set[str] = set()

    def _add_jobs(source_jobs: List[DiscoveredQueryJob]):
        for j in source_jobs:
            norm_key = f"{j.company_name.lower().strip()}:{j.title.lower().strip()}"
            if norm_key not in seen:
                seen.add(norm_key)
                all_jobs.append(j)

    # 1. LinkedIn Guest Search (primary real-time source for US & fresh postings)
    logger.info("Querying LinkedIn Guest API for '%s' in '%s' (today=%s)", keywords, location, past_24_hours)
    li_jobs = search_linkedin_guest_jobs(keywords, location=location, past_24_hours=past_24_hours, max_results=limit)
    _add_jobs(li_jobs)

    # 2. JSearch (Google Jobs aggregator if key provided)
    if rapidapi_key or os.environ.get("RAPIDAPI_KEY"):
        logger.info("Querying JSearch RapidAPI for '%s'", keywords)
        js_jobs = search_jsearch_rapidapi(f"{keywords} in {location}", rapidapi_key=rapidapi_key, date_posted="today")
        _add_jobs(js_jobs)

    # 3. Keyless Remote Feeds
    if len(all_jobs) < limit:
        logger.info("Checking keyless remote feeds for '%s'", keywords)
        jobicy_jobs = search_jobicy_jobs(keywords, max_results=20)
        _add_jobs(jobicy_jobs)

    if len(all_jobs) < limit:
        arbeit_jobs = search_arbeitnow_jobs(keywords, max_results=20)
        _add_jobs(arbeit_jobs)

    return all_jobs[:limit]


def search_linkedin_guest_jobs_paginated(
    keywords: str,
    location: str = "",
    past_24_hours: bool = True,
    max_pages: int = 4,
    limit_per_page: int = 25,
    timeout: float = 12.0,
) -> List[DiscoveredQueryJob]:
    """Exhaustive pagination over LinkedIn public guest endpoint across start=0, 25, 50..."""
    all_jobs: List[DiscoveredQueryJob] = []
    seen_urls: set[str] = set()

    for page in range(max_pages):
        start = page * limit_per_page
        jobs = search_linkedin_guest_jobs(
            keywords=keywords,
            location=location,
            past_24_hours=past_24_hours,
            start=start,
            max_results=limit_per_page,
            timeout=timeout,
        )
        if not jobs:
            break

        new_on_page = 0
        for j in jobs:
            if j.url and j.url not in seen_urls:
                seen_urls.add(j.url)
                all_jobs.append(j)
                new_on_page += 1

        if new_on_page == 0:
            break

    return all_jobs


def search_all_fresh_jobs_at_scale(
    keywords: str,
    location: str = "",
    past_24_hours: bool = True,
    rapidapi_key: Optional[str] = None,
    max_pages: int = 3,
) -> tuple[List[DiscoveredQueryJob], Dict[str, Dict[str, Any]]]:
    """
    Multi-source query with per-source isolation.
    If one source fails or gets rate-limited, all other sources continue normally.
    Returns (jobs, stats_by_source).
    """
    stats: Dict[str, Dict[str, Any]] = {
        "linkedin_guest": {"count": 0, "error": False},
        "jsearch": {"count": 0, "error": False},
        "jobicy": {"count": 0, "error": False},
        "arbeitnow": {"count": 0, "error": False},
    }
    all_jobs: List[DiscoveredQueryJob] = []
    seen: set[str] = set()

    def _add(j_list: List[DiscoveredQueryJob], src: str):
        cnt = 0
        for j in j_list:
            key = f"{j.company_name.lower().strip()}:{j.title.lower().strip()}"
            if key not in seen:
                seen.add(key)
                all_jobs.append(j)
                cnt += 1
        stats[src]["count"] = cnt

    # 1. LinkedIn with pagination & isolation
    try:
        li = search_linkedin_guest_jobs_paginated(keywords, location=location, past_24_hours=past_24_hours, max_pages=max_pages)
        _add(li, "linkedin_guest")
    except Exception as exc:
        logger.warning("LinkedIn guest search failed: %s", exc)
        stats["linkedin_guest"]["error"] = True

    # 2. JSearch (optional API key)
    key = rapidapi_key or os.environ.get("RAPIDAPI_KEY")
    if key:
        try:
            js = search_jsearch_rapidapi(f"{keywords} in {location}", rapidapi_key=key, date_posted="today")
            _add(js, "jsearch")
        except Exception as exc:
            logger.warning("JSearch query failed: %s", exc)
            stats["jsearch"]["error"] = True

    # 3. Jobicy remote feed
    try:
        jb = search_jobicy_jobs(keywords, max_results=30)
        _add(jb, "jobicy")
    except Exception as exc:
        logger.warning("Jobicy query failed: %s", exc)
        stats["jobicy"]["error"] = True

    # 4. Arbeitnow remote feed
    try:
        ar = search_arbeitnow_jobs(keywords, max_results=30)
        _add(ar, "arbeitnow")
    except Exception as exc:
        logger.warning("Arbeitnow query failed: %s", exc)
        stats["arbeitnow"]["error"] = True

    return all_jobs, stats

