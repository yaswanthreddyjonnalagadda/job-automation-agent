"""HTTP client with resilient exponential backoff and error classification."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple, Union
import requests

logger = logging.getLogger("poller.http")

RETRY_STATUSES = {403, 429, 500, 502, 503, 504}
BACKOFF_DELAYS = [1, 2, 4, 8, 16, 32]
REQUEST_TIMEOUT: Tuple[float, float] = (10.0, 30.0)
USER_AGENT = "JobAutomationAgent/1.0 (+https://github.com/yaswanthreddyjonnalagadda/job-automation-agent)"


class BoardNotFound(Exception):
    """Raised when an ATS board returns 404 Not Found (no retry)."""


class HttpError(Exception):
    """Raised on non-retried HTTP error statuses or after retry exhaustion."""
    def __init__(self, status: int, message: str = ""):
        super().__init__(message or f"HTTP {status}")
        self.status = status


class BadJson(Exception):
    """Raised when response body cannot be parsed as JSON."""


class HttpTimeout(Exception):
    """Raised when an HTTP request times out repeatedly and gives up."""


def _sleep(seconds: float) -> None:
    """Overridable sleep helper for test speed."""
    time.sleep(seconds)


def make_session(provider: str) -> requests.Session:
    """Create a new requests.Session per provider with non-shared headers."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    p_lower = provider.lower()
    if p_lower == "workday":
        session.headers["Accept"] = "application/json"
        session.headers["Content-Type"] = "application/json"
    elif p_lower in ("lever", "ashby", "smartrecruiters", "greenhouse"):
        session.headers["Accept"] = "application/json"

    return session


def _parse_retry_after(resp: Optional[requests.Response], default_delay: float) -> float:
    if resp is not None:
        header_val = resp.headers.get("Retry-After")
        if header_val:
            try:
                return max(0.0, float(header_val))
            except (ValueError, TypeError):
                pass
    return default_delay


def _execute_request(
    session: requests.Session,
    method: str,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    body: Optional[Union[Dict[str, Any], List[Any]]] = None,
) -> Any:
    max_tries = len(BACKOFF_DELAYS)

    for attempt in range(max_tries):
        resp: Optional[requests.Response] = None
        try:
            if method == "GET":
                resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            else:
                resp = session.post(url, json=body, timeout=REQUEST_TIMEOUT)
        except requests.exceptions.Timeout as exc:
            if attempt == max_tries - 1:
                raise HttpTimeout("Request timed out after maximum retries") from exc
            _sleep(BACKOFF_DELAYS[attempt])
            continue
        except requests.exceptions.RequestException as exc:
            if attempt == max_tries - 1:
                raise HttpError(status=500, message=str(exc)) from exc
            _sleep(BACKOFF_DELAYS[attempt])
            continue

        status = resp.status_code
        if status == 404:
            raise BoardNotFound(f"Board not found (404): {url}")

        if status in RETRY_STATUSES:
            if attempt == max_tries - 1:
                raise HttpError(status=status)
            delay = _parse_retry_after(resp, BACKOFF_DELAYS[attempt])
            _sleep(delay)
            continue

        if 400 <= status < 500:
            raise HttpError(status=status)

        if status >= 500:
            if attempt == max_tries - 1:
                raise HttpError(status=status)
            _sleep(BACKOFF_DELAYS[attempt])
            continue

        try:
            return resp.json()
        except Exception as exc:
            raise BadJson(f"Invalid JSON response from {url}: {exc}") from exc

    raise HttpError(status=500, message="Retry loop exhausted without response")


def get_json(session: requests.Session, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """Perform GET request returning decoded JSON with resilient retry."""
    return _execute_request(session, method="GET", url=url, params=params)


def post_json(session: requests.Session, url: str, body: Optional[Union[Dict[str, Any], List[Any]]] = None) -> Any:
    """Perform POST request returning decoded JSON with resilient retry."""
    return _execute_request(session, method="POST", url=url, body=body)
