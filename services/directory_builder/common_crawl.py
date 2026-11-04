"""Common Crawl CDX Mining for ATS Board Discovery.
Extracts company board slugs for Greenhouse, Lever, Ashby, Workday, and SmartRecruiters.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse
import requests

logger = logging.getLogger("directory.common_crawl")

ATS_PATTERNS = {
    "greenhouse": "boards.greenhouse.io/*",
    "lever": "jobs.lever.co/*",
    "ashby": "jobs.ashbyhq.com/*",
    "smartrecruiters": "jobs.smartrecruiters.com/*",
}


def query_cdx(url_pattern: str, limit: int = 100, session: Optional[requests.Session] = None) -> List[str]:
    """Query Common Crawl index API for a given URL match pattern."""
    index_endpoint = "https://index.commoncrawl.org/CC-MAIN-2024-38-index"
    params = {
        "url": url_pattern,
        "output": "json",
        "limit": str(limit),
        "fl": "url",
    }
    s = session or requests.Session()
    try:
        resp = s.get(index_endpoint, params=params, timeout=25)
        if resp.status_code != 200:
            logger.warning("CDX index query returned %d for %s", resp.status_code, url_pattern)
            return []
        urls = [line.strip().strip('"') for line in resp.text.splitlines() if line.strip()]
        return urls
    except Exception as exc:
        logger.error("Failed querying CDX for %s: %s", url_pattern, exc)
        return []


def extract_slug(ats_platform: str, url: str) -> Optional[str]:
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    parts = path.split("/")
    if ats_platform == "greenhouse":
        return parts[0] if parts and parts[0] != "embed" else (parts[1] if len(parts) > 1 else None)
    elif ats_platform in ("lever", "ashby", "smartrecruiters"):
        return parts[0] if parts else None
    elif "myworkdayjobs.com" in parsed.netloc:
        tenant = parsed.netloc.split(".")[0]
        return tenant
    return None


def mine_ats_slugs(ats_platform: str, limit: int = 100) -> Set[str]:
    pattern = ATS_PATTERNS.get(ats_platform)
    if not pattern:
        return set()
    urls = query_cdx(pattern, limit=limit)
    slugs: Set[str] = set()
    for u in urls:
        slug = extract_slug(ats_platform, u)
        if slug and len(slug) >= 2 and slug not in ("jobs", "api", "v1", "embed"):
            slugs.add(slug.lower())
    return slugs
