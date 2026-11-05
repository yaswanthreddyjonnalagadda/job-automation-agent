"""
Tests for shared ReaderResult contract across ATS public board readers.
Ensures offline parsing of real sanitized ATS responses without network requests.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services.poller.reader_result import ReaderResult
from services.poller.greenhouse import parse_greenhouse_response, read_greenhouse_board, poll_greenhouse
from services.poller.lever import parse_lever_response, read_lever_board, poll_lever
from services.poller.ashby import parse_ashby_response, read_ashby_board, poll_ashby
from services.poller.smartrecruiters import (
    parse_smartrecruiters_response,
    read_smartrecruiters_board,
    poll_smartrecruiters,
)
from services.poller.bamboohr import parse_bamboohr_response, fetch_bamboohr_jobs, poll_bamboohr
from services.poller.workday import parse_workday_response, read_workday_board, poll_workday

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def block_live_network(monkeypatch):
    """Safety guard: prevent any live network requests during reader testing."""
    def _blocked_request(*args, **kwargs):
        raise RuntimeError("Live network request blocked in unit tests!")

    monkeypatch.setattr("requests.Session.get", _blocked_request)
    monkeypatch.setattr("requests.Session.post", _blocked_request)


# ---------------------------------------------------------------------------
# 1. Greenhouse Reader Tests
# ---------------------------------------------------------------------------

def test_greenhouse_reader_result_success():
    fixture_path = FIXTURES_DIR / "greenhouse_response.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    result = parse_greenhouse_response(data, slug="cloudflare")
    assert isinstance(result, ReaderResult)
    assert result.complete is True
    assert result.error is None
    assert len(result.postings) > 0

    job = result.postings[0]
    assert "title" in job and job["title"]
    assert "external_id" in job and job["external_id"]
    assert "apply_url" in job and job["apply_url"]
    assert "location" in job


def test_greenhouse_reader_result_malformed():
    result = parse_greenhouse_response({"invalid_key": []})
    assert isinstance(result, ReaderResult)
    assert result.complete is False
    assert result.error is not None
    assert result.postings == []


def test_greenhouse_read_board_error_handling():
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_session.get.return_value = mock_resp

    result = read_greenhouse_board("nonexistent-slug", session=mock_session)
    assert isinstance(result, ReaderResult)
    assert result.complete is False
    assert "404 Not Found" in (result.error or "")


# ---------------------------------------------------------------------------
# 2. Lever Reader Tests
# ---------------------------------------------------------------------------

def test_lever_reader_result_success():
    fixture_path = FIXTURES_DIR / "lever_response.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    result = parse_lever_response(data, company="palantir")
    assert isinstance(result, ReaderResult)
    assert result.complete is True
    assert result.error is None
    assert len(result.postings) > 0

    job = result.postings[0]
    assert "title" in job and job["title"]
    assert "external_id" in job and job["external_id"]
    assert "apply_url" in job and "lever.co" in job["apply_url"]
    assert "location" in job


def test_lever_reader_result_malformed():
    result = parse_lever_response({"not": "a list"})
    assert isinstance(result, ReaderResult)
    assert result.complete is False
    assert result.error is not None
    assert result.postings == []


def test_lever_read_board_error_handling():
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_session.get.return_value = mock_resp

    result = read_lever_board("invalid-co", session=mock_session)
    assert isinstance(result, ReaderResult)
    assert result.complete is False
    assert "404 Not Found" in (result.error or "")


# ---------------------------------------------------------------------------
# 3. Ashby Reader Tests
# ---------------------------------------------------------------------------

def test_ashby_reader_result_success():
    fixture_path = FIXTURES_DIR / "ashby_response.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    result = parse_ashby_response(data, org="linear")
    assert isinstance(result, ReaderResult)
    assert result.complete is True
    assert result.error is None
    assert len(result.postings) > 0

    job = result.postings[0]
    assert "title" in job and job["title"]
    assert "external_id" in job and job["external_id"]
    assert "apply_url" in job and "ashbyhq.com" in job["apply_url"]


def test_ashby_reader_result_malformed():
    result = parse_ashby_response({"no_jobs": 123})
    assert isinstance(result, ReaderResult)
    assert result.complete is False
    assert result.error is not None
    assert result.postings == []


def test_ashby_read_board_error_handling():
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_session.get.return_value = mock_resp

    result = read_ashby_board("nonexistent-org", session=mock_session)
    assert isinstance(result, ReaderResult)
    assert result.complete is False
    assert "404 Not Found" in (result.error or "")


# ---------------------------------------------------------------------------
# 4. SmartRecruiters Reader Tests
# ---------------------------------------------------------------------------

def test_smartrecruiters_reader_result_success():
    fixture_path = FIXTURES_DIR / "smartrecruiters_response.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    result = parse_smartrecruiters_response(data, slug="smartrecruiters")
    assert isinstance(result, ReaderResult)
    assert result.complete is True
    assert result.error is None
    assert len(result.postings) > 0

    job = result.postings[0]
    assert "title" in job and job["title"]
    assert "external_id" in job and job["external_id"]
    assert "apply_url" in job and "smartrecruiters.com" in job["apply_url"]


def test_smartrecruiters_reader_result_incomplete():
    fixture_path = FIXTURES_DIR / "smartrecruiters_response.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    # Simulate partial page where totalFound exceeds returned content
    data["totalFound"] = len(data["content"]) + 50

    result = parse_smartrecruiters_response(data, slug="smartrecruiters")
    assert isinstance(result, ReaderResult)
    assert result.complete is False
    assert len(result.postings) > 0


def test_smartrecruiters_reader_result_malformed():
    result = parse_smartrecruiters_response({"no_content": True})
    assert isinstance(result, ReaderResult)
    assert result.complete is False
    assert result.error is not None
    assert result.postings == []


# ---------------------------------------------------------------------------
# 5. BambooHR Reader Tests
# ---------------------------------------------------------------------------

def test_bamboohr_reader_result_success():
    fixture_path = FIXTURES_DIR / "bamboohr_response.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    result = parse_bamboohr_response(data, company_slug="secunetics", company_name="Secunetics")
    assert isinstance(result, ReaderResult)
    assert result.complete is True
    assert result.error is None
    assert len(result.postings) > 0

    job = result.postings[0]
    assert "title" in job and job["title"]
    assert "external_id" in job and job["external_id"]
    assert "apply_url" in job and "bamboohr.com" in job["apply_url"]
    assert job["company_slug"] == "secunetics"


def test_bamboohr_reader_result_malformed():
    result = parse_bamboohr_response("not a valid json object or list")
    assert isinstance(result, ReaderResult)
    assert result.complete is False
    assert result.error is not None
    assert result.postings == []


# ---------------------------------------------------------------------------
# 6. Workday Reader Tests
# ---------------------------------------------------------------------------

def test_workday_reader_result_success():
    fixture_path = FIXTURES_DIR / "workday_response.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    result = parse_workday_response(
        data,
        host="copart.wd12.myworkdayjobs.com",
        site="Copart",
        board_url="https://copart.wd12.myworkdayjobs.com/Copart",
    )
    assert isinstance(result, ReaderResult)
    assert result.complete is True
    assert result.error is None
    assert len(result.postings) > 0

    job = result.postings[0]
    assert "title" in job and job["title"]
    assert "external_id" in job and job["external_id"]
    assert "apply_url" in job and "copart.wd12.myworkdayjobs.com" in job["apply_url"]


def test_workday_reader_result_incomplete():
    fixture_path = FIXTURES_DIR / "workday_response.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    # Simulate partial page where total postings exceed this batch
    data["total"] = len(data["jobPostings"]) + 100

    result = parse_workday_response(data)
    assert isinstance(result, ReaderResult)
    assert result.complete is False
    assert len(result.postings) > 0


def test_workday_reader_result_malformed():
    result = parse_workday_response({"no_postings": 42})
    assert isinstance(result, ReaderResult)
    assert result.complete is False
    assert result.error is not None
    assert result.postings == []


def test_backward_compatibility_poll_functions():
    fixture_gh = json.loads((FIXTURES_DIR / "greenhouse_response.json").read_text(encoding="utf-8"))
    res_gh = parse_greenhouse_response(fixture_gh)
    assert isinstance(res_gh.postings, list)

    fixture_lever = json.loads((FIXTURES_DIR / "lever_response.json").read_text(encoding="utf-8"))
    res_lever = parse_lever_response(fixture_lever)
    assert isinstance(res_lever.postings, list)

    fixture_ashby = json.loads((FIXTURES_DIR / "ashby_response.json").read_text(encoding="utf-8"))
    res_ashby = parse_ashby_response(fixture_ashby)
    assert isinstance(res_ashby.postings, list)

    fixture_sr = json.loads((FIXTURES_DIR / "smartrecruiters_response.json").read_text(encoding="utf-8"))
    res_sr = parse_smartrecruiters_response(fixture_sr)
    assert isinstance(res_sr.postings, list)

    fixture_bamboo = json.loads((FIXTURES_DIR / "bamboohr_response.json").read_text(encoding="utf-8"))
    res_bamboo = parse_bamboohr_response(fixture_bamboo)
    assert isinstance(res_bamboo.postings, list)

    fixture_wd = json.loads((FIXTURES_DIR / "workday_response.json").read_text(encoding="utf-8"))
    res_wd = parse_workday_response(fixture_wd)
    assert isinstance(res_wd.postings, list)
