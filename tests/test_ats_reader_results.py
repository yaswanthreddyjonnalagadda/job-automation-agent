"""
Tests for shared ReaderResult contract and HTTP client across ATS public board readers.
Ensures offline parsing of real sanitized ATS responses without network requests.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from services.poller.reader_result import ReaderResult
from services.poller.http import (
    make_session,
    get_json,
    post_json,
    BoardNotFound,
    HttpError,
    BadJson,
    HttpTimeout,
)
from services.poller.greenhouse import parse_greenhouse_response, read_greenhouse_board, poll_greenhouse
from services.poller.lever import parse_lever_response, read_lever_board, poll_lever
from services.poller.ashby import parse_ashby_response, read_ashby_board, poll_ashby
from services.poller.smartrecruiters import (
    parse_smartrecruiters_response,
    read_smartrecruiters_board,
    poll_smartrecruiters,
)
from services.poller.bamboohr import parse_bamboohr_response, read_bamboohr_board, fetch_bamboohr_jobs, poll_bamboohr
from services.poller.workday import parse_workday_response, read_workday_board, poll_workday

FIXTURES_BASE = Path(__file__).resolve().parent / "fixtures" / "job_source"


@pytest.fixture(autouse=True)
def block_live_network(monkeypatch):
    """Safety guard: prevent any live network requests during reader testing and bypass sleeps."""
    def _blocked_request(*args, **kwargs):
        raise RuntimeError("Live network request blocked in unit tests!")

    monkeypatch.setattr("requests.Session.get", _blocked_request)
    monkeypatch.setattr("requests.Session.post", _blocked_request)
    monkeypatch.setattr("services.poller.http._sleep", lambda s: None)


# ---------------------------------------------------------------------------
# HTTP Client Tests (services/poller/http.py)
# ---------------------------------------------------------------------------

def test_workday_and_greenhouse_sessions_do_not_share_headers():
    wd_session = make_session("workday")
    gh_session = make_session("greenhouse")

    assert wd_session is not gh_session
    assert "JobAutomationAgent" in wd_session.headers.get("User-Agent", "")
    assert "JobAutomationAgent" in gh_session.headers.get("User-Agent", "")

    # Workday has application/json headers; Greenhouse does not share them
    assert wd_session.headers.get("Content-Type") == "application/json"
    assert "Content-Type" not in gh_session.headers

    # Mutating one does not affect the other
    wd_session.headers["X-Custom-Test"] = "workday-only"
    assert "X-Custom-Test" not in gh_session.headers


def test_get_json_429_then_200_succeeds(monkeypatch):
    mock_session = MagicMock(spec=requests.Session)
    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.headers = {"Retry-After": "0"}

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = {"jobs": [{"id": 1}]}

    mock_session.get.side_effect = [resp_429, resp_200]
    monkeypatch.setattr("services.poller.http._sleep", lambda s: None)

    data = get_json(mock_session, "https://example.com/api")
    assert data == {"jobs": [{"id": 1}]}
    assert mock_session.get.call_count == 2


def test_get_json_six_failures_give_up(monkeypatch):
    mock_session = MagicMock(spec=requests.Session)
    resp_500 = MagicMock()
    resp_500.status_code = 500
    resp_500.headers = {}

    mock_session.get.return_value = resp_500
    monkeypatch.setattr("services.poller.http._sleep", lambda s: None)

    with pytest.raises(HttpError) as exc_info:
        get_json(mock_session, "https://example.com/api")

    assert exc_info.value.status == 500
    assert mock_session.get.call_count == 6


def test_get_json_404_not_retried():
    mock_session = MagicMock(spec=requests.Session)
    resp_404 = MagicMock()
    resp_404.status_code = 404

    mock_session.get.return_value = resp_404

    with pytest.raises(BoardNotFound):
        get_json(mock_session, "https://example.com/api")

    # 404 must not be retried: call count must be exactly 1
    assert mock_session.get.call_count == 1


def test_get_json_bad_json_raises_bad_json():
    mock_session = MagicMock(spec=requests.Session)
    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.side_effect = json.JSONDecodeError("Expecting value", "doc", 0)

    mock_session.get.return_value = resp_200

    with pytest.raises(BadJson):
        get_json(mock_session, "https://example.com/api")


# ---------------------------------------------------------------------------
# Fixture Empty Board Tests: complete=True with 0 postings
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider, parser_fn", [
    ("greenhouse", parse_greenhouse_response),
    ("lever", parse_lever_response),
    ("ashby", parse_ashby_response),
    ("smartrecruiters", parse_smartrecruiters_response),
    ("bamboohr", parse_bamboohr_response),
    ("workday", parse_workday_response),
])
def test_empty_board_complete_with_zero_postings(provider, parser_fn):
    fixture_path = FIXTURES_BASE / provider / "empty.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    result = parser_fn(data)
    assert isinstance(result, ReaderResult)
    assert result.complete is True
    assert result.error is None
    assert result.postings == []


# ---------------------------------------------------------------------------
# Fixture Truncated JSON Tests: complete=False, error="bad_json"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider, read_fn, arg", [
    ("greenhouse", read_greenhouse_board, "cloudflare"),
    ("lever", read_lever_board, "palantir"),
    ("ashby", read_ashby_board, "linear"),
    ("smartrecruiters", read_smartrecruiters_board, "smartrecruiters"),
    ("bamboohr", read_bamboohr_board, "secunetics"),
    ("workday", read_workday_board, "https://copart.wd12.myworkdayjobs.com/Copart"),
])
def test_truncated_json_returns_bad_json_error(provider, read_fn, arg, monkeypatch):
    fixture_path = FIXTURES_BASE / provider / "truncated.json"
    raw_content = fixture_path.read_text(encoding="utf-8")

    mock_session = MagicMock(spec=requests.Session)
    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.text = raw_content
    resp_200.json.side_effect = json.JSONDecodeError("Unterminated string", raw_content, len(raw_content))

    mock_session.get.return_value = resp_200
    mock_session.post.return_value = resp_200

    result = read_fn(arg, session=mock_session)
    assert isinstance(result, ReaderResult)
    assert result.complete is False
    assert result.error == "bad_json"
    assert result.postings == []


# ---------------------------------------------------------------------------
# Reader Error Code Mapping Tests: board_not_found, http_<status>, timeout
# ---------------------------------------------------------------------------

def test_reader_error_codes_greenhouse():
    mock_session = MagicMock(spec=requests.Session)
    resp_404 = MagicMock()
    resp_404.status_code = 404
    mock_session.get.return_value = resp_404

    # 404 -> board_not_found
    res_404 = read_greenhouse_board("test", session=mock_session)
    assert res_404.complete is False
    assert res_404.error == "board_not_found"

    # 401 -> http_401
    resp_401 = MagicMock()
    resp_401.status_code = 401
    mock_session.get.return_value = resp_401
    res_401 = read_greenhouse_board("test", session=mock_session)
    assert res_401.complete is False
    assert res_401.error == "http_401"

    # Timeout -> timeout
    mock_session.get.side_effect = requests.exceptions.Timeout("Connection timed out")
    res_timeout = read_greenhouse_board("test", session=mock_session)
    assert res_timeout.complete is False
    assert res_timeout.error == "timeout"


def test_workday_partial_pagination_error_preserves_postings():
    mock_session = MagicMock(spec=requests.Session)
    fixture_path = FIXTURES_BASE / "workday" / "response.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    # Page 1 returns postings (total=100)
    data["total"] = 100
    resp_page1 = MagicMock()
    resp_page1.status_code = 200
    resp_page1.json.return_value = data

    # Page 1 returns postings (total=100); subsequent calls time out
    mock_session.post.side_effect = [resp_page1] + [requests.exceptions.Timeout("timed out")] * 10

    result = read_workday_board("https://copart.wd12.myworkdayjobs.com/Copart", session=mock_session)
    assert isinstance(result, ReaderResult)
    assert result.complete is False
    assert result.error == "timeout"
    # Crucial: postings=<what it got>
    assert len(result.postings) == len(data["jobPostings"])
    assert len(result.postings) > 0


# ---------------------------------------------------------------------------
# Real Response Fixture Tests (tests/fixtures/job_source/<provider>/response.json)
# ---------------------------------------------------------------------------

def test_greenhouse_reader_result_success():
    fixture_path = FIXTURES_BASE / "greenhouse" / "response.json"
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


def test_lever_reader_result_success():
    fixture_path = FIXTURES_BASE / "lever" / "response.json"
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


def test_ashby_reader_result_success():
    fixture_path = FIXTURES_BASE / "ashby" / "response.json"
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


def test_smartrecruiters_reader_result_success():
    fixture_path = FIXTURES_BASE / "smartrecruiters" / "response.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    result = parse_smartrecruiters_response(data, slug="smartrecruiters")
    assert isinstance(result, ReaderResult)
    assert result.complete is True
    assert result.error is None
    assert len(result.postings) > 0


def test_bamboohr_reader_result_success():
    fixture_path = FIXTURES_BASE / "bamboohr" / "response.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    result = parse_bamboohr_response(data, company_slug="secunetics", company_name="Secunetics")
    assert isinstance(result, ReaderResult)
    assert result.complete is True
    assert result.error is None
    assert len(result.postings) > 0


def test_workday_reader_result_success():
    fixture_path = FIXTURES_BASE / "workday" / "response.json"
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
