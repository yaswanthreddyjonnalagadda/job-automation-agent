"""Phase 0-B3: a process restart must not blindly repeat a non-idempotent action.

Account creation is the one consequential action discovery found with no durable guard
before this phase (docs/security/phase0-b3-discovery.md section 8). The existing in-memory
`_created_at` guard (account_state.py: "the new-account form is still showing after the
account was created") already protects a *second call on the same PageAgent object* --
tests/test_account_step.py's test_a_new_account_form_creates_the_account_once covers that.
These tests cover what it does not: a second, independent PageAgent instance -- standing in
for a resumed process, sharing nothing in memory with the first -- that only has the durable
tracker in common.
"""
from types import SimpleNamespace

import pytest

import config
import job_tracker
import page_agent


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


class Assistant:
    def __init__(self, gate=True):
        self._login_paused, self._gate, self.calls = "", gate, []
        self._last_login_rejected = False
        self.submission_key = "subkey-restart-1"

    def adapter(self, tab):
        return SimpleNamespace(candidate_account_state=lambda tab: "")

    def account_on_record(self):
        return False

    def handle_auth_gate(self, tab, email):
        self.calls.append(("gate", email))
        return self._gate


CREATE = """<h2>Create Account</h2><label>Email Address<input type="email"></label>
<label>Password<input type="password"></label><label>Verify New Password<input type="password"></label>
<button>Create Account</button>"""
APPLICATION_FORM = "<h2>My Information</h2><label>First Name<input></label>"


def open_page(browser, body):
    context = browser.new_context()
    pg = context.new_page()
    pg.route("https://jobs.example.com/**", lambda route: route.fulfill(
        status=200, content_type="text/html", body=f"<html><body>{body}</body></html>"))
    pg.goto("https://jobs.example.com/apply")
    return pg


def new_agent(assistant, tracker, tmp_path):
    """A fresh PageAgent with no in-memory history -- standing in for a freshly-started
    process, the same way tests/test_login_guard.py's own restart test ("a new run: a new
    object") stands in for one."""
    job = SimpleNamespace(title="Engineer", company="Acme", url="https://jobs.example.com/apply")
    return page_agent.PageAgent(
        assistant, SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email="jane@example.com"),
        config.UserProfile(email="jane@example.com"), SimpleNamespace(raw_text="x"), job,
        tracker=tracker, key="k1", resume_file=None, job_dir=tmp_path)


def step(page, agent):
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    return agent.sign_in_step(page, controls)


def test_a_fresh_process_does_not_press_create_again_after_an_unverified_dispatch(browser, tmp_path):
    tracker = job_tracker.JobTracker(tmp_path / "applications.db")
    tracker.create(dedup_key="k1", title="Engineer", company="Acme", location="Remote",
                   url="https://jobs.example.com/apply")
    assistant = Assistant(gate=True)

    first_page = open_page(browser, CREATE)
    first = new_agent(assistant, tracker, tmp_path)
    assert step(first_page, first) is True
    assert assistant.calls == [("gate", "jane@example.com")]
    stored = tracker.read_checkpoint(assistant.submission_key)
    assert stored and stored["pending_action"] == "account_creation_dispatched@jobs.example.com"

    # The page never actually navigated past the create form (the mock gate does not touch
    # the DOM) -- exactly the "outcome unknown" case: a real process would have died between
    # the click and the confirmation. A second, independent PageAgent -- standing in for a
    # resumed process, reopening the same still-showing create form -- must not press Create
    # Account again.
    second_page = open_page(browser, CREATE)
    second = new_agent(assistant, tracker, tmp_path)
    assert step(second_page, second) is False
    assert "earlier attempt" in second.account_blocker and "already" in second.account_blocker
    assert assistant.calls == [("gate", "jane@example.com")]   # not called again


def test_the_marker_clears_once_live_evidence_shows_the_create_form_is_behind_us(browser, tmp_path):
    tracker = job_tracker.JobTracker(tmp_path / "applications.db")
    tracker.create(dedup_key="k1", title="Engineer", company="Acme", location="Remote",
                   url="https://jobs.example.com/apply")
    assistant = Assistant(gate=True)

    first = new_agent(assistant, tracker, tmp_path)
    step(open_page(browser, CREATE), first)
    assert tracker.read_checkpoint(assistant.submission_key)["pending_action"]

    # The site actually moved on: a freshly-resumed process reopens the application and finds
    # the create form is no longer there. Live evidence, not memory, resolves the marker.
    second = new_agent(assistant, tracker, tmp_path)
    step(open_page(browser, APPLICATION_FORM), second)
    assert tracker.read_checkpoint(assistant.submission_key)["pending_action"] == ""


def test_a_third_process_may_create_the_account_once_the_marker_is_resolved(browser, tmp_path):
    """Resolving the marker does not itself forbid a later, legitimate Create Account -- it
    only blocks retrying while the previous attempt's outcome is still unverified."""
    tracker = job_tracker.JobTracker(tmp_path / "applications.db")
    tracker.create(dedup_key="k1", title="Engineer", company="Acme", location="Remote",
                   url="https://jobs.example.com/apply")
    assistant = Assistant(gate=True)

    step(open_page(browser, CREATE), new_agent(assistant, tracker, tmp_path))
    step(open_page(browser, APPLICATION_FORM), new_agent(assistant, tracker, tmp_path))
    assert assistant.calls == [("gate", "jane@example.com")]

    third = new_agent(assistant, tracker, tmp_path)
    assert step(open_page(browser, CREATE), third) is True
    assert assistant.calls == [("gate", "jane@example.com"), ("gate", "jane@example.com")]


def test_no_tracker_does_not_block_account_creation(browser, tmp_path):
    """A run with no tracker configured (a test double, or tracking genuinely unavailable)
    behaves exactly as before this phase -- the lifecycle is additive, never a new required
    dependency for account creation to proceed at all."""
    assistant = Assistant(gate=True)
    job = SimpleNamespace(title="Engineer", company="Acme", url="https://jobs.example.com/apply")
    agent = page_agent.PageAgent(
        assistant, SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email="jane@example.com"),
        config.UserProfile(email="jane@example.com"), SimpleNamespace(raw_text="x"), job,
        resume_file=None, job_dir=tmp_path)
    assert step(open_page(browser, CREATE), agent) is True
    assert assistant.calls == [("gate", "jane@example.com")]
