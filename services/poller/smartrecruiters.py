"""SmartRecruiters ATS Public API Poller."""
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

logger = logging.getLogger("poller.smartrecruiters")


def _parse_smartrecruiters_item(item: Dict[str, Any], slug: str = "") -> Dict[str, Any]:
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

    fallback_url = f"https://jobs.smartrecruiters.com/{slug}/{job_id}" if slug else ""

    return {
        "external_id": job_id,
        "title": title,
        "location": location,
        "department": dept,
        "description": "",
        "description_html": "",
        "apply_url": fallback_url,
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "USD",
        "employment_type": emp_type,
        "remote": is_remote,
        "posted_at": item.get("releasedDate"),
    }


def parse_smartrecruiters_response(data: Any, slug: str = "") -> ReaderResult:
    """Parse SmartRecruiters postings response into standardized ReaderResult."""
    if not isinstance(data, dict) or "content" not in data:
        return ReaderResult(postings=[], complete=False, error="bad_json")

    postings: List[Dict[str, Any]] = []
    content = data.get("content", [])
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                postings.append(_parse_smartrecruiters_item(item, slug=slug))

    total_found = data.get("totalFound", len(postings))
    complete = len(postings) >= total_found

    return ReaderResult(postings=postings, complete=complete, error=None)


def read_smartrecruiters_board(slug: str, session: Optional[requests.Session] = None) -> ReaderResult:
    """Read SmartRecruiters public board, paging through all results.

    Requests offset += limit until offset >= totalFound.
    If a later page fails, returns the postings accumulated so far with
    complete=False and the error code from the failing page.
    """
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    s = session or make_session("smartrecruiters")

    accumulated: List[Dict[str, Any]] = []
    limit = 100  # SmartRecruiters default page size
    offset = 0

    while True:
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        try:
            data = get_json(s, url, params=params)
        except BoardNotFound:
            if offset == 0:
                return ReaderResult(postings=[], complete=False, error="board_not_found")
            # Board disappeared mid-pagination — preserve what we have
            return ReaderResult(postings=accumulated, complete=False, error="board_not_found")
        except HttpError as exc:
            error_code = f"http_{exc.status}"
            if offset == 0:
                return ReaderResult(postings=[], complete=False, error=error_code)
            return ReaderResult(postings=accumulated, complete=False, error=error_code)
        except (requests.exceptions.Timeout, HttpTimeout):
            if offset == 0:
                return ReaderResult(postings=[], complete=False, error="timeout")
            return ReaderResult(postings=accumulated, complete=False, error="timeout")
        except (BadJson, json.JSONDecodeError):
            if offset == 0:
                return ReaderResult(postings=[], complete=False, error="bad_json")
            return ReaderResult(postings=accumulated, complete=False, error="bad_json")

        if not isinstance(data, dict) or "content" not in data:
            if offset == 0:
                return ReaderResult(postings=[], complete=False, error="bad_json")
            return ReaderResult(postings=accumulated, complete=False, error="bad_json")

        content = data.get("content", [])
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    accumulated.append(_parse_smartrecruiters_item(item, slug=slug))

        total_found = data.get("totalFound", len(accumulated))

        # Use the page-level limit if the API echoes it, otherwise keep our default
        page_limit = data.get("limit", limit)
        if page_limit and isinstance(page_limit, int) and page_limit > 0:
            limit = page_limit

        offset += limit

        if offset >= total_found or not content:
            # All pages consumed
            return ReaderResult(postings=accumulated, complete=True, error=None)


def poll_smartrecruiters(slug: str, session: Optional[requests.Session] = None) -> List[Dict[str, Any]]:
    """Backward compatible helper returning list of job dicts."""
    return read_smartrecruiters_board(slug, session=session).postings
