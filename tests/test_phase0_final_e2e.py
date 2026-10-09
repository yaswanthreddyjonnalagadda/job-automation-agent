"""Phase 0 final: cross-phase end-to-end tests.

Each of P0-B1 through P0-B5 already has deep, dedicated coverage of its own behavior in
isolation (see docs/security/phase0-final-e2e-review.md for the full inventory). What that
review found missing was evidence that the phases compose correctly through the real
production bridge across a single continuous run -- not whether each phase's own guard
still works alone. These tests chain real production code (apply_flow.hand_over ->
apply_flow.submit_verified -> the real JobApplicationAssistant.click_verified_submit ->
SubmissionGuardV0, plus diagnostics.py's sanitization) rather than re-testing any one
phase's internals. Synthetic ATS only; no live employer site; no real personal data --
sentinel values only.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import apply_flow
import diagnostics
import safety
from browser_automation import JobApplicationAssistant


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    pg = browser.new_page()
    yield pg
    pg.close()


@pytest.fixture
def agent():
    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a.values = safety.AgentValues()
    return a


class Profile:
    full_name = "Yaswanth Reddy Jonnalagadda"
    email = "me@example.com"
    city = "Fairfax"
    state = "Virginia"


class Job:
    title, company, url = "Network Engineer", "Example Corp", "https://jobs.example.com/apply/42"


class Tracker:
    """Mirrors JobTracker's durable submission-effect state machine (the same stand-in
    test_auto_submit.py uses) -- real replay-blocked/DISPATCHED/CONFIRMED/UNCERTAIN semantics,
    without a SQLite file, since that durability is already proven independently by
    test_submission_effect_state.py against the real JobTracker."""

    def __init__(self):
        self.status = None
        self.notes = None
        self.events = []
        self.submission_state = None

    def update_status(self, key, status, notes=None):
        self.status, self.notes = status, notes

    def record_event(self, key, kind, message="", screenshot_path="", html_path="", payload=None):
        self.events.append((kind, message, payload))

    def get_submission_effect_state(self, key):
        return self.submission_state

    def begin_submission_dispatch(self, key, aliases=()):
        if self.submission_state in {"DISPATCHED", "CONFIRMED", "UNCERTAIN"}:
            raise RuntimeError("replay blocked")
        self.events.append(("SUBMISSION_AUTHORIZED", "", {}))
        self.submission_state = "DISPATCHED"
        self.events.append(("SUBMISSION_DISPATCHED", "", {}))

    def finish_submission_effect(self, key, state, *, reconciled=False, evidence_kind=None):
        if self.submission_state != "DISPATCHED" and not (
            self.submission_state == "UNCERTAIN" and state == "CONFIRMED" and reconciled
        ):
            raise RuntimeError("invalid state transition")
        self.submission_state = state
        self.events.append((f"SUBMISSION_{state}", "", {"evidence_kind": evidence_kind}))

    def record_submission_safety_event(self, key, kind, payload=None):
        self.events.append((kind, "", payload or {}))

    def document_matches(self, key, kind, path):
        return True

    def get(self, key):
        class Record:
            title, company = Job.title, Job.company
            url = Job.url
            status = "prepared"
        return Record()


def serve(page, body: str, url: str = "https://jobs.example.com/apply/42"):
    page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=body))
    page.goto(url)


def test_a_full_run_is_submitted_confirmed_diagnostics_stay_private_and_a_duplicate_is_refused(
        page, tmp_path, agent):
    """Chains B4 (verified field write) -> B1 (gateway dispatch through the real
    JobApplicationAssistant.click_verified_submit/SubmissionGuardV0) -> confirmation, then
    checks B5 (a diagnostic captured mid-run carries no PII sentinel) and finally B1 x B3
    again (a resumed/duplicate attempt on the same tracker/key, through the real
    apply_flow.submit_verified bridge -- not just the tracker unit level already proven in
    test_submission_effect_state.py -- must be refused before any click)."""
    serve(page,
          "<label for=c>City *</label><input id=c required>"
          "<p>Attached: Resume_Example.pdf</p>"
          "<button id=go onclick=\"document.body.innerHTML='<h1>Application Received!</h1>'\">"
          "Submit application</button>")
    agent.employer = "Example Corp"
    agent.set_value(page, "[id='c']", "Fairfax", "City *", source="profile:city")

    tracker = Tracker()
    summary = tmp_path / "review_summary.json"
    summary.write_text("{}", encoding="utf-8")
    resume = tmp_path / "Resume_Example.pdf"
    resume.write_bytes(b"%PDF-1.4")

    class Config:
        auto_submit_verified_only = True

    status = apply_flow.hand_over(agent, page, tracker, "key", Job(), tmp_path, resume.name, summary,
                                  config=Config(), profile=Profile(), documents={"resume": resume})

    assert status == "submitted" and tracker.status == "submitted"
    assert tracker.submission_state == "CONFIRMED"

    # B5: a diagnostic captured during this run must carry no PII sentinel, regardless of
    # what the live page happened to show at capture time.
    page.set_content("<p>Applicant: Jane PII_SENTINEL_NAME Doe, phone 555-SENTINEL-0100</p>")
    out = tmp_path / "page_1.txt"  # a diagnostics.py-recognized artifact name (DIAGNOSTIC_PATTERNS)
    assert diagnostics.write_safe_text(
        out, diagnostics.sanitize_snapshot(page.locator("body").aria_snapshot(mode="ai")))
    saved = out.read_text(encoding="utf-8")
    assert "PII_SENTINEL_NAME" not in saved and "SENTINEL-0100" not in saved

    # B1 x B3: a resumed/duplicate attempt on the SAME tracker/key, through the real
    # production bridge, must be refused before any click -- never re-dispatched, never
    # reset off a confirmed state.
    class ResumedRunAssistant:
        def click_verified_submit(self, page, authorization):
            raise AssertionError("a resumed run must never re-click submit after confirmation")

    ok = apply_flow.submit_verified(ResumedRunAssistant(), page, tracker, "key", Job(), tmp_path,
                                    safety.AutoSubmitDecision(eligible=True))

    assert ok is False
    assert tracker.submission_state == "CONFIRMED"  # unchanged: not reset, not re-dispatched
