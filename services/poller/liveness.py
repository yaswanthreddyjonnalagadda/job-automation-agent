"""Job Source Card 8: check jobs no crawl revisits.

Active jobs on a board the normal poller hasn't crawled in a while (board
paused, or last_crawl_at older than 7 days / never set) can quietly go stale
between crawls. This module re-verifies each such job directly: GET the
job's own apply_url (no browser, no AI) and read the page for an explicit
"this is gone" signal. Two consecutive expired signals close the job; a
single one just leaves a strike so a transient blip doesn't close a job that
is actually still open. A timeout or a 5xx is not a signal either way --
the fetch simply failed, so nothing about the job changes.

Run as:
    python -m services.poller.liveness --limit 200

WRITES: jobs.liveness_strikes, jobs.status/removed_at (only after 2 strikes
in a row). Never writes company_directory -- the normal poller owns
last_crawl_at/status; this module only reads them to pick candidates.
"""
from __future__ import annotations

import argparse
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

from services.diff_engine.storage import JobStorage
from services.poller import http as poller_http
from services.poller.http import HttpError, HttpTimeout, make_session

logger = logging.getLogger("poller.liveness")

STALE_AFTER = timedelta(days=7)
MIN_HOST_INTERVAL = 1.0  # seconds between requests to the same host
EXPIRED_STATUSES = {404, 410}

# Kept as a list so a new phrase is a one-line addition.
EXPIRED_PHRASES = [
    "no longer available",
    "position has been filled",
    "job not found",
    "no longer accepting",
    "this job has expired",
]


def _is_expired_text(text: str) -> bool:
    lowered = (text or "").lower()
    return any(phrase in lowered for phrase in EXPIRED_PHRASES)


def _host(url: str) -> str:
    return re.sub(r"^https?://", "", url or "").split("/")[0]


def _iso(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _get_page(session: requests.Session, url: str):
    """GET *url* under the shared poller retry/backoff policy, returning (status, text).

    404/410 are returned as a normal result (the job page saying it's gone is
    exactly the signal this module looks for, not a transport failure). Any
    other 4xx, an exhausted 5xx, or an exhausted timeout raises, same as
    services.poller.http._execute_request.
    """
    max_tries = len(poller_http.BACKOFF_DELAYS)
    for attempt in range(max_tries):
        try:
            resp = session.get(url, timeout=poller_http.REQUEST_TIMEOUT)
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
        if status in EXPIRED_STATUSES:
            return status, resp.text
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
        return status, resp.text
    raise HttpError(status=500, message="Retry loop exhausted without response")


def _candidates(storage: JobStorage, limit: int, cutoff_iso: str) -> List[Dict[str, Any]]:
    """Active jobs whose board is paused, or whose board was last crawled before *cutoff_iso* (or never)."""
    sql = """
    SELECT jobs.id AS id, jobs.apply_url AS apply_url, jobs.liveness_strikes AS liveness_strikes
    FROM jobs
    JOIN company_directory cd
      ON cd.ats_platform = jobs.ats_platform AND cd.board_slug = jobs.company_slug
    WHERE jobs.status = 'active'
      AND (cd.status = 'paused' OR cd.last_crawl_at IS NULL OR cd.last_crawl_at < ?)
    ORDER BY jobs.id
    LIMIT ?
    """
    if not storage.is_sqlite:
        sql = sql.replace("?", "%s")
    cur = storage._conn.cursor()
    cur.execute(sql, (cutoff_iso, limit))
    return [dict(row) for row in cur.fetchall()]


def _write(storage: JobStorage, sql: str, params: tuple) -> None:
    if not storage.is_sqlite:
        sql = sql.replace("?", "%s")
    if storage.is_sqlite:
        with storage._conn:
            storage._conn.execute(sql, params)
    else:
        with storage._conn.cursor() as cur:
            cur.execute(sql, params)
        storage._conn.commit()


def _apply_expired(storage: JobStorage, job: Dict[str, Any], now_str: str) -> str:
    strikes = int(job["liveness_strikes"] or 0) + 1
    if strikes >= 2:
        _write(
            storage,
            "UPDATE jobs SET liveness_strikes = ?, status = 'removed', removed_at = ?, updated_at = ? WHERE id = ?",
            (strikes, now_str, now_str, job["id"]),
        )
        return "closed"
    _write(
        storage,
        "UPDATE jobs SET liveness_strikes = ?, updated_at = ? WHERE id = ?",
        (strikes, now_str, job["id"]),
    )
    return "strike"


def _apply_still_open(storage: JobStorage, job: Dict[str, Any], now_str: str) -> None:
    _write(
        storage,
        "UPDATE jobs SET liveness_strikes = 0, updated_at = ? WHERE id = ?",
        (now_str, job["id"]),
    )


def check_liveness(
    storage: JobStorage,
    limit: int = 200,
    now: Optional[Any] = None,
    session: Optional[requests.Session] = None,
) -> Dict[str, int]:
    """Re-verify active jobs on paused/stale boards; return the run's counts."""
    now_dt = now if isinstance(now, datetime) else datetime.now(timezone.utc)
    now_str = _iso(now_dt)
    cutoff_str = _iso(now_dt - STALE_AFTER)
    s = session or make_session("liveness")

    counts = {"checked": 0, "still_open": 0, "strikes_added": 0, "closed": 0, "errors": 0}
    host_last_request: Dict[str, float] = {}

    for job in _candidates(storage, limit, cutoff_str):
        url = job["apply_url"]
        host = _host(url)
        last = host_last_request.get(host)
        if last is not None:
            elapsed = time.monotonic() - last
            if elapsed < MIN_HOST_INTERVAL:
                poller_http._sleep(MIN_HOST_INTERVAL - elapsed)

        counts["checked"] += 1
        try:
            status, text = _get_page(s, url)
        except (HttpError, HttpTimeout, requests.exceptions.Timeout):
            counts["errors"] += 1
            continue
        finally:
            host_last_request[host] = time.monotonic()

        expired = status in EXPIRED_STATUSES or _is_expired_text(text)
        if expired:
            outcome = _apply_expired(storage, job, now_str)
            counts["strikes_added"] += 1
            if outcome == "closed":
                counts["closed"] += 1
        else:
            _apply_still_open(storage, job, now_str)
            counts["still_open"] += 1

    return counts


def run(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m services.poller.liveness",
        description="Re-verify active jobs on paused/stale boards by GETting their apply_url directly.",
    )
    parser.add_argument("--limit", type=int, default=200, help="Maximum number of jobs to check")
    args = parser.parse_args(argv)

    storage = JobStorage()
    counts = check_liveness(storage, limit=args.limit)
    print(
        f"checked: {counts['checked']}\tstill open: {counts['still_open']}\t"
        f"strikes added: {counts['strikes_added']}\tclosed: {counts['closed']}\terrors: {counts['errors']}"
    )


if __name__ == "__main__":  # pragma: no cover
    run()
