"""Lever ATS Public API Poller."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional
import requests

from services.poller.reader_result import ReaderResult

logger = logging.getLogger("poller.lever")


def _parse_lever_item(item: Dict[str, Any], company: str = "") -> Dict[str, Any]:
    job_id = str(item.get("id", ""))
    title = item.get("text", "")
    cats = item.get("categories") or {}
    location = cats.get("location", "")
    dept = cats.get("team") or cats.get("department") or ""
    commitment = cats.get("commitment", "Full-time")

    desc_plain = item.get("descriptionPlain", "")
    desc_html = item.get("description", "")
    fallback_url = f"https://jobs.lever.co/{company}/{job_id}" if company else ""
    apply_url = item.get("applyUrl") or item.get("hostedUrl") or fallback_url

    workplace_type = cats.get("workplaceType", "").lower()
    is_remote = workplace_type == "remote" or bool(
        re.search(r"\b(remote|anywhere|virtual)\b", f"{title} {location}", re.I)
    )

    return {
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
        "remote": bool(is_remote),
        "posted_at": item.get("createdAt"),
    }


def parse_lever_response(data: Any, company: str = "") -> ReaderResult:
    """Parse Lever postings response into standardized ReaderResult."""
    if not isinstance(data, list):
        return ReaderResult(postings=[], complete=False, error="Malformed response: expected list of postings")

    jobs: List[Dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict):
            jobs.append(_parse_lever_item(item, company=company))
    return ReaderResult(postings=jobs, complete=True, error=None)


def read_lever_board(company: str, session: Optional[requests.Session] = None) -> ReaderResult:
    """Read Lever public board returning ReaderResult:
    GET https://api.lever.co/v0/postings/{company}
    """
    url = f"https://api.lever.co/v0/postings/{company}"
    s = session or requests.Session()
    s.headers.setdefault(
        "User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    )
    try:
        resp = s.get(url, headers={"Accept": "application/json"}, timeout=15)
        if resp.status_code == 404:
            logger.warning("Lever company not found: %s", company)
            return ReaderResult(postings=[], complete=False, error=f"404 Not Found: {company}")
        resp.raise_for_status()
        return parse_lever_response(resp.json(), company=company)
    except Exception as exc:
        logger.error("Error polling Lever company %s: %s", company, exc)
        return ReaderResult(postings=[], complete=False, error=str(exc))


def poll_lever(company: str, session: Optional[requests.Session] = None) -> List[Dict[str, Any]]:
    """Backward compatible helper returning list of job dicts."""
    return read_lever_board(company, session=session).postings
