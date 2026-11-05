"""Tests for Phase 4B: Local Web Control Panel (7 Screens & API Endpoints)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import web_ui
from services.matcher.search_spec import SearchSpec


@pytest.fixture
def client():
    web_ui.app.config["TESTING"] = True
    return web_ui.app.test_client()


def test_screen_1_search_spec_page_loads(client):
    """Screen 1: Profile & Search Instructions page loads."""
    resp = client.get("/search-spec")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Search Instructions" in html
    assert "Parse Instructions" in html or "parse-btn" in html


def test_screen_1_parse_and_save_api(client):
    """Screen 1 API: parse instructions and save versioned spec."""
    # 1. Parse instructions
    with patch("services.matcher.search_spec_parser._call_claude_api") as mock_claude:
        mock_claude.return_value = {
            "target_titles": ["Senior Network Engineer"],
            "title_synonyms": {},
            "adjacent_titles": [],
            "seniority_levels": ["Senior"],
            "must_have_keywords": ["cisco"],
            "nice_to_have_keywords": ["bgp"],
            "locations": ["Fairfax, VA", "Remote"],
            "workplace_types": ["remote"],
            "salary_floor_annual": 130000,
            "salary_currency": "USD",
            "employment_types": ["full-time"],
            "requires_visa_sponsorship": True,
            "excluded_companies": [],
            "excluded_keywords": ["clearance"],
            "posting_age_window_hours": 72,
            "hard_criteria": {"title": True, "location": True},
        }
        parse_resp = client.post(
            "/api/search-spec/parse",
            json={"instructions": "Senior Network Engineer in Fairfax or Remote paying 130k"},
        )
        assert parse_resp.status_code == 200
        spec_data = parse_resp.get_json()["spec"]
        assert spec_data["salary_floor_annual"] == 130000

    # 2. Save spec
    save_resp = client.post("/api/search-spec/save", json={"spec": spec_data})
    assert save_resp.status_code == 200
    assert save_resp.get_json()["saved"] is True


def test_screen_3_funnel_and_sources_page_loads(client):
    """Screen 3: Funnel & Sources with rejection explorer."""
    resp = client.get("/funnel")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Discovery Funnel" in html
    assert "Rejection Explorer" in html


def test_screen_4_review_queue_page_and_approval(client, tmp_path):
    """Screen 4: Review Queue loads pending questions and approves with learning."""
    from services.matcher.answer_engine import SemanticAnswerEngine
    engine = SemanticAnswerEngine(storage_dsn=f"sqlite:///{tmp_path}/web_panel.db")
    with patch("services.web_control_panel._get_answer_engine", return_value=engine):
        engine.resolve_question(
            question="Please provide your direct supervisor's mobile phone number for Lockheed verification:",
            application_id=10,
            company_name="Lockheed",
            job_title="Network Deployment Engineer",
        )

        resp = client.get("/review-queue")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Review Queue" in html
        assert "Lockheed" in html

        # Approve item via API
        items = engine.get_pending_review_items()
        review_id = items[0]["id"]
        approve_resp = client.post(
            "/api/review-queue/approve",
            json={
                "review_id": review_id,
                "user_answer": "571-555-0199",
                "save_as_reusable": True,
            },
        )
        assert approve_resp.status_code == 200
        assert approve_resp.get_json()["success"] is True


def test_screen_5_answer_bank_page_and_test_question(client):
    """Screen 5: Answer Bank with canonical intents and question tester."""
    resp = client.get("/answer-bank")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Answer Bank" in html
    assert "Yaswanth Reddy" in html

    # Test question resolver API
    test_resp = client.post(
        "/api/answer-bank/test-question",
        json={"question": "Do you need visa sponsorship to work in the US?"},
    )
    assert test_resp.status_code == 200
    result = test_resp.get_json()
    assert result["intent"] == "sponsorship"
    assert result["answer"] == "Yes"


def test_screen_7_run_control_toggle(client):
    """Screen 7: Run Control with dry-run toggle (on by default)."""
    resp = client.get("/run-control")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Run Control" in html
    assert "Dry-Run" in html

    toggle_resp = client.post("/api/run-control/dry-run", json={"dry_run": False})
    assert toggle_resp.status_code == 200
    assert toggle_resp.get_json()["dry_run"] is False

