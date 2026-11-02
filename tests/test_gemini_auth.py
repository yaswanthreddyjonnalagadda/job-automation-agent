"""Gemini may select login controls, but never supplies credentials or authority."""
from types import SimpleNamespace

import pytest

import gemini_auth
from browser_automation import JobApplicationAssistant
from submission_guard import SubmissionGuardV0
from test_account_step import browser, page, serve
from test_gemini_model_ladder import google, config, Reply, PER_MINUTE, models, ok

EMAIL = 'candidate@example.com'
PASSWORD = 'Synthetic#987az'
FORM = '''<form onsubmit="window.loginCount=(window.loginCount||0)+1;return false">
<h2>Sign In</h2><label>Email<input type=email></label>
<label>Password<input type=password></label><button type=submit>Sign In</button></form>'''


def assistant(page):
    a = object.__new__(JobApplicationAssistant)
    a._config = SimpleNamespace(gemini_api_key='test-key')
    a._submission_guard = SubmissionGuardV0(page.context)
    a._after_login_attempt = lambda *args: False
    return a


def plan(monkeypatch, callback=None):
    calls = []
    def ask(config, payload):
        calls.append(payload)
        if callback:
            return callback(payload)
        return {'email': 0, 'password': 1, 'submit': 2}
    monkeypatch.setattr(gemini_auth, '_ask', ask)
    return calls


def test_runtime_fallback_submits_once_and_checks_outcome(page, monkeypatch):
    serve(page, FORM)
    a = assistant(page)
    a._find_login_email_input = lambda root: None
    a._email_already_given = lambda *args: False
    calls = plan(monkeypatch)
    outcomes = []
    a._after_login_attempt = lambda *args: outcomes.append(args) or False
    monkeypatch.setattr(page, 'wait_for_timeout', lambda *args: None)
    assert not a.attempt_auto_login(page, EMAIL, PASSWORD, create_if_missing=False)
    assert page.evaluate('window.loginCount') == 1
    assert len(calls) == len(outcomes) == 1
    assert page.locator('input[type=password]:visible').input_value() == PASSWORD
    assert PASSWORD not in str(calls) and EMAIL not in str(calls)
    assert not gemini_auth.sign_in(a, page, page, EMAIL, PASSWORD, False)
    assert len(calls) == 1


def test_invalid_model_actions_do_nothing(page, monkeypatch):
    # All malformed replies exercise the same untouched page; no need to launch
    # six separate browser contexts for this boundary.
    serve(page, FORM)
    a = assistant(page)
    for reply in [None, {}, {'email': -1, 'password': 1, 'submit': 2},
                  {'email': True, 'password': 1, 'submit': 2},
                  {'email': 0, 'password': 1, 'submit': 99},
                  {'email': 0, 'password': 1, 'submit': 2, 'password_value': 'invented'}]:
        plan(monkeypatch, lambda _, reply=reply: reply)
        a.__dict__.pop('_gemini_sign_in_tried', None)
        assert not gemini_auth.sign_in(a, page, page, EMAIL, PASSWORD, False)
        assert page.evaluate('window.loginCount') is None
        assert page.locator('input[type=password]').input_value() == ''


@pytest.mark.parametrize('change', ['captcha', 'blocked', 'held', 'rejected', 'no_key', 'no_guard'])
def test_policy_blocks_before_model(page, monkeypatch, change):
    serve(page, FORM)
    a = assistant(page)
    calls = plan(monkeypatch)
    if change == 'captcha':
        monkeypatch.setattr(gemini_auth.safety, 'captcha_visible', lambda _: True)
    elif change == 'blocked':
        monkeypatch.setattr(gemini_auth.safety, 'password_allowed', lambda _: False)
    elif change == 'held':
        monkeypatch.setattr(gemini_auth.login_guard, 'may_sign_in', lambda *args: 'held')
    elif change == 'rejected':
        a._last_login_rejected = True
    elif change == 'no_key':
        a._config.gemini_api_key = ''
    else:
        a._submission_guard = None
    assert not gemini_auth.sign_in(a, page, page, EMAIL, PASSWORD, False)
    assert not calls


def test_stale_page_drops_plan(page, monkeypatch):
    serve(page, FORM)
    def moved(_):
        page.set_content(FORM)
        return {'email': 0, 'password': 1, 'submit': 2}
    plan(monkeypatch, moved)
    assert not gemini_auth.sign_in(assistant(page), page, page, EMAIL, PASSWORD, False)
    assert page.evaluate('window.loginCount') is None


def test_owner_value_is_preserved(page, monkeypatch):
    serve(page, FORM)
    page.locator('input[type=email]').fill('other@example.com')
    plan(monkeypatch)
    assert not gemini_auth.sign_in(assistant(page), page, page, EMAIL, PASSWORD, False)
    assert page.locator('input[type=email]').input_value() == 'other@example.com'
    assert page.locator('input[type=password]').input_value() == ''


def test_application_submit_is_not_login_authority(page, monkeypatch):
    serve(page, FORM.replace('>Sign In</button>', '>Submit Application</button>'))
    plan(monkeypatch)
    assert not gemini_auth.sign_in(assistant(page), page, page, EMAIL, PASSWORD, False)
    assert page.evaluate('window.loginCount') is None


def test_mixed_application_form_is_not_filled(page, monkeypatch):
    serve(page, FORM.replace('</form>', '<textarea aria-label="Experience"></textarea></form>'))
    plan(monkeypatch)
    assert not gemini_auth.sign_in(assistant(page), page, page, EMAIL, PASSWORD, False)
    assert page.locator('input[type=password]').input_value() == ''


def test_labels_are_redacted_and_values_are_never_sent(page, monkeypatch):
    serve(page, FORM)
    page.locator('input[type=email]').fill(EMAIL)
    page.locator('input[type=password]').fill(PASSWORD)
    page.locator('input[type=email]').evaluate(
        '(e, label) => e.setAttribute("aria-label", label)', f'Email {EMAIL} {PASSWORD} token=private-token')
    calls = plan(monkeypatch, lambda _: {})
    assert not gemini_auth.sign_in(assistant(page), page, page, EMAIL, PASSWORD, False)
    assert len(calls) == 1
    assert all(secret not in str(calls) for secret in (EMAIL, PASSWORD, 'private-token'))
    assert all(set(item) == {'tag', 'type', 'label'} for item in calls[0])


def test_failed_dispatch_never_falls_back(page, monkeypatch):
    serve(page, FORM)
    a = assistant(page)
    monkeypatch.setattr(a._submission_guard, 'click_account_sign_in', lambda *args: False)
    calls = plan(monkeypatch)
    assert not a.attempt_auto_login(page, EMAIL, PASSWORD, create_if_missing=False)
    assert not calls
    assert page.evaluate('window.loginCount') is None


def test_model_error_stops_without_logging_secrets(page, monkeypatch, caplog):
    serve(page, FORM)
    def unavailable(_):
        raise RuntimeError(PASSWORD)
    plan(monkeypatch, unavailable)
    assert not gemini_auth.sign_in(assistant(page), page, page, EMAIL, PASSWORD, False)
    assert PASSWORD not in caplog.text
    assert page.locator('input[type=password]').input_value() == ''


def test_verified_browser_transition_uses_existing_outcome_recorder(page, monkeypatch):
    serve(page, FORM)
    page.locator('form').evaluate("f => f.onsubmit = () => { f.innerHTML = '<h2>Current step 1 of 5</h2><label>First Name<input></label>'; return false; }")
    a = assistant(page)
    recorded = []
    a.remember_account = lambda *args: recorded.append(args)
    a._after_login_attempt = JobApplicationAssistant._after_login_attempt.__get__(a)
    plan(monkeypatch)
    monkeypatch.setattr(page, 'wait_for_timeout', lambda *args: None)
    assert gemini_auth.sign_in(a, page, page, EMAIL, PASSWORD, False)
    assert len(recorded) == 1


def test_code_prompt_after_click_does_not_claim_authenticated(page, monkeypatch):
    serve(page, FORM)
    page.locator('form').evaluate('''f => f.onsubmit = () => {
        f.innerHTML = '<h2>Enter verification code</h2><label>Verification code<input></label><button>Verify</button>';
        return false;
    }''')
    a = assistant(page)
    a._after_login_attempt = lambda *args: pytest.fail('OTP is not authentication success')
    plan(monkeypatch)
    monkeypatch.setattr(page, 'wait_for_timeout', lambda *args: None)
    assert not gemini_auth.sign_in(a, page, page, EMAIL, PASSWORD, False)
    assert page.get_by_role('heading', name='Enter verification code').is_visible()


def test_prompt_transport_uses_gemini_quick_ladder(google):
    google.script['lite-1'] = [ok('{"email":0,"password":1,"submit":2}')]
    assert gemini_auth._ask(config(), [{'tag': 'input', 'type': 'email', 'label': 'Email'}])['email'] == 0
    assert models(google) == ['lite-1']
    assert google.sleeps == []


@pytest.mark.parametrize('status,body', [(429, PER_MINUTE), (503, '{}')])
def test_auth_model_failure_has_no_retry_sleep(google, status, body):
    google.script['lite-1'] = [Reply(status, body)]
    google.script['lite-2'] = [Reply(status, body)]
    with pytest.raises(Exception):
        gemini_auth._ask(config(gemini_quick_models=('lite-1', 'lite-2')), [])
    assert models(google) == ['lite-1', 'lite-2']
    assert google.sleeps == []


def test_hidden_password_uses_visible_form_without_ai(page, monkeypatch):
    serve(page, '<input type=password hidden>' + FORM)
    a = assistant(page)
    a._after_login_attempt = JobApplicationAssistant._after_login_attempt.__get__(a)
    a.remember_account = lambda *args: pytest.fail('visible login form is still present')
    a.sign_in_with_google_if_offered = lambda *args: False
    calls = plan(monkeypatch)
    monkeypatch.setattr(page, 'wait_for_timeout', lambda *args: None)
    assert not a.attempt_auto_login(page, EMAIL, PASSWORD, create_if_missing=False)
    assert page.evaluate('window.loginCount') == 1
    assert page.locator('input[type=password]:visible').input_value() == PASSWORD
    assert not calls


def test_disappearing_form_alone_is_not_success(page, monkeypatch):
    serve(page, FORM)
    page.locator('form').evaluate('f => f.onsubmit = () => { f.remove(); return false; }')
    a = assistant(page)
    a._after_login_attempt = lambda *args: pytest.fail('blank page is not authentication evidence')
    plan(monkeypatch)
    monkeypatch.setattr(page, 'wait_for_timeout', lambda *args: None)
    assert not gemini_auth.sign_in(a, page, page, EMAIL, PASSWORD, False)


def test_scope_keeps_background_registration_out(page, monkeypatch):
    serve(page, '<form><h2>Create Account</h2><input type=password><input type=password></form>' + FORM)
    a = assistant(page)
    calls = plan(monkeypatch)
    monkeypatch.setattr(page, 'wait_for_timeout', lambda *args: None)
    root = page.locator('form').nth(1)
    assert not gemini_auth.sign_in(a, page, root, EMAIL, PASSWORD, False)
    assert page.evaluate('window.loginCount') == 1
    assert len(calls[0]) == 3
    assert page.locator('form').first.locator('input').first.input_value() == ''
