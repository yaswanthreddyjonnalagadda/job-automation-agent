"""Workday ATS CXS Public API Poller."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger("poller.workday")


def parse_workday_url(board_url: str) -> Optional[Dict[str, str]]:
    """Extract tenant, host, and site from a Workday career URL.
    E.g. https://analogdevices.wd1.myworkdayjobs.com/Analog_Devices_External
    -> {'host': 'analogdevices.wd1.myworkdayjobs.com', 'tenant': 'analogdevices', 'site': 'Analog_Devices_External'}
    """
    m = re.match(r"https?://([^/]+)/([^/?#]+)", board_url)
    if not m:
        return None
    host = m.group(1)
    site = m.group(2)
    tenant = host.split(".")[0]
    return {"host": host, "tenant": tenant, "site": site}


def poll_workday(board_url: str, max_jobs: int = 100, session: Optional[requests.Session] = None) -> List[Dict[str, Any]]:
    parsed = parse_workday_url(board_url)
    if not parsed:
        logger.warning("Invalid Workday board URL: %s", board_url)
        return []

    host = parsed["host"]
    tenant = parsed["tenant"]
    site = parsed["site"]
    api_url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"

    s = session or requests.Session()
    s.headers.update({
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })

    jobs: List[Dict[str, Any]] = []
    offset = 0
    limit = 20

    while offset < max_jobs:
        payload = {"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""}
        try:
            resp = s.post(api_url, json=payload, timeout=20)
            if resp.status_code == 404:
                break
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("Error polling Workday %s at offset %d: %s", board_url, offset, exc)
            break

        postings = data.get("jobPostings", [])
        if not postings:
            break

        for item in postings:
            ext_id = item.get("bulletFields", [None])[0] or item.get("externalPath", "").split("/")[-1] or str(len(jobs) + 1)
            title = item.get("title", "")
            locations = item.get("locationsText", "")
            posted_on = item.get("postedOn", "")
            external_path = item.get("externalPath", "")
            apply_url = f"https://{host}/{site}{external_path}" if external_path else board_url

            is_remote = bool(re.search(r"\b(remote|anywhere|virtual)\b", f"{title} {locations}", re.I))

            jobs.append({
                "external_id": ext_id,
                "title": title,
                "location": locations,
                "department": "",
                "description": title,  # Full description requires per-job call; preview used for diff
                "description_html": "",
                "apply_url": apply_url,
                "salary_min": None,
                "salary_max": None,
                "salary_currency": "USD",
                "employment_type": "full-time",
                "remote": 1 if is_remote else 0,
                "posted_at": posted_on,
            })

        total = data.get("total", len(jobs))
        offset += limit
        if offset >= total:
            break

    return jobs

