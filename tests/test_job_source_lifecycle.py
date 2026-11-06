"""Tests for Job Source Card 1: store jobs and close them safely.

Covers the safe lifecycle contract specified by the card:
  - storage.upsert_posting (new/seen/reopened, description preservation, detail_status)
  - storage.close_unseen (48-hour grace period)
  - diff_engine.apply_read_result(result, provider, board, storage, run_id, now)
  - the suspicious-empty guard (board had >=5 active jobs, read came back empty)
  - company_directory failure tracking (failures_in_row -> paused after 3)
  - run_poll_cycle wiring: a failing reader closes nothing

All tests use a fixed clock (no real wall-clock dependency) and a temp-file
SQLite JobStorage. No live site, no Postgres, no data/applications.db.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from services.diff_engine.diff_engine import apply_read_result
from services.diff_engine.storage import JobStorage
from services.poller.reader_result import ReaderResult

T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
PROVIDER = "greenhouse"
BOARD = "acme"


@pytest.fixture
def storage(tmp_path):
    db_file = tmp_path / "lifecycle_test.db"
    return JobStorage(dsn=f"sqlite:///{db_file}")


def _posting(ext_id: str, title: str = "Network Engineer", description: str = "A real job.") -> dict:
    return {
        "external_id": ext_id,
        "title": title,
        "location": "Remote",
        "description": description,
        "apply_url": f"https://acme.com/jobs/{ext_id}",
    }


def _rr(postings, complete=True, error=None) -> ReaderResult:
    return ReaderResult(postings=postings, complete=complete, error=error)


# ---------------------------------------------------------------------------
# upsert_posting: new / seen / reopened / description preservation
# ---------------------------------------------------------------------------

def test_new_posting_creates_one_row_active_with_matching_first_and_last_seen(storage):
    job_id, label = storage.upsert_posting(PROVIDER, BOARD, _posting("job_1"), T0)
    assert label == "new"
    row = storage.get_job_by_id(job_id)
    assert row["status"] == "active"
    assert row["first_seen_at"] == row["last_seen_at"]


def test_same_posting_twice_still_one_row_last_seen_moves(storage):
    job_id_1, label_1 = storage.upsert_posting(PROVIDER, BOARD, _posting("job_1"), T0)
    assert label_1 == "new"
    t1 = T0 + timedelta(hours=5)
    job_id_2, label_2 = storage.upsert_posting(PROVIDER, BOARD, _posting("job_1"), t1)
    assert label_2 == "seen"
    assert job_id_2 == job_id_1

    rows_sql = storage.get_active_job_ids(PROVIDER, BOARD)
    assert rows_sql == {"job_1"}
    row = storage.get_job_by_id(job_id_1)
    assert row["last_seen_at"] == t1.isoformat()


def test_removed_posting_reappears_same_id_active_removed_at_cleared_first_seen_unchanged(storage):
    job_id, _ = storage.upsert_posting(PROVIDER, BOARD, _posting("job_1"), T0)
    first_seen = storage.get_job_by_id(job_id)["first_seen_at"]

    # Goes quiet for 49h, then a complete read closes it.
    closed = storage.close_unseen(PROVIDER, BOARD, older_than=T0 + timedelta(hours=1), now=T0 + timedelta(hours=49))
    assert closed == 1
    row = storage.get_job_by_id(job_id)
    assert row["status"] == "removed"
    assert row["removed_at"] is not None

    # It reappears.
    reappear_time = T0 + timedelta(hours=100)
    job_id_2, label = storage.upsert_posting(PROVIDER, BOARD, _posting("job_1"), reappear_time)
    assert job_id_2 == job_id
    assert label == "reopened"
    row2 = storage.get_job_by_id(job_id)
    assert row2["status"] == "active"
    assert row2["removed_at"] is None
    assert row2["first_seen_at"] == first_seen


def test_second_read_with_empty_description_keeps_the_stored_description(storage):
    job_id, _ = storage.upsert_posting(PROVIDER, BOARD, _posting("job_1", description="Full JD text."), T0)
    storage.upsert_posting(PROVIDER, BOARD, _posting("job_1", description=""), T0 + timedelta(hours=1))
    row = storage.get_job_by_id(job_id)
    assert row["description"] == "Full JD text."
    assert row["detail_status"] == "done"


def test_posting_with_empty_description_is_pending_and_excluded_from_open_jobs(storage):
    job_id, _ = storage.upsert_posting(PROVIDER, BOARD, _posting("job_1", description=""), T0)
    row = storage.get_job_by_id(job_id)
    assert row["detail_status"] == "pending"

    cur = storage._conn.cursor()
    cur.execute("SELECT external_id FROM open_jobs WHERE external_id = ?", ("job_1",))
    assert cur.fetchone() is None


def test_two_postings_same_company_title_location_different_external_id_are_two_rows(storage):
    job_a = {
        "external_id": "job-101",
        "ats_platform": PROVIDER,
        "company_slug": BOARD,
        "company_name": "Acme Corp",
        "title": "Senior Network Engineer",
        "location": "Fairfax, VA",
        "apply_url": "https://boards.greenhouse.io/acme/jobs/101",
    }
    job_b = dict(job_a, external_id="job-202", apply_url="https://boards.greenhouse.io/acme/jobs/202")

    id1 = storage.insert_or_merge_job(job_a, source="greenhouse")
    id2 = storage.insert_or_merge_job(job_b, source="greenhouse")
    assert id1 != id2


# ---------------------------------------------------------------------------
# close_unseen: 48-hour grace period
# ---------------------------------------------------------------------------

def test_complete_read_without_job_seen_49h_ago_is_removed(storage):
    storage.upsert_posting(PROVIDER, BOARD, _posting("job_x"), T0)
    now = T0 + timedelta(hours=49)
    res = apply_read_result(_rr([]), PROVIDER, BOARD, storage, now=now)
    assert res["removed_count"] == 1
    assert storage.get_active_job_ids(PROVIDER, BOARD) == set()


def test_complete_read_without_job_seen_10h_ago_stays_active(storage):
    storage.upsert_posting(PROVIDER, BOARD, _posting("job_x"), T0)
    now = T0 + timedelta(hours=10)
    res = apply_read_result(_rr([_posting("other")]), PROVIDER, BOARD, storage, now=now)
    assert res["removed_count"] == 0
    assert "job_x" in storage.get_active_job_ids(PROVIDER, BOARD)


def test_incomplete_read_without_job_seen_100h_ago_stays_active(storage):
    storage.upsert_posting(PROVIDER, BOARD, _posting("job_x"), T0)
    now = T0 + timedelta(hours=100)
    res = apply_read_result(_rr([], complete=False, error="timeout"), PROVIDER, BOARD, storage, now=now)
    assert res["removed_count"] == 0
    assert "job_x" in storage.get_active_job_ids(PROVIDER, BOARD)


# ---------------------------------------------------------------------------
# Suspicious-empty guard
# ---------------------------------------------------------------------------

def test_suspicious_empty_guard_closes_nothing_then_closes_on_second_empty_read(storage):
    storage.register_company({
        "company_name": "Acme",
        "ats_platform": PROVIDER,
        "board_slug": BOARD,
        "board_url": "https://acme.com",
        "api_endpoint": "https://api.acme.com/jobs",
    })
    for i in range(6):
        storage.upsert_posting(PROVIDER, BOARD, _posting(f"job_{i}"), T0)
    assert len(storage.get_active_job_ids(PROVIDER, BOARD)) == 6

    now1 = T0 + timedelta(hours=1)
    res1 = apply_read_result(_rr([], complete=True, error=None), PROVIDER, BOARD, storage, now=now1)
    assert res1["removed_count"] == 0
    assert res1["suspicious_empty"] is True
    assert len(storage.get_active_job_ids(PROVIDER, BOARD)) == 6
    board_row = storage.get_board(PROVIDER, BOARD)
    assert board_row["last_error"] == "suspicious_empty"

    # Second consecutive empty complete read, now past the 48h grace window.
    now2 = T0 + timedelta(hours=50)
    res2 = apply_read_result(_rr([], complete=True, error=None), PROVIDER, BOARD, storage, now=now2)
    assert res2["removed_count"] == 6
    assert storage.get_active_job_ids(PROVIDER, BOARD) == set()


# ---------------------------------------------------------------------------
# Failure tracking: 3 in a row -> paused
# ---------------------------------------------------------------------------

def test_three_failed_reads_in_a_row_pauses_the_board(storage):
    storage.register_company({
        "company_name": "Acme",
        "ats_platform": PROVIDER,
        "board_slug": BOARD,
        "board_url": "https://acme.com",
        "api_endpoint": "https://api.acme.com/jobs",
    })
    for i in range(3):
        now = T0 + timedelta(hours=i)
        apply_read_result(_rr([], complete=False, error="http_500"), PROVIDER, BOARD, storage, now=now)

    board_row = storage.get_board(PROVIDER, BOARD)
    assert board_row["status"] == "paused"
    assert board_row["failures_in_row"] == 3


# ---------------------------------------------------------------------------
# run_poll_cycle integration: a failing reader closes nothing
# ---------------------------------------------------------------------------

def test_poll_cycle_with_a_failing_reader_removes_no_job(storage):
    from services.poller.poller_service import run_poll_cycle

    storage.register_company({
        "company_name": "Stripe",
        "ats_platform": PROVIDER,
        "board_slug": "stripe",
        "board_url": "https://stripe.com",
        "api_endpoint": "https://boards-api.greenhouse.io/v1/boards/stripe/jobs",
    })
    storage.upsert_posting(PROVIDER, "stripe", _posting("st-1"), T0)

    failing_rr = ReaderResult(postings=[], complete=False, error="timeout")
    with patch("services.poller.poller_service.poll_single_company", return_value=failing_rr):
        results = run_poll_cycle(storage)

    assert len(results) == 1
    assert results[0]["removed_count"] == 0
    assert storage.get_active_job_ids(PROVIDER, "stripe") == {"st-1"}
