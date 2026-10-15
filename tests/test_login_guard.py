"""The owner's Workday accounts were being locked: every run, restart and Continue tried the sign-in again.

Researched 25 September 2026: a Workday external candidate account is locked by a few wrong passwords
(NVIDIA's support: 5 in a row, 30 minutes; other tenants 3); the sign-in page says the same thing for
a wrong password, no account, an unverified account and a locked one; "Forgot your password" mails a
link (not a code) valid 2 hours, 5 requests in 24 hours. The agent forgot every attempt when its run
ended, so nothing stopped the next run from spending another one.

The class: a limited resource (sign-in attempts) spent without a memory of what was spent.

The owner removed the limits on 12 October 2026: daily caps on wrong sign-ins, account creations, password
resets and emailed-code reads, and the hold after a rejected sign-in. The record is now diagnostic only. These tests
say so: nothing it holds ever stops an attempt, it is still written (bounded, per site and per account), and a hold left
by an older version is cleared and never blocks.
"""
import json
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


# --- the memory is diagnostic only -----------------------------------------------------------------

def entry(host=HOST, email=EMAIL):
    return login_guard._entry(login_guard._load(), host, email)


def test_a_first_sign_in_is_allowed():
    assert login_guard.may_sign_in(HOST, EMAIL) is None


def test_a_rejected_sign_in_is_recorded_and_never_holds_the_next():
    login_guard.record_sign_in(HOST, EMAIL, ok=False)
    assert len(entry()["failed_sign_ins"]) == 1
    assert "hold" not in entry()
    assert login_guard.may_sign_in(HOST, EMAIL) is None
    assert login_guard.refused_before(HOST, EMAIL) is False


def test_no_number_of_rejections_stops_the_next_sign_in():
    for _ in range(12):
        login_guard.record_sign_in(HOST, EMAIL, ok=False)
    assert login_guard.may_sign_in(HOST, EMAIL) is None


def test_account_creation_resets_and_code_reads_are_never_limited():
    for _ in range(12):
        login_guard.record_account_attempt(HOST, EMAIL)
        login_guard.record_reset_request(HOST, EMAIL)
        login_guard.record_code_read(HOST, EMAIL)
    assert login_guard.may_create_account(HOST, EMAIL) is None
    assert login_guard.may_request_reset(HOST, EMAIL) is None
    assert login_guard.may_read_code(HOST, EMAIL) is None


def test_the_history_is_still_written_and_kept_small():
    for _ in range(30):
        login_guard.record_sign_in(HOST, EMAIL, ok=False)
        login_guard.record_account_attempt(HOST, EMAIL)
        login_guard.record_reset_request(HOST, EMAIL)
        login_guard.record_code_read(HOST, EMAIL)
    kept = entry()
    assert 1 <= len(kept["failed_sign_ins"]) <= 10 and 1 <= len(kept["creations"]) <= 10
    assert 1 <= len(kept["reset_requests"]) <= 10 and 1 <= len(kept["code_reads"]) <= 20


def test_a_sign_in_that_worked_clears_the_recorded_failures():
    login_guard.record_sign_in(HOST, EMAIL, ok=False)
    login_guard.record_sign_in(HOST, EMAIL, ok=True)
    assert "failed_sign_ins" not in entry()
    assert login_guard.may_sign_in(HOST, EMAIL) is None


def test_each_site_and_each_email_has_its_own_history():
    login_guard.record_sign_in(HOST, EMAIL, ok=False)
    assert not entry("other.wd1.myworkdayjobs.com", EMAIL).get("failed_sign_ins")          # another tenant
    assert not entry(HOST, "someone.else@example.com").get("failed_sign_ins")              # another account
    assert len(entry(HOST.upper(), EMAIL.upper())["failed_sign_ins"]) == 1                 # spelling is not a difference


def test_a_hold_an_older_version_left_behind_never_blocks_and_is_cleared():
    login_guard.LOGIN_ATTEMPTS_FILE.write_text(json.dumps(
        {login_guard._key(HOST, EMAIL): {"hold": "rejected", "hold_reason": "left by an older version"}}),
        encoding="utf-8")
    assert login_guard.may_sign_in(HOST, EMAIL) is None
    assert login_guard.refused_before(HOST, EMAIL) is False
    login_guard.owner_resumed(HOST)
    assert "hold" not in entry()


@pytest.mark.parametrize("content", ["", "not json", "[1, 2]", '{"a": '])
def test_a_damaged_record_never_stops_a_run(content):
    login_guard.LOGIN_ATTEMPTS_FILE.write_text(content, encoding="utf-8")
    assert login_guard.may_sign_in(HOST, EMAIL) is None
    login_guard.record_sign_in(HOST, EMAIL, ok=False)               # and is replaced
    assert login_guard.may_sign_in(HOST, EMAIL) is None
    assert len(entry()["failed_sign_ins"]) == 1


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


def test_a_rejected_sign_in_is_recorded_and_the_next_call_and_the_next_run_may_try_again(page, monkeypatch):
    serve(page)
    a = assistant_for(monkeypatch=monkeypatch)
    assert a.attempt_auto_login(page, EMAIL, "s3cret-ATS", create_if_missing=False) is False
    assert tries(page) == 1
    assert a.attempt_auto_login(page, EMAIL, "s3cret-ATS", create_if_missing=False) is False
    assert tries(page) == 2                                                   # nothing held the second back
    assert not login_guard.refused_before(HOST, EMAIL)
    b = assistant_for(monkeypatch=monkeypatch)                                # a new run: a new object
    assert b.attempt_auto_login(page, EMAIL, "s3cret-ATS", create_if_missing=False) is False
    assert tries(page) == 3
    assert len(entry()["failed_sign_ins"]) == 3                               # and each was recorded


def test_pressing_continue_is_not_needed_for_another_try(page, monkeypatch):
    serve(page)
    a = assistant_for(monkeypatch=monkeypatch)
    a.attempt_auto_login(page, EMAIL, "s3cret-ATS", create_if_missing=False)
    a.attempt_auto_login(page, EMAIL, "s3cret-ATS", create_if_missing=False)
    login_guard.owner_resumed(HOST)
    a.attempt_auto_login(page, EMAIL, "s3cret-ATS", create_if_missing=False)
    assert tries(page) == 3                                                   # no day limit either


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


def test_landing_on_sign_in_after_creating_an_account_signs_in_once_and_records_it(page, monkeypatch):
    """The owner's decision (28 September): sign in after creating. This sign-in form never lets anyone in, so the
    one attempt is recorded -- and, since 12 October, nothing is held."""
    a = assistant_for(adapter=WorkdayAdapter(), monkeypatch=monkeypatch)
    serve(page, body=CREATE)
    assert a.fill_create_account_form(page, EMAIL) is False
    assert len(entry().get("failed_sign_ins", [])) == 1
    assert login_guard.may_sign_in(HOST, EMAIL) is None


def test_an_account_already_tried_many_times_is_still_created(page, monkeypatch):
    a = assistant_for(adapter=WorkdayAdapter(), monkeypatch=monkeypatch)
    for _ in range(12):
        login_guard.record_account_attempt(HOST, EMAIL)
    serve(page, body=CREATE.replace("window.creates++;", "window.creates++; window.name = 'created';"))
    a.fill_create_account_form(page, EMAIL)
    assert page.evaluate("window.name") == "created"                          # the count never stops it (window.name
                                                                              # survives the reload the sign-in causes)


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


def an_agent_after_an_older_versions_hold():
    import config
    import page_agent
    login_guard.LOGIN_ATTEMPTS_FILE.write_text(json.dumps(
        {login_guard._key(HOST, EMAIL): {"hold": "rejected", "hold_reason": "left by an older version"}}),
        encoding="utf-8")
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    agent = page_agent.PageAgent(assistant, SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email=""),
                                 config.UserProfile(email=EMAIL), SimpleNamespace(raw_text="x"),
                                 SimpleNamespace(title="t", company="c", url=f"https://{HOST}/x"), resume_file=None)
    agent._current_host = HOST
    return agent


def test_pressing_continue_in_the_agent_clears_what_an_older_version_held():
    agent = an_agent_after_an_older_versions_hold()
    agent.forget_sign_in_attempts()
    assert login_guard.may_sign_in(HOST, EMAIL) is None
    assert "hold" not in entry()


@pytest.mark.parametrize("decision", ["reload_code", "refresh", "reupload_resume", "fill_education"])
def test_whatever_the_owner_decides_nothing_an_older_version_held_ever_blocks(decision):
    """A hold is no longer a gate, so a reload or a refresh has nothing to leave in place."""
    agent = an_agent_after_an_older_versions_hold()
    agent.forget_sign_in_attempts(owner_acted=(decision == "continue"))
    assert login_guard.may_sign_in(HOST, EMAIL) is None


def test_apply_flow_hands_the_owners_decision_to_the_agent():
    """Both resume points pass the decision on, and only Continue counts as the owner's action."""
    from pathlib import Path
    source = (Path(__file__).parents[1] / "apply_flow.py").read_text(encoding="utf-8")
    assert source.count('forget_sign_in_attempts(owner_acted=(decision == "continue"))') == 2
    assert "forget_sign_in_attempts(owner_acted=False)" in source
    assert "forget_sign_in_attempts()" not in source
