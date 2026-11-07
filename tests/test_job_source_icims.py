"""Offline tests for the iCIMS board reader (Card 4).

All responses come from tests/fixtures/job_source/icims/; no test touches
the network, job-agent-db, or data/applications.db.
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from services.poller.icims import parse_icims_response, poll_icims, read_icims_board

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "job_source" / "icims"
BOARD = "example.icims.com"

EXPECTED_KEYS = {
    "external_id", "title", "location", "department", "description",
    "description_html", "apply_url", "salary_min", "salary_max",
    "salary_currency", "employment_type", "remote", "posted_at",
}


def _html(name):
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
    """Safety guard: fail any real network call and skip retry sleeps."""
    def _blocked(*args, **kwargs):
        raise RuntimeError("Live network request blocked in unit tests!")

    monkeypatch.setattr("requests.Session.get", _blocked)
    monkeypatch.setattr("requests.Session.post", _blocked)
    monkeypatch.setattr("services.poller.http._sleep", lambda s: None)


def test_ok_parses_count_ids_titles_urls():
    result = parse_icims_response(_html("ok.html"), board=BOARD)

    assert result.complete is True
    assert result.error is None
    assert [p["external_id"] for p in result.postings] == ["101", "102", "103", "104"]
    first = result.postings[0]
    assert first["title"] == "Network Engineer"
    assert first["location"] == "Fairfax, VA"
    assert first["apply_url"] == "https://example.icims.com/jobs/101/network-engineer/job"
    assert result.postings[1]["remote"] is True
    assert result.postings[3]["location"] == ""
    for posting in result.postings:
        assert set(posting.keys()) == EXPECTED_KEYS
        assert isinstance(posting["external_id"], str)


def test_ok_reads_board_in_one_request():
    session = _session_with(_resp(200, _html("ok.html")))

    result = read_icims_board(BOARD, session=session)

    assert result.complete is True
    assert result.error is None
    assert len(result.postings) == 4
    assert session.get.call_count == 1
    url, kwargs = session.get.call_args[0][0], session.get.call_args[1]
    assert url == f"https://{BOARD}/jobs/search"
    assert kwargs["params"] == {"ss": 1, "in_iframe": 1}


def test_empty_board_complete_with_zero_postings():
    parsed = parse_icims_response(_html("empty.html"), board=BOARD)
    assert parsed.complete is True
    assert parsed.error is None
    assert parsed.postings == []

    result = read_icims_board(BOARD, session=_session_with(_resp(200, _html("empty.html"))))
    assert result.complete is True
    assert result.error is None
    assert result.postings == []


def test_404_returns_board_not_found_without_retry():
    session = _session_with(_resp(404))

    result = read_icims_board(BOARD, session=session)

    assert result.complete is False
    assert result.error == "board_not_found"
    assert result.postings == []
    assert session.get.call_count == 1


def test_503_twice_then_ok(monkeypatch):
    sleeps = []
    monkeypatch.setattr("services.poller.http._sleep", sleeps.append)
    session = _session_with(_resp(503), _resp(503), _resp(200, _html("ok.html")))

    result = read_icims_board(BOARD, session=session)

    assert result.complete is True
    assert result.error is None
    assert len(result.postings) == 4
    assert session.get.call_count == 3
    assert len(sleeps) == 2


def test_truncated_returns_bad_json():
    parsed = parse_icims_response(_html("truncated.html"), board=BOARD)
    assert parsed.complete is False
    assert parsed.error == "bad_json"

    result = read_icims_board(BOARD, session=_session_with(_resp(200, _html("truncated.html"))))
    assert result.complete is False
    assert result.error == "bad_json"
    assert result.postings == []


def test_pagination_reads_every_page():
    session = _session_with(_resp(200, _html("page1.html")), _resp(200, _html("page2.html")))

    result = read_icims_board(BOARD, session=session)

    assert result.complete is True
    assert result.error is None
    assert [p["external_id"] for p in result.postings] == ["201", "202", "203"]
    assert session.get.call_count == 2
    second_url = session.get.call_args_list[1][0][0]
    assert "pg=2" in second_url


def test_page2_failure_keeps_page1_postings():
    session = _session_with(
        _resp(200, _html("page1.html")),
        *[requests.exceptions.Timeout("timed out")] * 6,
    )

    result = read_icims_board(BOARD, session=session)

    assert result.complete is False
    assert result.error == "timeout"
    assert [p["external_id"] for p in result.postings] == ["201", "202"]


def test_poll_icims_returns_postings_list():
    postings = poll_icims(BOARD, session=_session_with(_resp(200, _html("ok.html"))))

    assert isinstance(postings, list)
    assert [p["external_id"] for p in postings] == ["101", "102", "103", "104"]


def test_postings_fit_job_storage_sqlite(tmp_path):
    from services.diff_engine.storage import JobStorage

    storage = JobStorage(dsn=f"sqlite:///{tmp_path / 'icims_card4.db'}")
    result = parse_icims_response(_html("ok.html"), board=BOARD)

    for posting in result.postings:
        storage.insert_job({
            "external_id": posting["external_id"],
            "ats_platform": "icims",
            "company_slug": "example",
            "title": posting["title"],
            "location": posting["location"],
            "apply_url": posting["apply_url"],
            "status": "active",
        })

    assert storage.get_active_job_ids("icims", "example") == {"101", "102", "103", "104"}
def test_real_schwab_cards_take_location_not_requisition_id():
    result = parse_icims_response(_html("schwab.html"), board="career-schwab.icims.com")

    assert result.complete is True
    assert result.error is None
    assert [p["external_id"] for p in result.postings] == ["129663", "129596", "129593"]
    assert [p["location"] for p in result.postings] == [
        "US-CA-Carlsbad",
        "US-TX-El Paso",
        "US-TX-Southlake | US-TX-Austin",
    ]
    assert result.postings[0]["title"] == "VP, Financial Consultant - Carlsbad, CA"
    assert result.postings[0]["apply_url"] == (
        "https://career-schwab.icims.com/jobs/129663/"
        "vp%2c-financial-consultant---carlsbad%2c-ca/job"
    )
    assert result.postings[2]["title"] == "Associate - Software Development & Engineering"