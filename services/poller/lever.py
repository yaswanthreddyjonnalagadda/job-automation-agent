"""Lever ATS Public API Poller."""
from __future__ import annotations

import logging
import json
import re
from typing import Any, Dict, List, Optional
import requests

from services.poller.reader_result import ReaderResult
from services.poller.http import (
    make_session,
    get_json,
    BoardNotFound,
    HttpError,
    BadJson,
    HttpTimeout,
)

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
        return ReaderResult(postings=[], complete=False, error="bad_json")

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
    s = session or make_session("lever")
    try:
        data = get_json(s, url)
        return parse_lever_response(data, company=company)
    except BoardNotFound:
        return ReaderResult(postings=[], complete=False, error="board_not_found")
    except HttpError as exc:
        return ReaderResult(postings=[], complete=False, error=f"http_{exc.status}")
    except (requests.exceptions.Timeout, HttpTimeout):
        return ReaderResult(postings=[], complete=False, error="timeout")
    except (BadJson, json.JSONDecodeError):
        return ReaderResult(postings=[], complete=False, error="bad_json")


def poll_lever(company: str, session: Optional[requests.Session] = None) -> List[Dict[str, Any]]:
    """Backward compatible helper returning list of job dicts."""
    return read_lever_board(company, session=session).postings
