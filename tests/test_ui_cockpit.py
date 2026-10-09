"""Unit and integration tests for the Live Agent Cockpit, Answer Transparency Ledger,
Desktop CAPTCHA Handoff, and System Health endpoints.
"""
from __future__ import annotations

import json
import sqlite3
import pytest
from flask import Flask

import web_ui
import ui_shell
import handoff
from runtime_events import RuntimeEvent, EventName, ReasonCode, AnswerSource


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Test client with test DB and isolated environment."""
    test_db = tmp_path / "test_applications.db"
    monkeypatch.setattr(web_ui, "_LEDGER_DB_PATH", test_db)
    
    app = web_ui.app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_init_and_record_answer_ledger(tmp_path, monkeypatch):
    test_db = tmp_path / "test_answers.db"
    monkeypatch.setattr(web_ui, "_LEDGER_DB_PATH", test_db)

    # Record first answer
    row_id = web_ui.record_answer_ledger(
        application_key="app_test_123",
        question_text="Are you legally authorized to work in the US?",
        final_answer="Yes",
        source=AnswerSource.PROFILE.value,
        verification_result="ACCEPTED",
    )
    assert row_id > 0

    answers = web_ui.get_answers_for_application("app_test_123")
    assert len(answers) == 1
    assert answers[0]["question_text"] == "Are you legally authorized to work in the US?"
    assert answers[0]["final_answer"] == "Yes"
    assert answers[0]["source"] == "PROFILE"
    assert answers[0]["previous_value"] == ""

    # Correct the answer
    web_ui.record_answer_ledger(
        application_key="app_test_123",
        question_text="Are you legally authorized to work in the US?",
        final_answer="Yes, Citizen",
        source=AnswerSource.PROFILE.value,
        verification_result="CORRECTED",
        previous_value="Yes",
        correction_reason="Profile clarification",
    )

    answers_after = web_ui.get_answers_for_application("app_test_123")
    assert len(answers_after) == 1
    assert answers_after[0]["final_answer"] == "Yes, Citizen"
    assert answers_after[0]["previous_value"] == "Yes"
    assert answers_after[0]["correction_reason"] == "Profile clarification"


def test_runtime_events_auto_record_into_ledger(tmp_path, monkeypatch):
    test_db = tmp_path / "test_events_ledger.db"
    monkeypatch.setattr(web_ui, "_LEDGER_DB_PATH", test_db)

    # Emit ANSWER_WRITTEN
    RuntimeEvent.emit(
        event_name=EventName.ANSWER_WRITTEN,
        component="page_agent",
        stage="form_filling",
        display_message="Wrote answer for City",
        application_key="app_auto_1",
        safe_metadata={
            "question": "What is your city of residence?",
            "answer": "Austin",
            "source": AnswerSource.PROFILE.value,
        },
    )

    answers = web_ui.get_answers_for_application("app_auto_1")
    assert len(answers) == 1
    assert answers[0]["question_text"] == "What is your city of residence?"
    assert answers[0]["final_answer"] == "Austin"

    # Emit ANSWER_CORRECTED
    RuntimeEvent.emit(
        event_name=EventName.ANSWER_CORRECTED,
        component="page_agent",
        stage="form_filling",
        display_message="Corrected City to Round Rock",
        application_key="app_auto_1",
        safe_metadata={
            "question": "What is your city of residence?",
            "previous_value": "Austin",
            "new_value": "Round Rock",
            "source": AnswerSource.PROFILE.value,
            "reason": "exact profile match",
        },
    )

    answers2 = web_ui.get_answers_for_application("app_auto_1")
    assert len(answers2) == 1
    assert answers2[0]["final_answer"] == "Round Rock"
    assert answers2[0]["previous_value"] == "Austin"
    assert answers2[0]["correction_reason"] == "exact profile match"


def test_api_cockpit_state(client, monkeypatch):
    # Set an active handoff
    h = handoff.build(
        application_key="app_cockpit_test",
        employer="Test Corp",
        portal="greenhouse.io",
        outcome_kind="captcha",
        reason_text="Cloudflare Turnstile challenge detected",
    )
    handoff.set_active_handoff(h)

    res = client.get("/api/cockpit-state")
    assert res.status_code == 200
    data = res.get_json()
    assert data is not None
    assert "is_running" in data
    assert "stage" in data
    assert "loop_status" in data
    assert data["active_handoff"] is not None
    assert data["active_handoff"]["category"] == handoff.CAPTCHA
    assert "Cloudflare Turnstile" in data["active_handoff"]["reason"]

    # Clear handoff
    handoff.clear_active_handoff()
    res2 = client.get("/api/cockpit-state")
    assert res2.get_json()["active_handoff"] is None


def test_api_cockpit_controls(client, monkeypatch):
    # Test pause
    res_pause = client.post("/api/cockpit/pause")
    assert res_pause.status_code == 200
    assert res_pause.get_json()["ok"] is True

    # Test resume
    res_resume = client.post("/api/cockpit/resume")
    assert res_resume.status_code == 200
    assert res_resume.get_json()["ok"] is True

    # Test raise browser
    monkeypatch.setattr(web_ui, "raise_browser_window", lambda: True)
    res_raise = client.post("/api/cockpit/raise-browser")
    assert res_raise.status_code == 200
    assert res_raise.get_json()["ok"] is True

    # Test takeover
    res_takeover = client.post("/api/cockpit/takeover")
    assert res_takeover.status_code == 200
    assert res_takeover.get_json()["ok"] is True
    active = handoff.get_active_handoff()
    assert active is not None
    assert active["category"] == handoff.OWNER_REVIEW
    handoff.clear_active_handoff()


def test_captcha_handoff_resolution_fail_closed(client):
    # Set an active CAPTCHA handoff
    h = handoff.build(
        application_key="app_captcha_test",
        employer="Acme",
        portal="jobs.acme.com",
        outcome_kind="captcha",
        reason_text="reCAPTCHA visible on page",
    )
    handoff.set_active_handoff(h)

    # Resolution with failing live verification
    resolved, msg = handoff.resolve_active_handoff(
        live_verification_fn=lambda: (False, "CAPTCHA iframe still visible in DOM")
    )
    assert resolved is False
    assert "CAPTCHA iframe still visible" in msg

    # Handoff must remain active (fail-closed invariant B3 / B4)
    assert handoff.get_active_handoff() is not None

    # Resolution with successful live verification
    resolved_ok, msg_ok = handoff.resolve_active_handoff(
        live_verification_fn=lambda: (True, "No CAPTCHA visible")
    )
    assert resolved_ok is True
    assert handoff.get_active_handoff() is None


def test_api_answers_update(client, tmp_path, monkeypatch):
    test_db = tmp_path / "test_update_answers.db"
    monkeypatch.setattr(web_ui, "_LEDGER_DB_PATH", test_db)

    aid = web_ui.record_answer_ledger(
        application_key="app_update_test",
        question_text="Preferred pronoun",
        final_answer="They/Them",
        source="AI_INFERENCE",
    )

    res = client.post("/api/answers/update", data={"answer_id": aid, "new_value": "He/Him"})
    assert res.status_code == 200
    assert res.get_json()["ok"] is True

    answers = web_ui.get_answers_for_application("app_update_test")
    assert len(answers) == 1
    assert answers[0]["final_answer"] == "He/Him"
    assert answers[0]["source"] == "USER_OVERRIDE"
    assert answers[0]["previous_value"] == "They/Them"
    assert answers[0]["status"] == "OVERRIDDEN"


def test_health_check_json_and_html(client):
    # JSON check
    res_json = client.get("/health", headers={"Accept": "application/json"})
    assert res_json.status_code == 200
    data = res_json.get_json()
    assert "status" in data
    assert "checks" in data
    assert "playwright" in data["checks"]
    assert "profile" in data["checks"]
    assert "resume" in data["checks"]
    assert "ai_provider" in data["checks"]
    assert "database" in data["checks"]

    # HTML check
    res_html = client.get("/health", headers={"Accept": "text/html"})
    assert res_html.status_code == 200
    assert "System Health" in res_html.get_data(as_text=True)
    assert "Playwright Chromium" in res_html.get_data(as_text=True)


@pytest.mark.parametrize("accept", ["application/json", "text/html"])
def test_database_health_reports_a_working_tracker(client, monkeypatch, tmp_path, accept):
    from job_tracker import JobTracker

    tracker = JobTracker(tmp_path / "health_applications.db")
    monkeypatch.setenv("TRACKER", "sqlite")
    monkeypatch.setattr(web_ui, "get_tracker", lambda: tracker)

    response = client.get("/health", headers={"Accept": accept})
    assert response.status_code == 200
    if accept == "application/json":
        check = response.get_json()["checks"]["database"]
        assert check["ok"] is True
        assert check["message"] == "Database connected (sqlite)"
    else:
        body = response.get_data(as_text=True)
        assert "Database connected (sqlite)" in body
        assert "Database unavailable" not in body


@pytest.mark.parametrize("accept", ["application/json", "text/html"])
def test_database_health_reports_a_tracker_failure(client, monkeypatch, accept):
    def unavailable():
        raise RuntimeError("synthetic tracker unavailable")

    monkeypatch.setattr(web_ui, "get_tracker", unavailable)
    response = client.get("/health", headers={"Accept": accept})
    assert response.status_code == 200
    message = "Database unavailable: synthetic tracker unavailable"
    if accept == "application/json":
        check = response.get_json()["checks"]["database"]
        assert check["ok"] is False
        assert check["message"] == message
    else:
        assert message in response.get_data(as_text=True)
