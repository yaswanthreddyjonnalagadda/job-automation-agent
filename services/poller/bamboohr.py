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
import requests

from services.poller.query_discovery import clean_location_string
from services.poller.reader_result import ReaderResult
from services.poller.http import (
    make_session,
    get_json,
    BoardNotFound,
    HttpError,
    BadJson,
    HttpTimeout,
)

logger = logging.getLogger("bamboohr_poller")


def _parse_bamboohr_item(item: Dict[str, Any], company_slug: str = "", company_name: str = "") -> Optional[Dict[str, Any]]:
    title = item.get("jobOpeningName") or item.get("title") or ""
    if not title:
        return None

    job_id = str(item.get("id") or item.get("jobOpeningId") or "")
    loc_obj = item.get("location") or {}
    if isinstance(loc_obj, dict):
        city = loc_obj.get("city", "")
        state = loc_obj.get("state", "")
        loc_str = f"{city}, {state}".strip(", ") if city or state else ""
    else:
        loc_str = str(loc_obj)

    is_remote = bool(
        item.get("isRemote") or item.get("telecommuting") or re.search(r"\bremote\b", f"{title} {loc_str}", re.I)
    )
    apply_url = item.get("applyUrl") or f"https://{company_slug}.bamboohr.com/careers/{job_id}"

    return {
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
    }


def parse_bamboohr_response(data: Any, company_slug: str = "", company_name: str = "") -> ReaderResult:
    """Parse BambooHR response into standardized ReaderResult."""
    items: List[Any] = []
    if isinstance(data, dict):
        items = data.get("result", []) or data.get("jobs", [])
    elif isinstance(data, list):
        items = data
    else:
        return ReaderResult(postings=[], complete=False, error="bad_json")

    jobs: List[Dict[str, Any]] = []
    for raw in items:
        if isinstance(raw, dict):
            parsed = _parse_bamboohr_item(raw, company_slug=company_slug, company_name=company_name)
            if parsed:
                jobs.append(parsed)

    return ReaderResult(postings=jobs, complete=True, error=None)


def read_bamboohr_board(
    company_slug: str,
    company_name: str = "",
    timeout: float = 10.0,
    session: Optional[requests.Session] = None,
) -> ReaderResult:
    """Read BambooHR board from JSON endpoints, returning ReaderResult."""
    s = session or make_session("bamboohr")
    urls = [
        f"https://{company_slug}.bamboohr.com/jobs/embed2List/?json=true",
        f"https://{company_slug}.bamboohr.com/careers/list",
    ]

    data = None
    last_error_code = "board_not_found"

    for url in urls:
        try:
            data = get_json(s, url)
            break
        except BoardNotFound:
            last_error_code = "board_not_found"
            continue
        except HttpError as exc:
            last_error_code = f"http_{exc.status}"
            continue
        except (requests.exceptions.Timeout, HttpTimeout):
            last_error_code = "timeout"
            continue
        except (BadJson, json.JSONDecodeError):
            last_error_code = "bad_json"
            continue

    if data is None:
        return ReaderResult(postings=[], complete=False, error=last_error_code)

    return parse_bamboohr_response(data, company_slug=company_slug, company_name=company_name)


def fetch_bamboohr_jobs(
    company_slug: str,
    company_name: str,
    timeout: float = 10.0,
) -> List[Dict[str, Any]]:
    """Backward compatible entrypoint returning list of job dicts."""
    return read_bamboohr_board(company_slug=company_slug, company_name=company_name, timeout=timeout).postings


poll_bamboohr = fetch_bamboohr_jobs
