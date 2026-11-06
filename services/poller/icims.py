"""iCIMS ATS search-page reader.

Ports the request and card-parsing logic of discovery_sources.fetch_icims_feed
(agent repo): the https://{host}/jobs/search page is read with ss=1 and each
iCIMS_JobCardItem card is parsed for its job link, title, and place.

Two deliberate deviations from the discovery fetch, both required by the
poller contract: (1) the whole board is listed (no per-title search) and every
result page is followed, so complete=True only after the last page; (2)
per-job detail pages are not opened (list-only, like the SmartRecruiters
reader), so description/posted_at stay empty on the listing.
"""
from __future__ import annotations

import html as html_lib
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests

from services.poller import http as poller_http
from services.poller.http import (
    BoardNotFound,
    HttpError,
    HttpTimeout,
    make_session,
)
from services.poller.reader_result import ReaderResult

logger = logging.getLogger("poller.icims")

# Card, link, and place patterns ported from discovery_sources.fetch_icims_feed.
_ICIMS_CARD = re.compile(r'<li class="iCIMS_JobCardItem">(.*?)</li>', re.S)
_ICIMS_CARD_OPEN = re.compile(r'<li class="iCIMS_JobCardItem">')
_ICIMS_LINK = re.compile(
    r'<a href="(https://[^"]+?/jobs/\d+/[^"]*?/job)[^"]*"[^>]*>.*?<h3[^>]*>\s*(.*?)\s*</h3>',
    re.S,
)
_ICIMS_PLACE = re.compile(
    r'iCIMS_JobHeaderData"[^>]*>\s*<span[^>]*>\s*([^<]+?)\s*</span>', re.S
)
_ICIMS_LOCATION = re.compile(
    r'Job Locations</span>\s*<span[^>]*>\s*([^<]+?)\s*</span>', re.S
)
_ICIMS_ID = re.compile(r"/jobs/(\d+)/")
_ICIMS_SEARCH_HREF = re.compile(r'<a[^>]+href="([^"]*?/jobs/search[^"]*?)"', re.S | re.I)
_REMOTE = re.compile(r"\b(remote|anywhere|virtual)\b", re.I)

MAX_PAGES = 50


def _plain(raw: str) -> str:
    text = re.sub(
        r"<(script|style)[^>]*>.*?</\1>",
        " ", html_lib.unescape(raw or ""), flags=re.S | re.I,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(html_lib.unescape(text).split())


def _parse_icims_item(card: str) -> Optional[Dict[str, Any]]:
    link = _ICIMS_LINK.search(card)
    if not link:
        return None
    apply_url = link.group(1)
    job_id = _ICIMS_ID.search(apply_url)
    if not job_id:
        return None
    title = _plain(link.group(2))
    place = _ICIMS_LOCATION.search(card) or _ICIMS_PLACE.search(card)
    location = _plain(place.group(1)) if place else ""
    return {
        "external_id": job_id.group(1),
        "title": title,
        "location": location,
        "department": "",
        "description": "",
        "description_html": "",
        "apply_url": apply_url,
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "USD",
        "employment_type": "",
        "remote": bool(_REMOTE.search(f"{title} {location}")),
        "posted_at": None,
    }


def parse_icims_response(html_text: str, board: str = "") -> ReaderResult:
    """Parse one iCIMS search page into a ReaderResult.

    `board` is kept for signature symmetry with the other readers (their
    fallback URLs need it; iCIMS links are absolute, so it is unused here).
    A page that opens a job card it never closes is a truncated body and
    returns error bad_json with whatever intact postings came before it.
    """
    _ = board
    text = html_text or ""
    if not text.strip():
        return ReaderResult(postings=[], complete=False, error="bad_json")
    cards = _ICIMS_CARD.findall(text)
    postings: List[Dict[str, Any]] = []
    for card in cards:
        item = _parse_icims_item(card)
        if item is not None:
            postings.append(item)
    if len(_ICIMS_CARD_OPEN.findall(text)) > len(cards):
        return ReaderResult(postings=postings, complete=False, error="bad_json")
    return ReaderResult(postings=postings, complete=True, error=None)


def _next_search_url(html_text: str, page_url: str, visited: set) -> Optional[str]:
    for match in _ICIMS_SEARCH_HREF.finditer(html_text or ""):
        absolute = urljoin(page_url, html_lib.unescape(match.group(1)))
        if absolute not in visited:
            return absolute
    return None


def _get_html(session: requests.Session, url: str, params: Optional[Dict[str, Any]] = None) -> str:
    """GET a search page under the shared poller retry policy.

    http.get_json cannot serve iCIMS (HTML, not JSON), so this mirrors its
    status/exception mapping exactly and calls poller_http._sleep through the
    module, so the shared test patch (services.poller.http._sleep) governs it.
    """
    max_tries = len(poller_http.BACKOFF_DELAYS)
    for attempt in range(max_tries):
        try:
            resp = session.get(url, params=params, timeout=poller_http.REQUEST_TIMEOUT)
        except requests.exceptions.Timeout as exc:
            if attempt == max_tries - 1:
                raise HttpTimeout("Request timed out after maximum retries") from exc
            poller_http._sleep(poller_http.BACKOFF_DELAYS[attempt])
            continue
        except requests.exceptions.RequestException as exc:
            if attempt == max_tries - 1:
                raise HttpError(status=500, message=str(exc)) from exc
            poller_http._sleep(poller_http.BACKOFF_DELAYS[attempt])
            continue

        status = resp.status_code
        if status == 404:
            raise BoardNotFound(f"Board not found (404): {url}")
        if status in poller_http.RETRY_STATUSES:
            if attempt == max_tries - 1:
                raise HttpError(status=status)
            delay = poller_http._parse_retry_after(resp, poller_http.BACKOFF_DELAYS[attempt])
            poller_http._sleep(delay)
            continue
        if 400 <= status < 500:
            raise HttpError(status=status)
        if status >= 500:
            if attempt == max_tries - 1:
                raise HttpError(status=status)
            poller_http._sleep(poller_http.BACKOFF_DELAYS[attempt])
            continue
        return resp.text
    raise HttpError(status=500, message="Retry loop exhausted without response")


def read_icims_board(board: str, session: Optional[requests.Session] = None) -> ReaderResult:
    """Read an iCIMS portal board returning ReaderResult.

    GET https://{board}/jobs/search (ss=1, whole list, no per-title search),
    following every further /jobs/search result page. complete=True only after
    the last page was read; any error keeps the postings gathered so far with
    complete=False and error board_not_found, http_<status>, timeout, or
    bad_json. `board` is the portal host (a full URL is tolerated, the host is
    taken from it).
    """
    host = re.sub(r"^https?://", "", (board or "").strip()).split("/")[0]
    if not host:
        return ReaderResult(postings=[], complete=False, error="board_not_found")
    s = session or make_session("icims")
    start_url = f"https://{host}/jobs/search"
    first_params = {"ss": 1, "in_iframe": 1}
    postings: List[Dict[str, Any]] = []
    seen: set = set()
    visited: set = set()
    url: Optional[str] = start_url
    try:
        for _ in range(MAX_PAGES):
            if url is None:
                return ReaderResult(postings=postings, complete=True, error=None)
            visited.add(url)
            text = _get_html(s, url, params=first_params if url == start_url else None)
            page = parse_icims_response(text, board=host)
            for posting in page.postings:
                if posting["external_id"] not in seen:
                    seen.add(posting["external_id"])
                    postings.append(posting)
            if page.error is not None:
                return ReaderResult(postings=postings, complete=False, error=page.error)
            url = _next_search_url(text, url, visited)
        logger.warning("iCIMS board %s exceeded %d pages; stopping", host, MAX_PAGES)
        return ReaderResult(postings=postings, complete=False, error=None)
    except BoardNotFound:
        return ReaderResult(postings=postings, complete=False, error="board_not_found")
    except HttpError as exc:
        return ReaderResult(postings=postings, complete=False, error=f"http_{exc.status}")
    except (requests.exceptions.Timeout, HttpTimeout):
        return ReaderResult(postings=postings, complete=False, error="timeout")


def poll_icims(board: str, session: Optional[requests.Session] = None) -> List[Dict[str, Any]]:
    """Backward compatible helper returning list of job dicts."""
    return read_icims_board(board, session=session).postings