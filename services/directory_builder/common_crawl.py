"""Common Crawl CDX Mining for ATS Board Discovery.
Extracts company board identifiers for Greenhouse, Lever, Ashby, Workday, and SmartRecruiters.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse
import requests

from services.poller.http import make_session

logger = logging.getLogger("directory.common_crawl")

ATS_PATTERNS = {
    "workday": "*.myworkdayjobs.com",
    "greenhouse": "boards.greenhouse.io/*",
    "greenhouse2": "job-boards.greenhouse.io/*",
    "lever": "jobs.lever.co/*",
    "ashby": "jobs.ashbyhq.com/*",
    "smartrecruiters": "jobs.smartrecruiters.com/*",
}

PROVIDER_HOSTS = {
    "greenhouse": "boards.greenhouse.io",
    "greenhouse2": "job-boards.greenhouse.io",
    "lever": "jobs.lever.co",
    "ashby": "jobs.ashbyhq.com",
    "smartrecruiters": "jobs.smartrecruiters.com",
}

HOST_WORKDAY_RE = re.compile(r"^[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com$", re.I)
LOCALE_RE = re.compile(r"^[a-z]{2}(?:-[a-z0-9]{2,4})?$", re.I)

LATEST_CRAWL_INDEX = "CC-MAIN-2024-51-index"
_CURRENT_INDEX_CACHE: Optional[str] = None


def get_newest_crawl_index(session: requests.Session) -> str:
    """Fetch the newest crawl index from commoncrawl collinfo.json, or fallback."""
    global _CURRENT_INDEX_CACHE
    if _CURRENT_INDEX_CACHE:
        return _CURRENT_INDEX_CACHE
    try:
        resp = session.get("https://index.commoncrawl.org/collinfo.json", timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data and isinstance(data[0], dict):
                idx = data[0].get("id")
                if idx:
                    if not idx.endswith("-index"):
                        idx = f"{idx}-index"
                    _CURRENT_INDEX_CACHE = idx
                    return idx
    except Exception as exc:
        logger.debug("Could not fetch newest crawl index: %s; using default %s", exc, LATEST_CRAWL_INDEX)
    return LATEST_CRAWL_INDEX


def query_cdx(
    url_pattern: str,
    limit: int = 100,
    session: Optional[requests.Session] = None,
) -> List[str]:
    """Query Common Crawl index API for a given pattern, paging results until empty."""
    s = session or make_session("commoncrawl")
    crawl_index = get_newest_crawl_index(s)
    if not crawl_index.endswith("-index"):
        crawl_index = f"{crawl_index}-index"
    index_endpoint = f"https://index.commoncrawl.org/{crawl_index}"

    results: List[str] = []
    page = 0
    page_size = min(limit, 100) if limit > 0 else 100

    while True:
        params = {
            "url": url_pattern,
            "output": "json",
            "page": str(page),
            "limit": str(page_size),
            "fl": "url",
        }
        resp = None
        for attempt in range(3):
            try:
                resp = s.get(index_endpoint, params=params, timeout=30.0)
                if resp.status_code in (502, 503, 504):
                    time.sleep(2.0 * (attempt + 1))
                    continue
                break
            except Exception as exc:
                if attempt == 2:
                    logger.warning("CDX index query error for %s (page=%d): %s", url_pattern, page, exc)
                    break
                time.sleep(2.0 * (attempt + 1))

        if not resp:
            break
        if resp.status_code == 404 or not resp.text.strip():
            break
        if resp.status_code != 200:
            logger.warning("CDX index query returned %d for %s (page=%d)", resp.status_code, url_pattern, page)
            break

        lines = [line.strip() for line in resp.text.splitlines() if line.strip()]
        if not lines:
            break

        page_urls: List[str] = []
        for line in lines:
            try:
                obj = json.loads(line)
                u = obj.get("url") if isinstance(obj, dict) else line
            except Exception:
                u = line.strip('"')
            if u:
                page_urls.append(u)

        if not page_urls:
            break

        results.extend(page_urls)
        if limit > 0 and len(results) >= limit:
            results = results[:limit]
            break

        page += 1
        time.sleep(1.0)  # Wait 1 second between requests

    return results


def extract_board(provider: str, url: str) -> Optional[str]:
    r"""Extract canonical board identifier from ATS URL.

    Workday: host must match ^[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com$; board = host + "/" + first
      path part that is not a locale (en-US, fr-FR ...). One tenant can have several sites: keep each.
    Others: board = first path part (the slug). Skip "embed", "api", "jobs" and empty.
    """
    if not url:
        return None

    raw_url = url.strip()
    if not (raw_url.startswith("http://") or raw_url.startswith("https://")):
        raw_url = "https://" + raw_url

    parsed = urlparse(raw_url)
    host = (parsed.netloc or "").strip().lower()
    path = parsed.path.strip("/")
    parts = [p for p in path.split("/") if p]

    prov = provider.lower()
    if prov == "workday":
        if not HOST_WORKDAY_RE.match(host):
            return None
        # Drop leading locale segments
        remainder = list(parts)
        while remainder and LOCALE_RE.match(remainder[0]):
            remainder.pop(0)
        if not remainder:
            return None
        site = remainder[0]
        if site.lower() in ("robots.txt", "sitemap.xml", "favicon.ico") or site.endswith((".txt", ".xml", ".ico")):
            return None
        return f"{host}/{site}"

    # Other providers: verify host matches provider domain
    expected_host = PROVIDER_HOSTS.get(prov)
    if expected_host and host != expected_host:
        return None

    if not parts:
        return None

    slug = parts[0].strip()
    if slug.lower() in ("embed", "api", "jobs", "v1") or len(slug) < 2:
        return None

    return slug


def extract_slug(ats_platform: str, url: str) -> Optional[str]:
    """Backward-compatible helper returning slug or tenant."""
    board = extract_board(ats_platform, url)
    if board and ats_platform == "workday" and "/" in board:
        return board.split("/")[0].split(".")[0]
    return board


def mine_ats_slugs(ats_platform: str, limit: int = 100) -> Set[str]:
    pattern = ATS_PATTERNS.get(ats_platform)
    if not pattern:
        return set()
    urls = query_cdx(pattern, limit=limit)
    slugs: Set[str] = set()
    for u in urls:
        slug = extract_board(ats_platform, u)
        if slug:
            slugs.add(slug.lower())
    return slugs
