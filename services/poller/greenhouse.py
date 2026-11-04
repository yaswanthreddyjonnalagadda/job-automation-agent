"""Greenhouse ATS Public API Poller."""
from __future__ import annotations

import logging
import html
import re
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger("poller.greenhouse")


def clean_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def poll_greenhouse(slug: str, session: Optional[requests.Session] = None) -> List[Dict[str, Any]]:
    """Poll Greenhouse public board endpoint:
    GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
    """
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    s = session or requests.Session()
    try:
        resp = s.get(url, timeout=15)
        if resp.status_code == 404:
            logger.warning("Greenhouse slug not found: %s", slug)
            return []
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error("Error polling Greenhouse slug %s: %s", slug, exc)
        return []

    jobs: List[Dict[str, Any]] = []
    for item in data.get("jobs", []):
        job_id = str(item.get("id", ""))
        title = item.get("title", "")
        location_obj = item.get("location") or {}
        location = location_obj.get("name", "") if isinstance(location_obj, dict) else str(location_obj)
        content_html = item.get("content", "")
        plain_desc = clean_html(content_html)
        departments = [d.get("name") for d in item.get("departments", []) if d.get("name")]
        dept = ", ".join(departments) if departments else ""

        is_remote = bool(re.search(r"\b(remote|anywhere|virtual)\b", f"{title} {location}", re.I))

        jobs.append({
            "external_id": job_id,
            "title": title,
            "location": location,
            "department": dept,
            "description": plain_desc,
            "description_html": content_html,
            "apply_url": item.get("absolute_url") or f"https://boards.greenhouse.io/{slug}/jobs/{job_id}",
            "salary_min": None,
            "salary_max": None,
            "salary_currency": "USD",
            "employment_type": "full-time",
            "remote": 1 if is_remote else 0,
            "posted_at": item.get("updated_at"),
        })
    return jobs
