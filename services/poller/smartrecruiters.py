"""SmartRecruiters ATS Public API Poller."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger("poller.smartrecruiters")


def poll_smartrecruiters(slug: str, session: Optional[requests.Session] = None) -> List[Dict[str, Any]]:
    """Poll SmartRecruiters public postings endpoint:
    GET https://api.smartrecruiters.com/v1/companies/{slug}/postings
    """
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    s = session or requests.Session()
    try:
        resp = s.get(url, timeout=15)
        if resp.status_code == 404:
            logger.warning("SmartRecruiters company slug not found: %s", slug)
            return []
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error("Error polling SmartRecruiters slug %s: %s", slug, exc)
        return []

    jobs: List[Dict[str, Any]] = []
    for item in data.get("content", []):
        job_id = str(item.get("id", ""))
        title = item.get("name", "")
        loc_obj = item.get("location") or {}
        city = loc_obj.get("city", "")
        region = loc_obj.get("region", "")
        country = loc_obj.get("country", "")
        loc_parts = [p for p in [city, region, country] if p]
        location = ", ".join(loc_parts) if loc_parts else "Not Specified"

        dept_obj = item.get("department") or {}
        dept = dept_obj.get("label", "") if isinstance(dept_obj, dict) else str(dept_obj)

        is_remote = bool(loc_obj.get("remote")) or bool(
            re.search(r"\b(remote|anywhere|virtual)\b", f"{title} {location}", re.I)
        )

        emp_type_obj = item.get("typeOfEmployment") or {}
        emp_type = emp_type_obj.get("label", "full-time").lower() if isinstance(emp_type_obj, dict) else "full-time"

        apply_url = f"https://jobs.smartrecruiters.com/{slug}/{job_id}"

        jobs.append({
            "external_id": job_id,
            "title": title,
            "location": location,
            "department": dept,
            "description": "",
            "description_html": "",
            "apply_url": apply_url,
            "salary_min": None,
            "salary_max": None,
            "salary_currency": "USD",
            "employment_type": emp_type,
            "remote": is_remote,
            "posted_at": item.get("releasedDate"),
        })

    return jobs

