"""Tests for form_preview.py (Task 5).

Ported from freehire internal/ingest/applyform/{greenhouse,lever,ashby}.go (MIT).
Tests:
- every required question answerable -> browser starts
- one unanswerable -> skips browser and records needs_answers
- site without preview API -> browser starts as today
"""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import config
import form_preview

FIXTURES = Path(__file__).parent / "fixtures" / "form_preview"


def test_greenhouse_preview_parser():
    data = json.loads((FIXTURES / "greenhouse_job.json").read_text(encoding="utf-8"))
    questions = form_preview.parse_greenhouse_response(data)
    assert len(questions) == 6
    assert questions[0]["label"] == "First Name"
    assert questions[0]["required"] is True
    assert questions[4]["label"] == "Are you legally authorized to work in the United States?"
    assert questions[4]["options"] == ["Yes", "No"]


def test_ashby_preview_parser():
    data = json.loads((FIXTURES / "ashby_job.json").read_text(encoding="utf-8"))
    questions = form_preview.parse_ashby_response(data)
    assert len(questions) == 4
    assert questions[0]["label"] == "Full Name"
    assert questions[0]["required"] is True
    assert questions[3]["label"] == "Will you now or in the future require sponsorship for an employment visa?"
    assert questions[3]["options"] == ["Yes", "No"]


def test_lever_preview_parser():
    html = (FIXTURES / "lever_job.html").read_text(encoding="utf-8")
    questions = form_preview.parse_lever_html(html)
    assert len(questions) == 5
    assert questions[1]["label"] == "Full name"
    assert questions[1]["required"] is True
    assert questions[4]["label"] == "Are you legally authorized to work in the United States?"
    assert questions[4]["options"] == ["Yes", "No"]


def test_preview_questions_unsupported_portal():
    job = SimpleNamespace(url="https://wd5.myworkdayjobs.com/company/job/123", company="Acme")
    assert form_preview.preview_questions(job) is None


def test_dry_mode_assessment_answerable_vs_unanswerable():
    profile = config.get_user_profile()
    job = SimpleNamespace(title="Network Engineer", company="Acme", url="https://boards.greenhouse.io/acme/jobs/123")

    # Ashby fixture has standard fields that profile/rules answer
    ashby_data = json.loads((FIXTURES / "ashby_job.json").read_text(encoding="utf-8"))
    ashby_questions = form_preview.parse_ashby_response(ashby_data)
    unanswerable_ashby = form_preview.unanswerable_required_questions(ashby_questions, profile, job)
    assert unanswerable_ashby == []

    # Greenhouse fixture has a required non-resume file upload (transcripts)
    gh_data = json.loads((FIXTURES / "greenhouse_job.json").read_text(encoding="utf-8"))
    gh_questions = form_preview.parse_greenhouse_response(gh_data)
    unanswerable_gh = form_preview.unanswerable_required_questions(gh_questions, profile, job)
    assert "Upload your official transcripts" in unanswerable_gh


def test_early_skip_records_needs_answers():
    profile = config.get_user_profile()
    job = SimpleNamespace(title="Network Engineer", company="Acme", url="https://boards.greenhouse.io/acme/jobs/123")
    tracker = SimpleNamespace(update_status=Mock())

    # When preview returns questions with an unanswerable required field
    gh_data = json.loads((FIXTURES / "greenhouse_job.json").read_text(encoding="utf-8"))
    with patch("form_preview.fetch_greenhouse_preview", return_value=form_preview.parse_greenhouse_response(gh_data)):
        skipped = form_preview.check_early_skip(job, profile, tracker, key="k1")
        assert skipped is True
        tracker.update_status.assert_called_once()
        call_args = tracker.update_status.call_args
        assert call_args[0][0] == "k1"
        assert call_args[0][1] == "needs_answers"
        assert "transcripts" in call_args[1]["notes"].lower()


def test_early_skip_passes_when_all_answerable():
    profile = config.get_user_profile()
    job = SimpleNamespace(title="Network Engineer", company="Acme", url="https://jobs.ashbyhq.com/acme/123")
    tracker = SimpleNamespace(update_status=Mock())

    ashby_data = json.loads((FIXTURES / "ashby_job.json").read_text(encoding="utf-8"))
    with patch("form_preview.fetch_ashby_preview", return_value=form_preview.parse_ashby_response(ashby_data)):
        skipped = form_preview.check_early_skip(job, profile, tracker, key="k2")
        assert skipped is False
        tracker.update_status.assert_not_called()


def test_api_error_returns_none_and_does_not_skip():
    profile = config.get_user_profile()
    job = SimpleNamespace(title="Engineer", company="Acme", url="https://boards.greenhouse.io/acme/jobs/123")
    tracker = SimpleNamespace(update_status=Mock())

    with patch("requests.Session.get", side_effect=Exception("Connection refused")):
        questions = form_preview.preview_questions(job)
        assert questions is None
        skipped = form_preview.check_early_skip(job, profile, tracker, key="k3")
        assert skipped is False
        tracker.update_status.assert_not_called()


def test_site_adapters_preview_questions():
    import sites
    gh_job = SimpleNamespace(url="https://boards.greenhouse.io/test/jobs/1")
    lever_job = SimpleNamespace(url="https://jobs.lever.co/test/1")
    ashby_job = SimpleNamespace(url="https://jobs.ashbyhq.com/test/1")
    wd_job = SimpleNamespace(url="https://wd5.myworkdayjobs.com/test/1")

    with patch("form_preview.fetch_greenhouse_preview", return_value=[{"label": "Q1"}]) as m_gh:
        assert sites.adapter_for(gh_job.url).preview_questions(gh_job) == [{"label": "Q1"}]
        m_gh.assert_called_once()

    with patch("form_preview.fetch_lever_preview", return_value=[{"label": "Q2"}]) as m_lv:
        assert sites.adapter_for(lever_job.url).preview_questions(lever_job) == [{"label": "Q2"}]
        m_lv.assert_called_once()

    with patch("form_preview.fetch_ashby_preview", return_value=[{"label": "Q3"}]) as m_as:
        assert sites.adapter_for(ashby_job.url).preview_questions(ashby_job) == [{"label": "Q3"}]
        m_as.assert_called_once()

    assert sites.adapter_for(wd_job.url).preview_questions(wd_job) is None


def test_ai_answerable_questions_do_not_skip():
    """An open question like 'Why do you want to work here?' does NOT skip, but unanswerable EEO does."""
    profile = config.get_user_profile()
    job = SimpleNamespace(title="Engineer", company="Acme", url="https://boards.greenhouse.io/acme/jobs/123")

    # Open questions the AI can answer
    questions = [
        {"label": "Why do you want to work here?", "required": True, "type": "textarea", "options": []},
        {"label": "Please describe your experience with Python", "required": True, "type": "textarea", "options": []},
    ]
    unanswerable = form_preview.unanswerable_required_questions(questions, profile, job)
    assert unanswerable == []

    # EEO question with no fact and no decline option
    empty_profile = config.UserProfile(gender="", ethnicity="", veteran_status="", disability_status="")
    eeo_questions = [
        {"label": "What is your gender?", "required": True, "type": "select", "options": ["Male", "Female"]},
    ]
    unanswerable_eeo = form_preview.unanswerable_required_questions(eeo_questions, empty_profile, job)
    assert unanswerable_eeo == ["What is your gender?"]

    # Non-resume required file upload causes a skip
    doc_questions = [
        {"label": "Upload your official transcripts", "required": True, "type": "file", "options": []},
    ]
    unanswerable_doc = form_preview.unanswerable_required_questions(doc_questions, profile, job)
    assert unanswerable_doc == ["Upload your official transcripts"]

    # Resume upload does not cause a skip
    resume_questions = [
        {"label": "Resume / CV", "required": True, "type": "file", "options": []},
    ]
    unanswerable_resume = form_preview.unanswerable_required_questions(resume_questions, profile, job)
    assert unanswerable_resume == []
