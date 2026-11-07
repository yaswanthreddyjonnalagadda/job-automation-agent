"""Ashby ATS Public API Poller."""
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

logger = logging.getLogger("poller.ashby")


def clean_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_ashby_item(item: Dict[str, Any], org: str = "") -> Dict[str, Any]:
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

    is_remote = bool(item.get("isRemote")) or bool(
        re.search(r"\b(remote|anywhere|virtual)\b", f"{title} {location}", re.I)
    )
    fallback_url = f"https://jobs.ashbyhq.com/{org}/{job_id}" if org else ""
    apply_url = item.get("applyUrl") or item.get("jobUrl") or fallback_url

    return {
        "external_id": job_id,
        "title": title,
        "location": location,
        "department": dept,
        "description": desc_plain,
        "description_html": desc_html,
        "apply_url": apply_url,
        "salary_min": min_sal,
        "salary_max": max_sal,
        "salary_currency": currency,
        "employment_type": employment_type,
        "remote": bool(is_remote),
        "posted_at": item.get("publishedAt"),
    }


def parse_ashby_response(data: Any, org: str = "") -> ReaderResult:
    """Parse Ashby job board response into standardized ReaderResult."""
    if not isinstance(data, dict) or "jobs" not in data:
        return ReaderResult(postings=[], complete=False, error="bad_json")

    jobs: List[Dict[str, Any]] = []
    for item in data.get("jobs", []):
        if isinstance(item, dict):
            jobs.append(_parse_ashby_item(item, org=org))
    return ReaderResult(postings=jobs, complete=True, error=None)


def read_ashby_board(org: str, session: Optional[requests.Session] = None) -> ReaderResult:
    """Read Ashby public board returning ReaderResult:
    GET https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true
    """
    url = f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true"
    s = session or make_session("ashby")
    try:
        data = get_json(s, url)
        return parse_ashby_response(data, org=org)
    except BoardNotFound:
        return ReaderResult(postings=[], complete=False, error="board_not_found")
    except HttpError as exc:
        return ReaderResult(postings=[], complete=False, error=f"http_{exc.status}")
    except (requests.exceptions.Timeout, HttpTimeout):
        return ReaderResult(postings=[], complete=False, error="timeout")
    except (BadJson, json.JSONDecodeError):
        return ReaderResult(postings=[], complete=False, error="bad_json")


def poll_ashby(org: str, session: Optional[requests.Session] = None) -> List[Dict[str, Any]]:
    """Backward compatible helper returning list of job dicts."""
    return read_ashby_board(org, session=session).postings
