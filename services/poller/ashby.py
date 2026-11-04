"""Ashby ATS Public API Poller."""
from __future__ import annotations

import logging
import html
import re
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger("poller.ashby")


def clean_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def poll_ashby(org: str, session: Optional[requests.Session] = None) -> List[Dict[str, Any]]:
    """Poll Ashby public endpoint:
    GET https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true
    """
    url = f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true"
    s = session or requests.Session()
    try:
        resp = s.get(url, timeout=15)
        if resp.status_code == 404:
            logger.warning("Ashby org not found: %s", org)
            return []
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error("Error polling Ashby org %s: %s", org, exc)
        return []

    jobs: List[Dict[str, Any]] = []
    for item in data.get("jobs", []):
        job_id = str(item.get("id", ""))
        title = item.get("title", "")
        location = item.get("locationName", "")
        dept = item.get("departmentName", "")
        employment_type = item.get("employmentType", "Full-time")

        desc_html = item.get("descriptionHtml", "")
        desc_plain = clean_html(desc_html)

        comp = item.get("compensation") or {}
        comp_tier = comp.get("tierSummary") or {}
        min_sal = comp_tier.get("minCompensation")
        max_sal = comp_tier.get("maxCompensation")
        currency = comp_tier.get("currencyCode", "USD")

        is_remote = bool(item.get("isRemote")) or bool(re.search(r"\b(remote|anywhere|virtual)\b", f"{title} {location}", re.I))

        jobs.append({
            "external_id": job_id,
            "title": title,
            "location": location,
            "department": dept,
            "description": desc_plain,
            "description_html": desc_html,
            "apply_url": item.get("applyUrl") or item.get("jobUrl") or f"https://jobs.ashbyhq.com/{org}/{job_id}",
            "salary_min": min_sal,
            "salary_max": max_sal,
            "salary_currency": currency,
            "employment_type": employment_type,
            "remote": 1 if is_remote else 0,
            "posted_at": item.get("publishedAt"),
        })
    return jobs
