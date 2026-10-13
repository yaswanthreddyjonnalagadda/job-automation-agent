"""Authentication history is diagnostic only; portal and credential gates still apply."""
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


def test_a_rejected_sign_in_records_history_without_holding_the_next():
    login_guard.record_sign_in(HOST, EMAIL, ok=False)
    assert login_guard.may_sign_in(HOST, EMAIL) is None
    assert not login_guard.refused_before(HOST, EMAIL)
    entry = login_guard._load()[login_guard._key(HOST, EMAIL)]
    assert entry['failed_sign_ins']
    assert 'hold' not in entry



@pytest.mark.parametrize("gate", ["may_sign_in", "may_create_account", "may_request_reset", "may_read_code"])
def test_legacy_counts_and_holds_never_gate_authentication(gate):
    stamp = login_guard._stamp()
    login_guard._save({login_guard._key(HOST, EMAIL): {
        'failed_sign_ins': [stamp] * 20, 'creations': [stamp] * 20,
        'reset_requests': [stamp] * 20, 'code_reads': [stamp] * 20,
        'hold': 'rejected', 'hold_reason': 'Wait 24 hours then press Continue'}})
    assert getattr(login_guard, gate)(HOST, EMAIL) is None
    assert not login_guard.refused_before(HOST, EMAIL)



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


def test_diagnostic_history_is_scoped_to_account():
    login_guard.record_sign_in(HOST.upper(), EMAIL.upper(), ok=False)
    data = login_guard._load()
    assert list(data) == [login_guard._key(HOST, EMAIL)]
    assert data[login_guard._key(HOST, EMAIL)]['failed_sign_ins']



def test_repeated_creation_reset_and_mail_reads_are_diagnostic_only():
    for _ in range(10):
        login_guard.record_account_attempt(HOST, EMAIL)
        login_guard.record_reset_request(HOST, EMAIL)
        login_guard.record_code_read(HOST, EMAIL)
    for gate in (login_guard.may_create_account, login_guard.may_request_reset, login_guard.may_read_code):
        assert gate(HOST, EMAIL) is None



@pytest.mark.parametrize("content", ["", "not json", "[1, 2]", '{"a": '])
def test_a_damaged_record_never_stops_a_run(content):
    login_guard.LOGIN_ATTEMPTS_FILE.write_text(content, encoding="utf-8")
    assert login_guard.may_sign_in(HOST, EMAIL) is None
    login_guard.record_sign_in(HOST, EMAIL, ok=False)               # and is replaced
    assert login_guard.may_sign_in(HOST, EMAIL) is None


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


def test_rejections_do_not_block_a_later_sign_in_or_new_assistant(page, monkeypatch):
    serve(page)
    a = assistant_for(monkeypatch=monkeypatch)
    for expected in (1, 2, 3):
        assert not a.attempt_auto_login(page, EMAIL, 's3cret-ATS', create_if_missing=False)
        assert tries(page) == expected
    b = assistant_for(monkeypatch=monkeypatch)
    assert not b.attempt_auto_login(page, EMAIL, 's3cret-ATS', create_if_missing=False)
    assert tries(page) == 4



def test_legacy_hold_does_not_prevent_real_sign_in(page, monkeypatch):
    login_guard._save({login_guard._key(HOST, EMAIL): {'hold': 'rejected',
        'failed_sign_ins': [login_guard._stamp()] * 10}})
    serve(page)
    a = assistant_for(monkeypatch=monkeypatch)
    assert not a.attempt_auto_login(page, EMAIL, 's3cret-ATS', create_if_missing=False)
    assert tries(page) == 1



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


def test_creation_followed_by_rejection_does_not_add_persistent_hold(page, monkeypatch):
    a = assistant_for(adapter=WorkdayAdapter(), monkeypatch=monkeypatch)
    serve(page, body=CREATE)
    assert a.fill_create_account_form(page, EMAIL) is False
    assert login_guard.may_sign_in(HOST, EMAIL) is None



def test_creation_history_does_not_block_form_dispatch(page, monkeypatch):
    for _ in range(10):
        login_guard.record_account_attempt(HOST, EMAIL)
    a = assistant_for(adapter=WorkdayAdapter(), monkeypatch=monkeypatch)
    serve(page, body=CREATE.replace('<form data-automation-id=', '<form onsubmit="event.preventDefault()" data-automation-id='))
    a.fill_create_account_form(page, EMAIL)
    assert page.evaluate('window.creates') == 1
    assert page.get_by_role('heading', name='Sign In').is_visible()



def test_the_agent_never_asks_workday_for_a_password_reset(page, monkeypatch):
    """Workday resets by a link the agent cannot follow, that lasts two hours, five requests a day."""
    serve(page)
    a = assistant_for(adapter=WorkdayAdapter(), monkeypatch=monkeypatch)
    assert a._reset_password_with_emailed_code(page, EMAIL, "s3cret-ATS") is False
    assert page.evaluate("window.forgot") == 0
    assert "link" in a._login_paused


def test_reset_still_requires_authorized_mailbox_access(page, monkeypatch):
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


@pytest.mark.parametrize('decision', ['reload_code', 'refresh', 'reupload_resume', 'fill_education'])
def test_reload_and_refresh_need_no_owner_action_to_remove_a_quota(decision):
    agent = a_paused_agent()
    agent.forget_sign_in_attempts(owner_acted=False)
    assert login_guard.may_sign_in(HOST, EMAIL) is None



def test_apply_flow_hands_the_owners_decision_to_the_agent():
    """Both resume points pass the decision on, and only Continue counts as the owner's action."""
    from pathlib import Path
    source = (Path(__file__).parents[1] / "apply_flow.py").read_text(encoding="utf-8")
    assert source.count('forget_sign_in_attempts(owner_acted=(decision == "continue"))') == 2
    assert "forget_sign_in_attempts(owner_acted=False)" in source
    assert "forget_sign_in_attempts()" not in source
