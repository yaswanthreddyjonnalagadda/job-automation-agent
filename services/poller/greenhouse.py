"""Greenhouse ATS Public API Poller."""
from __future__ import annotations

import logging
import html
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

logger = logging.getLogger("poller.greenhouse")


def clean_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_greenhouse_item(item: Dict[str, Any], slug: str = "") -> Dict[str, Any]:
    job_id = str(item.get("id", ""))
    title = item.get("title", "")
    location_obj = item.get("location") or {}
    location = location_obj.get("name", "") if isinstance(location_obj, dict) else str(location_obj)
    content_html = item.get("content", "")
    plain_desc = clean_html(content_html)
    departments = [d.get("name") for d in item.get("departments", []) if d.get("name")]
    dept = ", ".join(departments) if departments else ""

    is_remote = bool(re.search(r"\b(remote|anywhere|virtual)\b", f"{title} {location}", re.I))
    fallback_url = f"https://boards.greenhouse.io/{slug}/jobs/{job_id}" if slug else ""

    return {
        "external_id": job_id,
        "title": title,
        "location": location,
        "department": dept,
        "description": plain_desc,
        "description_html": content_html,
        "apply_url": item.get("absolute_url") or fallback_url,
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "USD",
        "employment_type": "full-time",
        "remote": bool(is_remote),
        "posted_at": item.get("updated_at"),
    }


def parse_greenhouse_response(data: Any, slug: str = "") -> ReaderResult:
    """Parse Greenhouse board response into standardized ReaderResult."""
    if not isinstance(data, dict) or "jobs" not in data:
        return ReaderResult(postings=[], complete=False, error="bad_json")

    jobs: List[Dict[str, Any]] = []
    for item in data.get("jobs", []):
        jobs.append(_parse_greenhouse_item(item, slug=slug))
    return ReaderResult(postings=jobs, complete=True, error=None)


def read_greenhouse_board(slug: str, session: Optional[requests.Session] = None) -> ReaderResult:
    """Read Greenhouse public board returning ReaderResult:
    GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
    """
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    s = session or make_session("greenhouse")
    try:
        data = get_json(s, url)
        return parse_greenhouse_response(data, slug=slug)
    except BoardNotFound:
        return ReaderResult(postings=[], complete=False, error="board_not_found")
    except HttpError as exc:
        return ReaderResult(postings=[], complete=False, error=f"http_{exc.status}")
    except (requests.exceptions.Timeout, HttpTimeout):
        return ReaderResult(postings=[], complete=False, error="timeout")
    except (BadJson, json.JSONDecodeError):
        return ReaderResult(postings=[], complete=False, error="bad_json")


def poll_greenhouse(slug: str, session: Optional[requests.Session] = None) -> List[Dict[str, Any]]:
    """Backward compatible helper returning list of job dicts."""
    return read_greenhouse_board(slug, session=session).postings
