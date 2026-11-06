"""Tests for Card 5: Board Discovery & Approval System.

Requirements under test:
- extract_board on 20 sample URLs (Workday host shapes, locale stripping, multi-sites, look-alikes, other ATS slugs).
- detect_ats_in_html on 5 saved careers page fixtures (Greenhouse embed script, Lever, Ashby, Workday, look-alikes).
- discover writes only "pending" rows to company_directory and never "live".
- approve CLI sets status="live" on --approve and status="paused" on --reject.
- Offline tests using SQLite temp database and local fixtures.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from services.diff_engine.storage import JobStorage
from services.directory_builder.common_crawl import extract_board
from services.poller.ats_resolver import detect_ats_in_html
from services.poller.reader_result import ReaderResult

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "job_source" / "discovery"


# ---------------------------------------------------------------------------
# 1. extract_board on 20 sample URLs
# ---------------------------------------------------------------------------

def test_extract_board_twenty_sample_urls():
    # 1. Workday standard host and site
    assert extract_board("workday", "https://copart.wd12.myworkdayjobs.com/Copart") == "copart.wd12.myworkdayjobs.com/Copart"

    # 2. Workday with en-US locale segment
    assert extract_board("workday", "https://target.wd5.myworkdayjobs.com/en-US/target_careers") == "target.wd5.myworkdayjobs.com/target_careers"

    # 3. Workday second site on same tenant with fr-FR locale
    assert extract_board("workday", "https://target.wd5.myworkdayjobs.com/fr-FR/corporate_careers") == "target.wd5.myworkdayjobs.com/corporate_careers"

    # 4. Workday http scheme without locale
    assert extract_board("workday", "http://company.wd1.myworkdayjobs.com/Careers") == "company.wd1.myworkdayjobs.com/Careers"

    # 5. Workday no scheme with es-ES locale
    assert extract_board("workday", "company.wd2.myworkdayjobs.com/es-ES/Site") == "company.wd2.myworkdayjobs.com/Site"

    # 6. Look-alike attacker host ending in .attacker.com
    assert extract_board("workday", "https://fake.myworkdayjobs.com.attacker.com/site") is None

    # 7. Look-alike prefix host evil-myworkdayjobs.com
    assert extract_board("workday", "https://evil-myworkdayjobs.com/site") is None

    # 8. Missing site path
    assert extract_board("workday", "https://acme.wd1.myworkdayjobs.com") is None

    # 9. Only locale in path, no site
    assert extract_board("workday", "https://acme.wd1.myworkdayjobs.com/en-US") is None

    # 10. Greenhouse standard slug
    assert extract_board("greenhouse", "https://boards.greenhouse.io/stripe") == "stripe"

    # 11. Greenhouse embed path skipped
    assert extract_board("greenhouse", "https://boards.greenhouse.io/embed/job_board?for=stripe") is None

    # 12. Greenhouse2 (job-boards.greenhouse.io)
    assert extract_board("greenhouse2", "https://job-boards.greenhouse.io/datadog/jobs/123") == "datadog"

    # 13. Lever standard slug
    assert extract_board("lever", "https://jobs.lever.co/palantir") == "palantir"

    # 14. Lever 'jobs' reserved slug skipped
    assert extract_board("lever", "https://jobs.lever.co/jobs/123") is None

    # 15. Ashby standard slug
    assert extract_board("ashby", "https://jobs.ashbyhq.com/linear") == "linear"

    # 16. Ashby 'api' reserved slug skipped
    assert extract_board("ashby", "https://jobs.ashbyhq.com/api/v1") is None

    # 17. SmartRecruiters standard slug
    assert extract_board("smartrecruiters", "https://jobs.smartrecruiters.com/visa") == "visa"

    # 18. Greenhouse look-alike domain
    assert extract_board("greenhouse", "https://fakeboards.greenhouse.io/test") is None

    # 19. Lever look-alike domain
    assert extract_board("lever", "https://notjobs.lever.co/test") is None

    # 20. Empty/malformed URL
    assert extract_board("workday", "") is None


# ---------------------------------------------------------------------------
# 2. detect_ats_in_html on 5 saved careers pages
# ---------------------------------------------------------------------------

def test_detect_ats_in_html_on_saved_fixtures():
    # 1. Greenhouse embed script (checks for= before plain URL)
    html_gh = (FIXTURES_DIR / "careers_greenhouse_embed.html").read_text(encoding="utf-8")
    assert detect_ats_in_html(html_gh) == [("greenhouse", "acmecorp")]

    # 2. Lever careers page
    html_lever = (FIXTURES_DIR / "careers_lever.html").read_text(encoding="utf-8")
    assert detect_ats_in_html(html_lever) == [("lever", "stripe")]

    # 3. Ashby careers page
    html_ashby = (FIXTURES_DIR / "careers_ashby.html").read_text(encoding="utf-8")
    assert detect_ats_in_html(html_ashby) == [("ashby", "linear")]

    # 4. Workday careers page
    html_wd = (FIXTURES_DIR / "careers_workday.html").read_text(encoding="utf-8")
    assert detect_ats_in_html(html_wd) == [("workday", "copart.wd12.myworkdayjobs.com/Copart")]

    # 5. Look-alikes careers page (detects nothing)
    html_lookalike = (FIXTURES_DIR / "careers_lookalike.html").read_text(encoding="utf-8")
    assert detect_ats_in_html(html_lookalike) == []


# ---------------------------------------------------------------------------
# 3. discover writes only "pending" rows and never "live"
# ---------------------------------------------------------------------------

def test_discover_writes_only_pending_and_never_live(tmp_path):
    from services.directory_builder.discover import run_discovery

    db_file = tmp_path / "test_disc.db"
    storage = JobStorage(dsn=f"sqlite:///{db_file}")

    mock_urls = [
        "https://copart.wd12.myworkdayjobs.com/Copart",
        "https://target.wd5.myworkdayjobs.com/en-US/target_careers",
    ]

    mock_rr = ReaderResult(
        postings=[
            {"external_id": "1", "title": "Senior Network Engineer"},
            {"external_id": "2", "title": "Accountant"},
        ],
        complete=True,
        error=None,
    )

    with patch("services.directory_builder.discover.query_cdx", return_value=mock_urls):
        with patch("services.directory_builder.discover.read_workday_board", return_value=mock_rr) as mock_read:
            summary = run_discovery(provider="workday", limit=10, storage=storage)
            assert summary["candidates"] == 2
            assert summary["real_boards"] == 2
            assert summary["matching_boards"] == 2

            # Check read called with known_ids lambda
            assert mock_read.call_count == 2
            _, kwargs = mock_read.call_args
            assert "known_ids" in kwargs

    # Verify rows in storage: all status must be "pending", none "live"
    pending_boards = storage.boards(status="pending")
    assert len(pending_boards) == 2
    for b in pending_boards:
        assert b["status"] == "pending"
        assert b["status"] != "live"

    live_boards = storage.boards(status="live")
    assert len(live_boards) == 0


# ---------------------------------------------------------------------------
# 4. approve CLI: --approve sets live; --reject sets paused
# ---------------------------------------------------------------------------

def test_approve_cli_operations(tmp_path):
    from services.directory_builder.approve import run_approve_action

    db_file = tmp_path / "test_appr.db"
    storage = JobStorage(dsn=f"sqlite:///{db_file}")

    # Register two companies as pending
    id1 = storage.register_company({
        "company_name": "Copart",
        "ats_platform": "workday",
        "board_slug": "copart.wd12.myworkdayjobs.com/Copart",
        "board_url": "https://copart.wd12.myworkdayjobs.com/Copart",
        "api_endpoint": "https://copart.wd12.myworkdayjobs.com/wday/cxs/copart/Copart/jobs",
    })
    storage.set_board_status(id1, "pending")

    id2 = storage.register_company({
        "company_name": "Target",
        "ats_platform": "workday",
        "board_slug": "target.wd5.myworkdayjobs.com/target_careers",
        "board_url": "https://target.wd5.myworkdayjobs.com/target_careers",
        "api_endpoint": "https://target.wd5.myworkdayjobs.com/wday/cxs/target/target_careers/jobs",
    })
    storage.set_board_status(id2, "pending")

    # 1. Approve id1 -> status becomes 'live'
    run_approve_action(storage=storage, approve_ids=[id1])
    board1 = storage.boards(status="live")
    assert len(board1) == 1
    assert board1[0]["id"] == id1
    assert board1[0]["status"] == "live"

    # 2. Reject id2 -> status becomes 'paused'
    run_approve_action(storage=storage, reject_ids=[id2])
    board2 = storage.boards(status="paused")
    assert len(board2) == 1
    assert board2[0]["id"] == id2
    assert board2[0]["status"] == "paused"
