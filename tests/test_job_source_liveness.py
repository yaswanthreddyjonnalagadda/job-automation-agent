"""Offline tests for Job Source Card 8: check jobs no crawl revisits.

services.poller.liveness re-verifies active jobs whose board hasn't been
crawled recently (or is paused) by GETting the job's own apply_url directly
-- no browser, no AI. Two consecutive "expired" signals close the job; a
timeout or a 5xx changes nothing. No test touches a live site, job-agent-db
(port 5432), or data/applications.db -- all storage is a temp-file SQLite
JobStorage and all HTTP is mocked.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from services.diff_engine.storage import JobStorage
from services.poller import liveness

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "job_source" / "liveness"
PROVIDER = "greenhouse"
T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _resp(status=200, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.headers = {}
    return resp


def _session_with(*responses):
    session = MagicMock(spec=requests.Session)
    session.get.side_effect = list(responses)
    return session


@pytest.fixture(autouse=True)
def block_live_network(monkeypatch):
    """Safety guard: fail any real network call and skip retry/pacing sleeps."""
    def _blocked(*args, **kwargs):
        raise RuntimeError("Live network request blocked in unit tests!")

    monkeypatch.setattr("requests.Session.get", _blocked)
    monkeypatch.setattr("requests.Session.post", _blocked)
    monkeypatch.setattr("services.poller.http._sleep", lambda s: None)


@pytest.fixture
def storage(tmp_path):
    db_file = tmp_path / "liveness_test.db"
    return JobStorage(dsn=f"sqlite:///{db_file}")


def _seed_board(storage, board: str, status: str = "live", last_crawl_at=None) -> None:
    storage.register_company({
        "company_name": board, "domain": f"{board}.com", "ats_platform": PROVIDER,
        "board_slug": board, "board_url": f"https://{board}.greenhouse.io",
        "api_endpoint": "", "discovered_via": "test",
    })
    last_crawl_str = last_crawl_at.isoformat() if last_crawl_at else None
    with storage._conn:
        storage._conn.execute(
            "UPDATE company_directory SET status = ?, last_crawl_at = ? WHERE ats_platform = ? AND board_slug = ?",
            (status, last_crawl_str, PROVIDER, board),
        )


def _seed_job(storage, board: str, ext_id: str, strikes: int = 0, apply_url: str = None) -> int:
    posting = {
        "external_id": ext_id, "title": "Network Engineer", "location": "Remote",
        "description": "A real job.", "apply_url": apply_url or f"https://{board}.com/jobs/{ext_id}",
    }
    job_id, _label = storage.upsert_posting(PROVIDER, board, posting, T0)
    if strikes:
        with storage._conn:
            storage._conn.execute("UPDATE jobs SET liveness_strikes = ? WHERE id = ?", (strikes, job_id))
    return job_id


# ---------------------------------------------------------------------------
# Expired / live signal handling
# ---------------------------------------------------------------------------

def test_expired_page_once_adds_one_strike_job_stays_active(storage):
    _seed_board(storage, "acme", status="paused")
    job_id = _seed_job(storage, "acme", "job_1")
    session = _session_with(_resp(200, _html("expired.html")))

    counts = liveness.check_liveness(storage, session=session, now=T0)

    row = storage.get_job_by_id(job_id)
    assert row["liveness_strikes"] == 1
    assert row["status"] == "active"
    assert counts == {"checked": 1, "still_open": 0, "strikes_added": 1, "closed": 0, "errors": 0}


def test_expired_twice_in_a_row_removes_the_job(storage):
    _seed_board(storage, "acme", status="paused")
    job_id = _seed_job(storage, "acme", "job_1", strikes=1)
    session = _session_with(_resp(200, _html("expired.html")))

    counts = liveness.check_liveness(storage, session=session, now=T0)

    row = storage.get_job_by_id(job_id)
    assert row["liveness_strikes"] == 2
    assert row["status"] == "removed"
    assert row["removed_at"] is not None
    assert counts == {"checked": 1, "still_open": 0, "strikes_added": 1, "closed": 1, "errors": 0}


def test_live_page_after_one_strike_resets_strikes_to_zero(storage):
    _seed_board(storage, "acme", status="paused")
    job_id = _seed_job(storage, "acme", "job_1", strikes=1)
    session = _session_with(_resp(200, _html("live.html")))

    counts = liveness.check_liveness(storage, session=session, now=T0)

    row = storage.get_job_by_id(job_id)
    assert row["liveness_strikes"] == 0
    assert row["status"] == "active"
    assert counts == {"checked": 1, "still_open": 1, "strikes_added": 0, "closed": 0, "errors": 0}


def test_410_counts_as_expired(storage):
    _seed_board(storage, "acme", status="paused")
    job_id = _seed_job(storage, "acme", "job_1")
    session = _session_with(_resp(410, ""))

    counts = liveness.check_liveness(storage, session=session, now=T0)

    row = storage.get_job_by_id(job_id)
    assert row["liveness_strikes"] == 1
    assert row["status"] == "active"
    assert counts == {"checked": 1, "still_open": 0, "strikes_added": 1, "closed": 0, "errors": 0}


def test_timeout_is_not_a_strike(storage):
    _seed_board(storage, "acme", status="paused")
    job_id = _seed_job(storage, "acme", "job_1")
    session = MagicMock(spec=requests.Session)
    session.get.side_effect = requests.exceptions.Timeout("timed out")

    counts = liveness.check_liveness(storage, session=session, now=T0)

    row = storage.get_job_by_id(job_id)
    assert row["liveness_strikes"] == 0
    assert row["status"] == "active"
    assert counts == {"checked": 1, "still_open": 0, "strikes_added": 0, "closed": 0, "errors": 1}


def test_503_is_not_a_strike(storage):
    _seed_board(storage, "acme", status="paused")
    job_id = _seed_job(storage, "acme", "job_1", strikes=1)
    session = MagicMock(spec=requests.Session)
    session.get.return_value = _resp(503, "")

    counts = liveness.check_liveness(storage, session=session, now=T0)

    row = storage.get_job_by_id(job_id)
    assert row["liveness_strikes"] == 1   # unchanged, not reset and not incremented
    assert row["status"] == "active"
    assert counts == {"checked": 1, "still_open": 0, "strikes_added": 0, "closed": 0, "errors": 1}


# ---------------------------------------------------------------------------
# Candidate selection: paused or stale boards only
# ---------------------------------------------------------------------------

def test_only_jobs_on_paused_or_stale_boards_are_picked_fresh_board_skipped(storage):
    _seed_board(storage, "paused-co", status="paused", last_crawl_at=T0)
    _seed_board(storage, "stale-co", status="live", last_crawl_at=T0 - timedelta(days=8))
    _seed_board(storage, "fresh-co", status="live", last_crawl_at=T0 - timedelta(hours=1))

    paused_job = _seed_job(storage, "paused-co", "p1")
    stale_job = _seed_job(storage, "stale-co", "s1")
    fresh_job = _seed_job(storage, "fresh-co", "f1")

    session = _session_with(_resp(200, _html("live.html")), _resp(200, _html("live.html")))

    counts = liveness.check_liveness(storage, session=session, now=T0)

    assert counts["checked"] == 2
    requested_urls = {c.args[0] if c.args else c.kwargs.get("url") for c in session.get.call_args_list}
    assert requested_urls == {
        storage.get_job_by_id(paused_job)["apply_url"],
        storage.get_job_by_id(stale_job)["apply_url"],
    }
    # the fresh board's job was never requested, so its row is untouched
    fresh_row = storage.get_job_by_id(fresh_job)
    assert fresh_row["liveness_strikes"] == 0
    assert fresh_row["status"] == "active"


def test_requests_to_the_same_host_wait_at_least_one_second_apart(storage, monkeypatch):
    sleeps = []
    monkeypatch.setattr("services.poller.http._sleep", lambda s: sleeps.append(s))

    _seed_board(storage, "acme", status="paused")
    _seed_job(storage, "acme", "job_1", apply_url="https://acme.com/jobs/job_1")
    _seed_job(storage, "acme", "job_2", apply_url="https://acme.com/jobs/job_2")
    session = _session_with(_resp(200, _html("live.html")), _resp(200, _html("live.html")))

    liveness.check_liveness(storage, session=session, now=T0)

    assert any(s > 0 for s in sleeps), "no pacing delay was observed between same-host requests"
