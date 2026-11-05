"""Workday ATS CXS Public API Poller."""
from __future__ import annotations

import logging
import json
import re
from typing import Any, Dict, List, Optional
import requests

from services.poller.reader_result import ReaderResult
from services.poller.http import (
    make_session,
    post_json,
    BoardNotFound,
    HttpError,
    BadJson,
    HttpTimeout,
)

logger = logging.getLogger("poller.workday")


def parse_workday_url(board_url: str) -> Optional[Dict[str, str]]:
    """Extract tenant, host, and site from a Workday career URL.
    E.g. https://analogdevices.wd1.myworkdayjobs.com/Analog_Devices_External
    -> {'host': 'analogdevices.wd1.myworkdayjobs.com', 'tenant': 'analogdevices', 'site': 'Analog_Devices_External'}
    """
    m = re.match(r"https?://([^/]+)/([^/?#]+)", board_url)
    if not m:
        return None
    host = m.group(1)
    site = m.group(2)
    tenant = host.split(".")[0]
    return {"host": host, "tenant": tenant, "site": site}


def _parse_workday_item(item: Dict[str, Any], host: str = "", site: str = "", board_url: str = "") -> Dict[str, Any]:
    ext_id = item.get("bulletFields", [None])[0] or item.get("externalPath", "").split("/")[-1] or ""
    title = item.get("title", "")
    locations = item.get("locationsText", "")
    posted_on = item.get("postedOn", "")
    external_path = item.get("externalPath", "")
    apply_url = f"https://{host}/{site}{external_path}" if external_path and host and site else board_url

    is_remote = bool(re.search(r"\b(remote|anywhere|virtual)\b", f"{title} {locations}", re.I))

    return {
        "external_id": ext_id,
        "title": title,
        "location": locations,
        "department": "",
        "description": title,
        "description_html": "",
        "apply_url": apply_url,
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "USD",
        "employment_type": "full-time",
        "remote": bool(is_remote),
        "posted_at": posted_on,
    }


def parse_workday_response(data: Any, host: str = "", site: str = "", board_url: str = "") -> ReaderResult:
    """Parse Workday CXS response page into standardized ReaderResult."""
    if not isinstance(data, dict) or "jobPostings" not in data:
        return ReaderResult(postings=[], complete=False, error="bad_json")

    postings: List[Dict[str, Any]] = []
    items = data.get("jobPostings", [])
    if isinstance(items, list):
        for raw in items:
            if isinstance(raw, dict):
                postings.append(_parse_workday_item(raw, host=host, site=site, board_url=board_url))

    total = data.get("total", len(postings))
    complete = len(postings) >= total
    return ReaderResult(postings=postings, complete=complete, error=None)


def read_workday_board(
    board_url: str,
    max_jobs: int = 100,
    session: Optional[requests.Session] = None,
) -> ReaderResult:
    """Read Workday CXS board with pagination, returning ReaderResult."""
    parsed = parse_workday_url(board_url)
    if not parsed:
        logger.warning("Invalid Workday board URL: %s", board_url)
        return ReaderResult(postings=[], complete=False, error="board_not_found")

    host = parsed["host"]
    tenant = parsed["tenant"]
    site = parsed["site"]
    api_url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"

    s = session or make_session("workday")
    jobs: List[Dict[str, Any]] = []
    offset = 0
    limit = 20
    total = None
    complete = False

    while offset < max_jobs:
        payload = {"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""}
        try:
            data = post_json(s, api_url, body=payload)
        except BoardNotFound:
            return ReaderResult(postings=jobs, complete=False, error="board_not_found")
        except HttpError as exc:
            return ReaderResult(postings=jobs, complete=False, error=f"http_{exc.status}")
        except (requests.exceptions.Timeout, HttpTimeout):
            return ReaderResult(postings=jobs, complete=False, error="timeout")
        except (BadJson, json.JSONDecodeError):
            return ReaderResult(postings=jobs, complete=False, error="bad_json")

        page_result = parse_workday_response(data, host=host, site=site, board_url=board_url)
        if page_result.error:
            return ReaderResult(postings=jobs, complete=False, error=page_result.error)

        if not page_result.postings:
            complete = True
            break

        jobs.extend(page_result.postings)
        total = data.get("total", len(jobs))
        offset += limit
        if offset >= total:
            complete = True
            break

    if total is not None and len(jobs) >= total:
        complete = True

    return ReaderResult(postings=jobs, complete=complete, error=None)


def poll_workday(board_url: str, max_jobs: int = 100, session: Optional[requests.Session] = None) -> List[Dict[str, Any]]:
    """Backward compatible helper returning list of job dicts."""
    return read_workday_board(board_url, max_jobs=max_jobs, session=session).postings
