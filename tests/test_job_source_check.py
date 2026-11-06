"""Tests for Card 2: SmartRecruiters multi-page pagination and the report-only check.py tool.

JOB 1 -- SmartRecruiters pagination:
  read_smartrecruiters_board keeps requesting offset += limit until offset >= totalFound.
  On a mid-pagination failure it returns the postings accumulated so far (complete=False).

JOB 2 -- services/poller/check.py:
  Calls read_<provider>_board directly, prints one line per board, writes NOTHING.
  Supports greenhouse, lever, ashby, smartrecruiters, bamboohr, workday (if present), icims (if present).
  A failing reader prints its error and continues (does not crash the script).
"""
from __future__ import annotations

import importlib
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import MagicMock, call, patch

import pytest
import requests

from services.poller.reader_result import ReaderResult
from services.poller.smartrecruiters import read_smartrecruiters_board

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "job_source" / "smartrecruiters"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_resp(data: Any, status: int = 200) -> MagicMock:
    """Build a mock requests.Response that returns *data* as JSON."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.headers = {}
    resp.json.return_value = data
    return resp


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# JOB 1 — SmartRecruiters multi-page pagination
# ---------------------------------------------------------------------------

class TestSmartRecruitersMultiPage:
    """read_smartrecruiters_board reads every page until offset >= totalFound."""

    def test_both_pages_read_yields_all_postings_complete_true(self, monkeypatch):
        """When page1 and page2 both succeed: 4 postings, complete=True."""
        monkeypatch.setattr("services.poller.http._sleep", lambda s: None)

        page1 = _load("page1.json")   # totalFound=4, 2 items, offset=0
        page2 = _load("page2.json")   # totalFound=4, 2 items, offset=2

        session = MagicMock(spec=requests.Session)
        session.headers = {}
        session.get.side_effect = [_make_resp(page1), _make_resp(page2)]

        result = read_smartrecruiters_board("testco", session=session)

        assert isinstance(result, ReaderResult)
        assert result.complete is True
        assert result.error is None
        assert len(result.postings) == 4

        ids = {p["external_id"] for p in result.postings}
        assert ids == {
            "744000001000001",
            "744000001000002",
            "744000001000003",
            "744000001000004",
        }

        # Verify two HTTP calls were made (one per page)
        assert session.get.call_count == 2

    def test_page2_http_error_returns_page1_postings_complete_false(self, monkeypatch):
        """When page2 returns 500: return page1 postings, complete=False, error='http_500'."""
        monkeypatch.setattr("services.poller.http._sleep", lambda s: None)

        page1 = _load("page1.json")   # totalFound=4

        session = MagicMock(spec=requests.Session)
        session.headers = {}

        resp1 = _make_resp(page1)
        # Page 2 returns HTTP 500 six times (exhausting retries)
        resp_500 = MagicMock(spec=requests.Response)
        resp_500.status_code = 500
        resp_500.headers = {}
        session.get.side_effect = [resp1] + [resp_500] * 6

        result = read_smartrecruiters_board("testco", session=session)

        assert isinstance(result, ReaderResult)
        assert result.complete is False
        assert result.error == "http_500"
        # Page 1 postings are preserved
        assert len(result.postings) == 2
        ids = {p["external_id"] for p in result.postings}
        assert ids == {"744000001000001", "744000001000002"}

    def test_page2_timeout_returns_page1_postings_complete_false(self, monkeypatch):
        """When page2 times out: return page1 postings, complete=False, error='timeout'."""
        monkeypatch.setattr("services.poller.http._sleep", lambda s: None)

        page1 = _load("page1.json")   # totalFound=4

        session = MagicMock(spec=requests.Session)
        session.headers = {}

        resp1 = _make_resp(page1)
        session.get.side_effect = (
            [resp1]
            + [requests.exceptions.Timeout("timed out")] * 6
        )

        result = read_smartrecruiters_board("testco", session=session)

        assert isinstance(result, ReaderResult)
        assert result.complete is False
        assert result.error == "timeout"
        assert len(result.postings) == 2

    def test_single_page_when_totalfound_equals_limit(self, monkeypatch):
        """When totalFound == len(content) on first page: only one GET, complete=True."""
        monkeypatch.setattr("services.poller.http._sleep", lambda s: None)

        single_page = {
            "content": [
                {"id": "job_a", "name": "Engineer A", "location": {}, "department": {},
                 "typeOfEmployment": {}, "releasedDate": None},
            ],
            "totalFound": 1,
        }

        session = MagicMock(spec=requests.Session)
        session.headers = {}
        session.get.return_value = _make_resp(single_page)

        result = read_smartrecruiters_board("testco", session=session)

        assert result.complete is True
        assert result.error is None
        assert len(result.postings) == 1
        assert session.get.call_count == 1

    def test_first_page_fails_returns_empty_complete_false(self, monkeypatch):
        """When even page 1 fails: postings=[], complete=False."""
        monkeypatch.setattr("services.poller.http._sleep", lambda s: None)

        session = MagicMock(spec=requests.Session)
        session.headers = {}
        resp_500 = MagicMock(spec=requests.Response)
        resp_500.status_code = 500
        resp_500.headers = {}
        session.get.return_value = resp_500

        result = read_smartrecruiters_board("testco", session=session)

        assert result.complete is False
        assert result.postings == []
        assert result.error is not None

    def test_offset_params_sent_on_subsequent_pages(self, monkeypatch):
        """Each GET carries the correct offset and limit query parameters."""
        monkeypatch.setattr("services.poller.http._sleep", lambda s: None)

        page1 = _load("page1.json")   # limit=2, totalFound=4
        page2 = _load("page2.json")

        session = MagicMock(spec=requests.Session)
        session.headers = {}
        session.get.side_effect = [_make_resp(page1), _make_resp(page2)]

        read_smartrecruiters_board("testco", session=session)

        calls = session.get.call_args_list
        assert len(calls) == 2

        # First call: offset=0 (default or explicit)
        first_params = calls[0][1].get("params") or calls[0][0][1] if len(calls[0][0]) > 1 else calls[0][1].get("params", {})
        # Second call must have offset >= 2 (the limit from page 1)
        second_params = calls[1][1].get("params", {})
        assert second_params.get("offset", 0) >= 2


# ---------------------------------------------------------------------------
# JOB 2 — services/poller/check.py
# ---------------------------------------------------------------------------

def _import_check():
    """Import check module, reloading to avoid caching between tests."""
    if "services.poller.check" in sys.modules:
        del sys.modules["services.poller.check"]
    return importlib.import_module("services.poller.check")


class TestCheckTool:
    """check.py prints one line per board; writes nothing; a failing reader doesn't crash it."""

    def test_single_board_prints_one_line(self, capsys, monkeypatch):
        """--provider greenhouse --board stripe prints exactly one CSV-style line."""
        check = _import_check()

        mock_result = ReaderResult(postings=[{"external_id": "j1"}] * 3, complete=True, error=None)
        # Patch the module-level name; check._run_one resolves it via getattr at call time.
        monkeypatch.setattr(check, "read_greenhouse_board", lambda board_id, session=None: mock_result)

        check.run(["--provider", "greenhouse", "--board", "stripe"])

        out = capsys.readouterr().out
        lines = [l for l in out.strip().splitlines() if l.strip()]
        assert len(lines) == 1
        line = lines[0]
        assert "greenhouse" in line
        assert "stripe" in line
        assert "\t3\t" in line   # posting count between tabs

    def test_failing_reader_prints_error_and_continues(self, capsys, monkeypatch):
        """When reader returns error, the line shows the error and the script doesn't crash."""
        check = _import_check()

        mock_fail = ReaderResult(postings=[], complete=False, error="http_500")
        monkeypatch.setattr(check, "read_greenhouse_board", lambda board_id, session=None: mock_fail)

        check.run(["--provider", "greenhouse", "--board", "failco"])

        out = capsys.readouterr().out
        lines = [l for l in out.strip().splitlines() if l.strip()]
        assert len(lines) == 1
        assert "http_500" in lines[0]

    def test_limit_mode_reads_boards_from_db_and_prints_one_line_each(self, capsys, monkeypatch, tmp_path):
        """--provider greenhouse --limit 2 queries 2 boards from company_directory, prints 2 lines."""
        check = _import_check()

        # Mock the storage to return 2 greenhouse companies
        mock_companies = [
            {"board_slug": "stripe", "ats_platform": "greenhouse"},
            {"board_slug": "plaid", "ats_platform": "greenhouse"},
        ]
        mock_storage = MagicMock()
        mock_storage.get_companies_to_poll.return_value = mock_companies

        mock_result = ReaderResult(postings=[{"external_id": "j1"}], complete=True, error=None)
        read_calls = []

        def _fake_read(board_id, session=None):
            read_calls.append(board_id)
            return mock_result

        monkeypatch.setattr(check, "read_greenhouse_board", _fake_read)
        monkeypatch.setattr(check, "_open_storage_readonly", lambda: mock_storage)

        check.run(["--provider", "greenhouse", "--limit", "2"])

        out = capsys.readouterr().out
        lines = [l for l in out.strip().splitlines() if l.strip()]
        assert len(lines) == 2
        assert read_calls == ["stripe", "plaid"]

    def test_limit_mode_one_failure_does_not_stop_remaining(self, capsys, monkeypatch, tmp_path):
        """In --limit mode, a reader that raises an exception prints the error and continues."""
        check = _import_check()

        mock_companies = [
            {"board_slug": "good-co", "ats_platform": "lever"},
            {"board_slug": "bad-co", "ats_platform": "lever"},
            {"board_slug": "good-co-2", "ats_platform": "lever"},
        ]
        mock_storage = MagicMock()
        mock_storage.get_companies_to_poll.return_value = mock_companies

        call_log = []

        def _fake_read(slug, session=None):
            call_log.append(slug)
            if slug == "bad-co":
                raise RuntimeError("unexpected crash")
            return ReaderResult(postings=[{"external_id": "j1"}], complete=True, error=None)

        monkeypatch.setattr(check, "read_lever_board", _fake_read)
        monkeypatch.setattr(check, "_open_storage_readonly", lambda: mock_storage)

        check.run(["--provider", "lever", "--limit", "3"])

        out = capsys.readouterr().out
        lines = [l for l in out.strip().splitlines() if l.strip()]
        # All three boards produce output lines
        assert len(lines) == 3
        # All three slugs were attempted
        assert call_log == ["good-co", "bad-co", "good-co-2"]
        # The error line mentions the exception
        assert any("bad-co" in l for l in lines)

    def test_check_writes_nothing_to_storage(self, monkeypatch, tmp_path):
        """check.py must not write any rows to the database."""
        check = _import_check()

        mock_storage = MagicMock()
        mock_storage.get_companies_to_poll.return_value = [
            {"board_slug": "acme", "ats_platform": "greenhouse"},
        ]
        mock_storage.upsert_job = MagicMock()
        mock_storage.mark_removed = MagicMock()
        mock_storage.register_company = MagicMock()

        monkeypatch.setattr(check, "read_greenhouse_board",
                            lambda slug, session=None: ReaderResult(postings=[{"external_id": "j1"}], complete=True, error=None))
        monkeypatch.setattr(check, "_open_storage_readonly", lambda: mock_storage)

        check.run(["--provider", "greenhouse", "--limit", "1"])

        mock_storage.upsert_job.assert_not_called()
        mock_storage.mark_removed.assert_not_called()
        mock_storage.register_company.assert_not_called()

    def test_unsupported_provider_exits_with_error(self, capsys, monkeypatch):
        """An unknown provider name should raise SystemExit or print an error."""
        check = _import_check()

        with pytest.raises((SystemExit, ValueError)):
            check.run(["--provider", "unknown_ats_xyz", "--board", "testco"])

    def test_line_contains_timing(self, capsys, monkeypatch):
        """Each output line includes the number of seconds taken (a float >= 0)."""
        import re
        check = _import_check()

        mock_result = ReaderResult(postings=[], complete=True, error=None)
        monkeypatch.setattr(check, "read_greenhouse_board", lambda slug, session=None: mock_result)

        check.run(["--provider", "greenhouse", "--board", "acme"])

        out = capsys.readouterr().out
        line = out.strip().splitlines()[0]
        # There should be a decimal number in the output (seconds)
        assert re.search(r"\d+\.\d+", line), f"No timing found in: {line!r}"
