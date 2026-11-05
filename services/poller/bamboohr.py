"""
BambooHR Public Career Board Poller.
Added by ECC: Fetches active job postings directly from BambooHR's public career APIs.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.poller.query_discovery import _http_get_resilient, clean_location_string

logger = logging.getLogger("bamboohr_poller")


def fetch_bamboohr_jobs(
    company_slug: str,
    company_name: str,
    timeout: float = 10.0,
) -> List[Dict[str, Any]]:
    """
    Fetches active jobs from BambooHR's public career API.
    Endpoint: https://{company_slug}.bamboohr.com/careers/list or /jobs/embed2List/?json=true
    """
    # Try primary JSON endpoint
    url = f"https://{company_slug}.bamboohr.com/jobs/embed2List/?json=true"
    text = _http_get_resilient(url, timeout=timeout)
    data = []

    if text:
        try:
            parsed = json.loads(text)
            data = parsed.get("result", []) or parsed.get("jobs", [])
        except Exception:
            data = []

    # Fallback to /careers/list endpoint
    if not data:
        fallback_url = f"https://{company_slug}.bamboohr.com/careers/list"
        text = _http_get_resilient(fallback_url, timeout=timeout)
        if text:
            try:
                parsed = json.loads(text)
                data = parsed.get("result", []) or parsed.get("jobs", [])
            except Exception:
                data = []

    jobs: List[Dict[str, Any]] = []
    for item in data:
        title = item.get("jobOpeningName") or item.get("title") or ""
        if not title:
            continue

        job_id = str(item.get("id") or item.get("jobOpeningId") or "")
        loc_obj = item.get("location") or {}
        if isinstance(loc_obj, dict):
            city = loc_obj.get("city", "")
            state = loc_obj.get("state", "")
            loc_str = f"{city}, {state}".strip(", ") if city or state else ""
        else:
            loc_str = str(loc_obj)

        is_remote = bool(item.get("isRemote") or item.get("telecommuting") or re.search(r"\bremote\b", f"{title} {loc_str}", re.I))
        apply_url = item.get("applyUrl") or f"https://{company_slug}.bamboohr.com/careers/{job_id}"

        jobs.append({
            "company_name": company_name,
            "company_slug": company_slug,
            "ats_type": "bamboohr",
            "external_id": job_id or apply_url,
            "title": title.strip(),
            "location": clean_location_string(loc_str),
            "is_remote": 1 if is_remote else 0,
            "remote": 1 if is_remote else 0,
            "apply_url": apply_url,
            "department": item.get("department") or "",
            "description": item.get("description") or "",
            "posted_at": datetime.now(timezone.utc).isoformat(),
        })

    return jobs

