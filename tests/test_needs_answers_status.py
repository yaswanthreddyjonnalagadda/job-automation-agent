"""Tests for status 'needs_answers' across tracker, UI shell, and web dashboard."""
from pathlib import Path
import pytest

import db
import job_tracker
import ui_shell
import web_guard
import web_ui


def test_status_constants():
    assert job_tracker.STATUS_NEEDS_ANSWERS == "needs_answers"
    assert db.STATUS_NEEDS_ANSWERS == "needs_answers"
    assert "needs_answers" in web_ui.VALID_STATUSES


def test_ui_shell_display_for_needs_answers():
    assert ui_shell.status_label("needs_answers") == "Needs answers"
    assert ui_shell.status_group("needs_answers") == "needs"
    badge = ui_shell.status_badge("needs_answers")
    assert "sbadge needs" in badge
    assert "Needs answers" in badge


def test_tracker_accepts_needs_answers_status(tmp_path):
    db_file = tmp_path / "test_apps.db"
    tracker = job_tracker.JobTracker(db_file)

    app_id = tracker.create(
        dedup_key="test_job_1",
        title="Network Engineer",
        company="Acme Corp",
        location="Boston, MA",
        source_site="greenhouse",
        url="https://boards.greenhouse.io/acme/jobs/1",
        resume_path="resumes/resume.pdf",
    )
    assert app_id > 0

    # Update status to needs_answers with notes
    tracker.update_status("test_job_1", "needs_answers", notes="Missing salary expectation")
    rec = tracker.get("test_job_1")
    assert rec is not None
    assert rec.status == "needs_answers"
    assert "Missing salary expectation" in (rec.notes or "")


def test_web_ui_accepts_needs_answers_status(monkeypatch, tmp_path):
    db_file = tmp_path / "test_apps_ui.db"
    tracker = job_tracker.JobTracker(db_file)
    app_id = tracker.create(
        dedup_key="test_job_ui",
        title="Systems Engineer",
        company="Initech",
        location="Remote",
        source_site="ashby",
        url="https://jobs.ashbyhq.com/initech/1",
        resume_path="resumes/resume.pdf",
    )
    monkeypatch.setattr(web_ui, "get_tracker", lambda: tracker)

    web_ui.app.config["TESTING"] = True
    client = web_ui.app.test_client()
    resp = client.post(
        f"/application/{app_id}/status",
        data={"status": "needs_answers", "notes": "EEO race missing", "csrf_token": web_guard.TOKEN},
    )
    assert resp.status_code == 302  # redirect after successful update

    rec = tracker.get("test_job_ui")
    assert rec.status == "needs_answers"
    assert "EEO race missing" in rec.notes
