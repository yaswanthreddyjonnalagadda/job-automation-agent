"""Lever ATS Public API Poller."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger("poller.lever")


def poll_lever(company: str, session: Optional[requests.Session] = None) -> List[Dict[str, Any]]:
    """Poll Lever public endpoint:
    GET https://api.lever.co/v0/postings/{company}
    """
    url = f"https://api.lever.co/v0/postings/{company}"
    s = session or requests.Session()
    try:
        resp = s.get(url, timeout=15)
        if resp.status_code == 404:
            logger.warning("Lever company not found: %s", company)
            return []
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error("Error polling Lever company %s: %s", company, exc)
        return []

    jobs: List[Dict[str, Any]] = []
    for item in data:
        job_id = str(item.get("id", ""))
        title = item.get("text", "")
        cats = item.get("categories") or {}
        location = cats.get("location", "")
        dept = cats.get("team") or cats.get("department") or ""
        commitment = cats.get("commitment", "Full-time")

        desc_plain = item.get("descriptionPlain", "")
        desc_html = item.get("description", "")
        apply_url = item.get("applyUrl") or item.get("hostedUrl") or f"https://jobs.lever.co/{company}/{job_id}"

        workplace_type = cats.get("workplaceType", "").lower()
        is_remote = workplace_type == "remote" or bool(re.search(r"\b(remote|anywhere|virtual)\b", f"{title} {location}", re.I))

        jobs.append({
            "external_id": job_id,
            "title": title,
            "location": location,
            "department": dept,
            "description": desc_plain,
            "description_html": desc_html,
            "apply_url": apply_url,
            "salary_min": None,
            "salary_max": None,
            "salary_currency": "USD",
            "employment_type": commitment,
            "remote": 1 if is_remote else 0,
            "posted_at": item.get("createdAt"),
        })
    return jobs
