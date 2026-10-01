"""The owner's Workday accounts were being locked: every run, restart and Continue tried the sign-in again.

Researched 25 September 2026: a Workday external candidate account is locked by a few wrong passwords
(NVIDIA's support: 5 in a row, 30 minutes; other tenants 3); the sign-in page says the same thing for
a wrong password, no account, an unverified account and a locked one; "Forgot your password" mails a
link (not a code) valid 2 hours, 5 requests in 24 hours. The agent forgot every attempt when its run
ended, so nothing stopped the next run from spending another one.

The class: a limited resource (sign-in attempts) spent without a memory of what was spent.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import login_guard
import safety
from browser_automation import JobApplicationAssistant
from sites.base import SiteAdapter
from sites.workday import WorkdayAdapter

HOST = "tenant.wd1.myworkdayjobs.com"
EMAIL = "owner@example.com"
REJECT = "You may have entered the wrong email address or password or your account might be locked."


# --- the memory ------------------------------------------------------------------------------------

def test_a_first_sign_in_is_allowed():
    assert login_guard.may_sign_in(HOST, EMAIL) is None


def test_a_rejected_sign_in_holds_the_next_until_the_owner_resumes():
    login_guard.record_sign_in(HOST, EMAIL, ok=False)
    why = login_guard.may_sign_in(HOST, EMAIL)
    assert why and "Continue" in why and HOST in why
    login_guard.owner_resumed(HOST)
    assert login_guard.may_sign_in(HOST, EMAIL) is None


def test_two_rejections_in_a_day_stop_it_whatever_the_owner_presses():
    login_guard.record_sign_in(HOST, EMAIL, ok=False)
    login_guard.owner_resumed(HOST)
    login_guard.record_sign_in(HOST, EMAIL, ok=False)
    login_guard.owner_resumed(HOST)
    why = login_guard.may_sign_in(HOST, EMAIL)
    assert why and "24 hours" in why and "lock" in why


def test_the_rejections_are_forgotten_after_a_day(monkeypatch):
    login_guard.record_sign_in(HOST, EMAIL, ok=False)
    login_guard.owner_resumed(HOST)
    login_guard.record_sign_in(HOST, EMAIL, ok=False)
    login_guard.owner_resumed(HOST)
    later = datetime.now(timezone.utc) + timedelta(hours=25)
    monkeypatch.setattr(login_guard, "_now", lambda: later)
    assert login_guard.may_sign_in(HOST, EMAIL) is None


def test_a_sign_in_that_worked_clears_the_count_and_the_hold():
    login_guard.record_sign_in(HOST, EMAIL, ok=False)
    login_guard.record_sign_in(HOST, EMAIL, ok=True)
    assert login_guard.may_sign_in(HOST, EMAIL) is None


def test_each_site_and_each_email_is_counted_on_its_own():
    login_guard.record_sign_in(HOST, EMAIL, ok=False)
    assert login_guard.may_sign_in("other.wd1.myworkdayjobs.com", EMAIL) is None       # another tenant
    assert login_guard.may_sign_in(HOST, "someone.else@example.com") is None           # another account
    assert login_guard.may_sign_in(HOST.upper(), EMAIL.upper())                        # spelling is not a difference


def test_account_creation_and_password_resets_are_limited_too():
    assert login_guard.may_create_account(HOST, EMAIL) is None
    for _ in range(login_guard.MAX_ACCOUNT_CREATIONS):
        login_guard.record_account_attempt(HOST, EMAIL)
    assert login_guard.may_create_account(HOST, EMAIL)
    assert login_guard.may_request_reset(HOST, EMAIL) is None
    login_guard.record_reset_request(HOST, EMAIL)
    assert login_guard.may_request_reset(HOST, EMAIL)


@pytest.mark.parametrize("content", ["", "not json", "[1, 2]", '{"a": '])
def test_a_damaged_record_never_stops_a_run(content):
    login_guard.LOGIN_ATTEMPTS_FILE.write_text(content, encoding="utf-8")
    assert login_guard.may_sign_in(HOST, EMAIL) is None
    login_guard.record_sign_in(HOST, EMAIL, ok=False)               # and is replaced
    assert login_guard.may_sign_in(HOST, EMAIL)


def test_a_record_that_cannot_be_written_never_stops_a_run(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a folder should be", encoding="utf-8")
    monkeypatch.setattr(login_guard, "LOGIN_ATTEMPTS_FILE", blocker / "deeper" / "x.json")
    login_guard.record_sign_in(HOST, EMAIL, ok=False)               # must not raise


# --- the sign-in itself ---------------------------------------------------------------------------------

REJECTING_SIGN_IN = """<html><body><h2>Sign In</h2><div id="alert" role="alert"></div>
<label for="e">Email Address*</label><input id="e" type="email">
<label for="p">Password*</label><input id="p" type="password">
<button id="go">Sign In</button><a href="#" id="forgot">Forgot your password?</a>
<script>
  window.tries = 0; window.forgot = 0;
  document.getElementById('go').addEventListener('click', () => { window.tries++;
    document.getElementById('alert').textContent = 'REJECT'; });
  document.getElementById('forgot').addEventListener('click', e => { e.preventDefault(); window.forgot++; });
</script></body></html>""".replace("REJECT", REJECT)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    pg = context.new_page()
    yield pg
    context.close()


def assistant_for(adapter=None, monkeypatch=None):
    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a.values = safety.AgentValues()
    a._profile = None
    a.adapter = lambda _page: adapter or SiteAdapter()
    if monkeypatch is not None:
        monkeypatch.setattr(JobApplicationAssistant, "_read_ats_password", staticmethod(lambda: "s3cret-ATS"))
    return a


def serve(page, body=REJECTING_SIGN_IN, host=HOST):
    page.route(f"https://{host}/**", lambda route: route.fulfill(status=200, content_type="text/html", body=body))
    page.goto(f"https://{host}/en-US/careers/login")


def tries(page):
    return page.evaluate("window.tries")


def test_a_rejected_sign_in_is_not_tried_again_by_the_next_call_or_the_next_run(page, monkeypatch):
    serve(page)
    a = assistant_for(monkeypatch=monkeypatch)
    assert a.attempt_auto_login(page, EMAIL, "s3cret-ATS", create_if_missing=False) is False
    assert tries(page) == 1
    assert a.attempt_auto_login(page, EMAIL, "s3cret-ATS", create_if_missing=False) is False
    assert tries(page) == 1                                                   # nothing was submitted
    assert "Continue" in a._login_paused
    b = assistant_for(monkeypatch=monkeypatch)                                # a new run: a new object
    assert b.attempt_auto_login(page, EMAIL, "s3cret-ATS", create_if_missing=False) is False
    assert tries(page) == 1


def test_after_the_owner_resumes_one_more_try_is_allowed_and_then_the_day_limit_holds(page, monkeypatch):
    serve(page)
    a = assistant_for(monkeypatch=monkeypatch)
    a.attempt_auto_login(page, EMAIL, "s3cret-ATS", create_if_missing=False)
    login_guard.owner_resumed(HOST)
    a.attempt_auto_login(page, EMAIL, "s3cret-ATS", create_if_missing=False)
    assert tries(page) == 2
    login_guard.owner_resumed(HOST)                                           # pressing Continue again ...
    a.attempt_auto_login(page, EMAIL, "s3cret-ATS", create_if_missing=False)
    assert tries(page) == 2                                                   # ... cannot spend a third
    assert "24 hours" in a._login_paused


def test_a_sign_in_that_works_is_recorded_as_such(page, monkeypatch):
    ok = REJECTING_SIGN_IN.replace("window.tries++;", "window.tries++; document.body.innerHTML = '<h1>Candidate Home</h1>'; return;")
    serve(page, body=ok)
    a = assistant_for(monkeypatch=monkeypatch)
    assert a.attempt_auto_login(page, EMAIL, "s3cret-ATS", create_if_missing=False) is True
    assert login_guard.may_sign_in(HOST, EMAIL) is None


CREATE = """<html><body><h1>Create Account</h1>
<label for="email">Email Address*</label><input id="email" type="email">
<label for="pw">Password*</label><input id="pw" type="password">
<label for="pw2">Verify New Password*</label><input id="pw2" type="password">
<input type="checkbox" id="agree"><label for="agree">I agree to creating this account to apply for positions</label>
<button id="create">Create Account</button>
<script>window.creates = 0; document.getElementById('create').addEventListener('click', () => { window.creates++;
  document.body.innerHTML = '<h2>Sign In</h2><form data-automation-id="signInForm"><input type="email">' +
    '<input type="password"><button>Sign In</button></form>'; });</script></body></html>"""


def test_landing_on_sign_in_after_creating_an_account_signs_in_once_then_holds(page, monkeypatch):
    """The owner's decision (28 September): sign in after creating. This sign-in form never lets
    anyone in, so the one attempt is counted and the next waits for the owner."""
    a = assistant_for(adapter=WorkdayAdapter(), monkeypatch=monkeypatch)
    serve(page, body=CREATE)
    assert a.fill_create_account_form(page, EMAIL) is False
    why = login_guard.may_sign_in(HOST, EMAIL)
    assert why and "verif" in why and "Continue" in why


def test_an_account_is_not_created_again_and_again(page, monkeypatch):
    a = assistant_for(adapter=WorkdayAdapter(), monkeypatch=monkeypatch)
    for _ in range(login_guard.MAX_ACCOUNT_CREATIONS):
        login_guard.record_account_attempt(HOST, EMAIL)
    serve(page, body=CREATE)
    assert a.fill_create_account_form(page, EMAIL) is False
    assert page.evaluate("window.creates") == 0


def test_the_agent_never_asks_workday_for_a_password_reset(page, monkeypatch):
    """Workday resets by a link the agent cannot follow, that lasts two hours, five requests a day."""
    serve(page)
    a = assistant_for(adapter=WorkdayAdapter(), monkeypatch=monkeypatch)
    assert a._reset_password_with_emailed_code(page, EMAIL, "s3cret-ATS") is False
    assert page.evaluate("window.forgot") == 0
    assert "link" in a._login_paused


def test_a_reset_is_asked_for_once_a_day_elsewhere(page, monkeypatch):
    serve(page)
    a = assistant_for(monkeypatch=monkeypatch)
    login_guard.record_reset_request(HOST, EMAIL)
    assert a._reset_password_with_emailed_code(page, EMAIL, "s3cret-ATS") is False
    assert page.evaluate("window.forgot") == 0


def a_paused_agent():
    import config
    import page_agent
    login_guard.record_sign_in(HOST, EMAIL, ok=False)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    agent = page_agent.PageAgent(assistant, SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email=""),
                                 config.UserProfile(email=EMAIL), SimpleNamespace(raw_text="x"),
                                 SimpleNamespace(title="t", company="c", url=f"https://{HOST}/x"), resume_file=None)
    agent._current_host = HOST
    return agent


def test_pressing_continue_in_the_agent_lets_it_try_again():
    agent = a_paused_agent()
    agent.forget_sign_in_attempts()
    assert login_guard.may_sign_in(HOST, EMAIL) is None


@pytest.mark.parametrize("decision", ["reload_code", "refresh", "reupload_resume", "fill_education"])
def test_only_the_owners_continue_lifts_a_hold_not_a_reload_or_a_refresh(decision):
    """A code reload or a refresh says nothing about whether the owner looked at the account."""
    agent = a_paused_agent()
    agent.forget_sign_in_attempts(owner_acted=(decision == "continue"))
    assert login_guard.may_sign_in(HOST, EMAIL)                 # still held
    agent.forget_sign_in_attempts(owner_acted=True)             # the owner's Continue
    assert login_guard.may_sign_in(HOST, EMAIL) is None


def test_apply_flow_hands_the_owners_decision_to_the_agent():
    """Both resume points pass the decision on, and only Continue counts as the owner's action."""
    from pathlib import Path
    source = (Path(__file__).parents[1] / "apply_flow.py").read_text(encoding="utf-8")
    assert source.count('forget_sign_in_attempts(owner_acted=(decision == "continue"))') == 2
    assert "forget_sign_in_attempts()" not in source
    # Resume is a new run (review, 1 October), so no reload path forgives the sign-in attempts: a hold on the
    # account is kept in login_guard's file and only the owner's Continue lifts it.
    assert "forget_sign_in_attempts(owner_acted=False)" not in source
