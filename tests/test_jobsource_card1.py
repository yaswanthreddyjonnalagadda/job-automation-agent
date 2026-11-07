"""Tests for Job Source Card 1: ReaderResult Propagation and Safe Diff Application.

Requirements under test:
1. `poller_service.poll_single_company` returns the whole `ReaderResult` without dropping `complete`.
2. `apply_read_result` applies postings to storage and closes missing jobs ONLY on complete, successful
   reads, and only once they have been missing for more than the 48h grace period (see
   `tests/test_job_source_lifecycle.py` for the full safe-close contract: upsert labels,
   suspicious-empty guard, and failure/pause tracking).
3. A failed or incomplete read (`complete=False` or `error is not None`) closes nothing.
4. `run_poll_cycle` passes `ReaderResult` directly to `apply_read_result`.

Note: `apply_read_result`'s signature is `(result, provider, board, storage, run_id=None, now=None)`
-- fixed positional order, no flexible argument resolution. The flexible-order calling convention
this file originally tested was retired by Card 1 in favor of this single, explicit contract.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch
import pytest

from services.diff_engine.diff_engine import apply_read_result
from services.diff_engine.storage import JobStorage
from services.poller.poller_service import poll_single_company, run_poll_cycle
from services.poller.reader_result import ReaderResult


@pytest.fixture
def temp_storage(tmp_path):
    db_file = tmp_path / "card1_test.db"
    return JobStorage(dsn=f"sqlite:///{db_file}")


# ---------------------------------------------------------------------------
# 1. poll_single_company returns whole ReaderResult
# ---------------------------------------------------------------------------

def test_poll_single_company_greenhouse_returns_whole_reader_result():
    mock_res = ReaderResult(
        postings=[{"external_id": "gh-1", "title": "Network Engineer"}],
        complete=True,
        error=None,
    )
    company = {"ats_platform": "greenhouse", "board_slug": "stripe"}
    with patch("services.poller.poller_service.read_greenhouse_board", return_value=mock_res) as mock_read:
        result = poll_single_company(company)
        assert isinstance(result, ReaderResult)
        assert result.complete is True
        assert result.error is None
        assert len(result.postings) == 1
        assert result.postings[0]["external_id"] == "gh-1"
        mock_read.assert_called_once_with("stripe", session=None)


def test_poll_single_company_lever_returns_whole_reader_result():
    mock_res = ReaderResult(
        postings=[{"external_id": "lev-1", "title": "Infrastructure Engineer"}],
        complete=False,
        error="http_500",
    )
    company = {"ats_platform": "lever", "board_slug": "palantir"}
    with patch("services.poller.poller_service.read_lever_board", return_value=mock_res):
        result = poll_single_company(company)
        assert isinstance(result, ReaderResult)
        assert result.complete is False
        assert result.error == "http_500"
        assert len(result.postings) == 1


def test_poll_single_company_ashby_returns_whole_reader_result():
    mock_res = ReaderResult(postings=[], complete=False, error="timeout")
    company = {"ats_platform": "ashby", "board_slug": "linear"}
    with patch("services.poller.poller_service.read_ashby_board", return_value=mock_res):
        result = poll_single_company(company)
        assert isinstance(result, ReaderResult)
        assert result.complete is False
        assert result.error == "timeout"


def test_poll_single_company_workday_returns_whole_reader_result():
    mock_res = ReaderResult(
        postings=[{"external_id": "wd-1", "title": "Cloud Architect"}],
        complete=True,
        error=None,
    )
    company = {"ats_platform": "workday", "board_url": "https://company.wd1.myworkdayjobs.com/careers"}
    with patch("services.poller.poller_service.read_workday_board", return_value=mock_res):
        result = poll_single_company(company)
        assert isinstance(result, ReaderResult)
        assert result.complete is True
        assert len(result.postings) == 1


def test_poll_single_company_smartrecruiters_returns_whole_reader_result():
    mock_res = ReaderResult(postings=[], complete=True, error=None)
    company = {"ats_platform": "smartrecruiters", "board_slug": "visa"}
    with patch("services.poller.poller_service.read_smartrecruiters_board", return_value=mock_res):
        result = poll_single_company(company)
        assert isinstance(result, ReaderResult)
        assert result.complete is True
        assert result.error is None


def test_poll_single_company_bamboohr_returns_whole_reader_result():
    mock_res = ReaderResult(
        postings=[{"external_id": "b-1", "title": "Systems Engineer"}],
        complete=True,
        error=None,
    )
    company = {"ats_platform": "bamboohr", "board_slug": "secunetics", "company_name": "Secunetics"}
    with patch("services.poller.poller_service.read_bamboohr_board", return_value=mock_res):
        result = poll_single_company(company)
        assert isinstance(result, ReaderResult)
        assert result.complete is True
        assert result.postings[0]["title"] == "Systems Engineer"


def test_poll_single_company_generic_scraper_returns_reader_result():
    company = {"ats_platform": "generic", "board_url": "https://example.com/careers"}
    with patch("services.poller.generic_scraper.scrape_career_page", return_value=[{"external_id": "gen-1", "title": "Engineer"}]):
        result = poll_single_company(company)
        assert isinstance(result, ReaderResult)
        assert result.complete is True
        assert len(result.postings) == 1


def test_poll_single_company_unsupported_platform_returns_incomplete_error():
    company = {"ats_platform": "unknown_ats", "board_slug": "someco"}
    result = poll_single_company(company)
    assert isinstance(result, ReaderResult)
    assert result.complete is False
    assert "Unsupported ATS platform" in (result.error or "")


# ---------------------------------------------------------------------------
# 2. apply_read_result: a failed read closes nothing
# ---------------------------------------------------------------------------

def test_apply_read_result_successful_complete_read_closes_removed_jobs(temp_storage):
    """When a read is complete, error-free, and the missing job has been gone past the
    48h grace period, it is marked removed."""
    from datetime import datetime, timezone
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # Seed 2 active jobs
    r1 = ReaderResult(
        postings=[
            {"external_id": "job_1", "title": "Network Engineer 1"},
            {"external_id": "job_2", "title": "Network Engineer 2"},
        ],
        complete=True,
        error=None,
    )
    res1 = apply_read_result(r1, "greenhouse", "acme", temp_storage, now=t0)
    assert res1["new_count"] == 2
    assert res1["removed_count"] == 0
    assert temp_storage.get_active_job_ids("greenhouse", "acme") == {"job_1", "job_2"}

    # Second poll, 49h later: only job_1 is present; complete=True -> job_2 is removed
    r2 = ReaderResult(
        postings=[
            {"external_id": "job_1", "title": "Network Engineer 1"},
        ],
        complete=True,
        error=None,
    )
    res2 = apply_read_result(r2, "greenhouse", "acme", temp_storage, now=t0 + timedelta(hours=49))
    assert res2["new_count"] == 0
    assert res2["removed_count"] == 1
    assert temp_storage.get_active_job_ids("greenhouse", "acme") == {"job_1"}


def test_apply_read_result_failed_read_closes_nothing(temp_storage):
    """When a read has error or complete=False, missing jobs are NEVER marked removed."""
    company_platform, board = "greenhouse", "acme"

    # Seed 2 active jobs
    r1 = ReaderResult(
        postings=[
            {"external_id": "job_1", "title": "Network Engineer 1"},
            {"external_id": "job_2", "title": "Network Engineer 2"},
        ],
        complete=True,
        error=None,
    )
    apply_read_result(r1, company_platform, board, temp_storage)
    assert temp_storage.get_active_job_ids(company_platform, board) == {"job_1", "job_2"}

    # Subsequent poll: network failure / 500 error returns empty postings and complete=False
    r_fail = ReaderResult(postings=[], complete=False, error="http_500")
    res_fail = apply_read_result(r_fail, company_platform, board, temp_storage)

    # A failed read closes nothing!
    assert res_fail["removed_count"] == 0
    assert temp_storage.get_active_job_ids(company_platform, board) == {"job_1", "job_2"}


def test_apply_read_result_incomplete_read_still_inserts_new_jobs_without_closing(temp_storage):
    """An incomplete pagination read can add new jobs, but must not close existing active ones."""
    platform, board = "workday", "copart"

    # Seed initial job
    r1 = ReaderResult(
        postings=[{"external_id": "job_1", "title": "Engineer 1"}],
        complete=True,
        error=None,
    )
    apply_read_result(r1, platform, board, temp_storage)

    # Incomplete read: returns job_3 (new), drops job_1 due to page cutoff, complete=False
    r_partial = ReaderResult(
        postings=[{"external_id": "job_3", "title": "Engineer 3"}],
        complete=False,
        error=None,
    )
    res = apply_read_result(r_partial, platform, board, temp_storage)

    assert res["new_count"] == 1
    assert res["removed_count"] == 0  # Closes nothing!
    # Both old job_1 and new job_3 are active!
    active_ids = temp_storage.get_active_job_ids(platform, board)
    assert active_ids == {"job_1", "job_3"}


def test_apply_read_result_default_run_id_and_now_are_optional(temp_storage):
    """apply_read_result works with just (result, provider, board, storage); run_id/now default."""
    rr = ReaderResult(postings=[{"external_id": "lev-99", "title": "DevOps"}], complete=True, error=None)

    res = apply_read_result(rr, "lever", "testco", temp_storage)
    assert res["new_count"] == 1
    assert temp_storage.get_active_job_ids("lever", "testco") == {"lev-99"}


# ---------------------------------------------------------------------------
# 3. run_poll_cycle integration
# ---------------------------------------------------------------------------

def test_run_poll_cycle_passes_reader_result_to_apply_read_result(temp_storage):
    """run_poll_cycle polls companies and applies their ReaderResult without dropping complete."""
    temp_storage.register_company({
        "company_name": "Stripe",
        "ats_platform": "greenhouse",
        "board_slug": "stripe",
        "board_url": "https://stripe.com",
        "api_endpoint": "https://boards-api.greenhouse.io/v1/boards/stripe/jobs",
    })

    # Initial poll
    mock_rr = ReaderResult(
        postings=[{"external_id": "st-1", "title": "Network Engineer"}],
        complete=True,
        error=None,
    )
    with patch("services.poller.poller_service.poll_single_company", return_value=mock_rr):
        results = run_poll_cycle(temp_storage)
        assert len(results) == 1
        assert results[0]["new_count"] == 1
        assert temp_storage.get_active_job_ids("greenhouse", "stripe") == {"st-1"}

    # Next poll fails: complete=False
    fail_rr = ReaderResult(postings=[], complete=False, error="timeout")
    with patch("services.poller.poller_service.poll_single_company", return_value=fail_rr):
        results_fail = run_poll_cycle(temp_storage)
        assert len(results_fail) == 1
        assert results_fail[0]["removed_count"] == 0
        # Active job st-1 is preserved!
        assert temp_storage.get_active_job_ids("greenhouse", "stripe") == {"st-1"}
