"""Credential-only account actions must not be mistaken for sending an application."""
import pytest
from submission_guard import SubmissionGuardV0


@pytest.fixture(scope='module')
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    p = context.new_page()
    p.route('https://tenant.wd3.myworkdayjobs.com/**', lambda route: route.fulfill(body='<html></html>', content_type='text/html'))
    p.goto('https://tenant.wd3.myworkdayjobs.com/en-US/Careers/login')
    yield p
    context.close()


def install(page, extra='', label='Sign In', marker='signInFormo', password_count=1):
    page.set_content(f'''<h1>Sign In</h1><form data-automation-id="{marker}"
      onsubmit="window.accountSent=(window.accountSent||0)+1;return false">
      <label>Email Address<input type="email" data-automation-id="email"></label>
      {''.join('<label>Password<input type="password"></label>' for _ in range(password_count))}
      {extra}<button type="submit" data-automation-id="signInSubmitButton">{label}</button></form>''')
    guard = SubmissionGuardV0(page.context)
    return guard


@pytest.mark.parametrize('method', ['click', 'enter', 'requestSubmit'])
def test_verified_workday_sign_in_form_reaches_account_handler(page, method):
    guard = install(page)
    page.get_by_label('Email Address').fill('owner@example.com')
    page.get_by_label('Password').fill('synthetic-password')
    if method == 'click':
        page.get_by_role('button', name='Sign In', exact=True).click()
    elif method == 'enter':
        page.get_by_label('Password').press('Enter')
    else:
        page.evaluate('document.querySelector("form").requestSubmit()')
    assert page.evaluate('window.accountSent') == 1
    assert guard.denials(page) == []


@pytest.mark.parametrize('extra', [
    '<label>First Name<input name="firstName"></label>',
    '<input type="hidden" name="applicantId" value="draft">',
    '<label>I certify my answers<input type="checkbox"></label>',
    '<input type="file">',
    '<textarea name="answer"></textarea>',
    '<button type="submit">Submit Application</button>',
    '<button type="button" aria-label="Submit Application">Show</button>',
    '<div role="textbox">An application answer</div>',
])
def test_mixed_or_uncertain_account_form_stays_guarded(page, extra):
    guard = install(page, extra=extra)
    page.get_by_role('button', name='Sign In', exact=True).click()
    assert page.evaluate('window.accountSent') is None
    assert guard.denials(page)


@pytest.mark.parametrize('label,marker,password_count', [
    ('Submit Application', 'signInForm', 1),
    ('Sign In', 'applicationForm', 1),
    ('Sign In', 'signInForm', 0),
    ('Sign In', 'signInForm', 2),
])
def test_labels_or_partial_account_evidence_cannot_exempt_submission(page, label, marker, password_count):
    guard = install(page, label=label, marker=marker, password_count=password_count)
    page.get_by_role('button', name=label, exact=True).click()
    assert page.evaluate('window.accountSent') is None
    assert guard.denials(page)


def test_matching_form_on_unrecognized_host_stays_guarded(page):
    page.unroute_all()
    page.route('https://jobs.example.com/**', lambda route: route.fulfill(body='<html></html>', content_type='text/html'))
    page.goto('https://jobs.example.com/login')
    guard = install(page)
    page.get_by_role('button', name='Sign In', exact=True).click()
    assert page.evaluate('window.accountSent') is None
    assert guard.denials(page)


def test_sign_in_remains_scoped_when_an_application_form_is_also_present(page):
    guard = install(page, extra='<input type="hidden" name="_csrf" value="synthetic-csrf">')
    page.evaluate('''() => {
      const other = document.createElement('form');
      other.innerHTML = '<input type="password"><button type="submit">Submit Application</button>';
      other.onsubmit = () => { window.applicationSent = true; return false; };
      document.body.append(other);
    }''')
    page.get_by_role('button', name='Sign In', exact=True).click()
    assert page.evaluate('window.accountSent') == 1
    page.get_by_role('button', name='Submit Application', exact=True).click()
    assert page.evaluate('window.applicationSent') is None
    assert guard.denials(page)


def test_credential_only_workday_registration_reaches_account_handler(page):
    page.set_content('''<form data-automation-id="signInFormo"
      onsubmit="window.accountSent=true;return false">
      <input type="text" data-automation-id="email"><input type="password">
      <input type="password"><button type="submit" data-automation-id="createAccountSubmitButton">Create Account</button>
      </form>''')
    guard = SubmissionGuardV0(page.context)
    page.get_by_role('button', name='Create Account').click()
    assert page.evaluate('window.accountSent') is True
    assert guard.denials(page) == []


def test_real_auto_login_advances_through_guarded_sign_in(page, monkeypatch):
    from browser_automation import JobApplicationAssistant
    import login_guard
    guard = install(page)
    page.locator('form').evaluate('''form => form.onsubmit = () => {
      window.credentialsMatch = form.querySelector('input[type=email]').value === 'owner@example.com'
        && form.querySelector('input[type=password]').value === 'synthetic-password';
      document.body.innerHTML = '<h1>My Information</h1><form><input name="firstName">'
        + '<button type="submit">Submit Application</button></form>';
      document.querySelector('form').onsubmit = () => { window.applicationSent = true; return false; };
      return false;
    }''')
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.account_on_record = lambda: True
    assistant._after_login_attempt = lambda p, email, create: p.get_by_role('heading', name='My Information').count() == 1
    monkeypatch.setattr(login_guard, 'may_sign_in', lambda *args: '')
    assert assistant.attempt_auto_login(page, 'owner@example.com', 'synthetic-password')
    assert page.evaluate('window.credentialsMatch') is True
    assert guard.denials(page) == []
    page.get_by_role('button', name='Submit Application').click()
    assert page.evaluate('window.applicationSent') is None
    assert guard.denials(page)


def test_detached_clicked_node_keeps_guard_evidence_from_its_frame(page):
    from browser_automation import JobApplicationAssistant
    guard = install(page)
    page.locator('form').evaluate('form => form.onsubmit = () => { form.remove(); return false; }')
    assert JobApplicationAssistant._click_resiliently(page.get_by_role('button', name='Sign In'))
    assert guard.denials(page) == []


def test_changed_document_cannot_erase_a_click_observation(page):
    install(page)
    button = page.get_by_role('button', name='Sign In')
    before = SubmissionGuardV0.click_denial_count(button)
    page.goto('https://tenant.wd3.myworkdayjobs.com/en-US/Careers/other')
    assert SubmissionGuardV0.click_was_denied(button, before)


def test_denied_account_click_is_not_retried_through_enter(page, monkeypatch):
    from browser_automation import JobApplicationAssistant
    import login_guard
    guard = install(page, marker='applicationForm')
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.account_on_record = lambda: True
    monkeypatch.setattr(login_guard, 'may_sign_in', lambda *args: '')
    assert not assistant.attempt_auto_login(page, 'owner@example.com', 'synthetic-password')
    assert page.evaluate('window.accountSent') is None
    assert [x['kind'] for x in guard.denials(page)] == ['click']


def test_guard_handoff_names_the_blocked_account_action(page, tmp_path):
    from types import SimpleNamespace
    import config
    from browser_automation import JobApplicationAssistant
    from page_agent import PageAgent, PagePlan, parse_snapshot
    guard = install(page, marker='applicationForm')
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant._submission_guard = guard
    assistant.protect_submission = lambda p: None
    agent = PageAgent(assistant, SimpleNamespace(), SimpleNamespace(), config.UserProfile(),
                      SimpleNamespace(raw_text=''), SimpleNamespace(url=page.url),
                      resume_file=None, job_dir=tmp_path)
    controls = parse_snapshot(agent.snapshot(page))
    sign_in = next(c for c in controls if c.name == 'Sign In' and c.role == 'button')
    plan = PagePlan(page_kind='account_gate', next_kind='next_step', next_label='Sign In', next_ref=sign_in.ref)
    status, _, reason = agent.press_next(page, plan, controls)
    assert status == 'stop'
    assert "blocked 'Sign In'" in reason
    assert 'current step' in reason
    assert 'final submission' not in reason


def test_sign_in_dialog_does_not_register_when_account_cache_is_empty(page):
    from browser_automation import JobApplicationAssistant
    from sites.workday import WorkdayAdapter
    page.set_content('''<h1>Create Account</h1><form><input type=password><input type=password>
      <button>Create Account</button></form><div role=dialog><h2>Sign In</h2>
      <form data-automation-id=signInFormo><input type=email><input type=password>
      <button>Sign In</button></form></div>''')
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.adapter = lambda p: WorkdayAdapter()
    assistant._read_ats_password = lambda: 'synthetic-password'
    assistant._account_exists_message = lambda p: False
    assistant.account_on_record = lambda: False
    assistant._goto_login_page = lambda p: False
    calls = []
    assistant.attempt_auto_login = lambda *args, **kwargs: calls.append(kwargs) or True
    assistant.fill_create_account_form = lambda *args: pytest.fail('background registration was selected')
    assert assistant.handle_auth_gate(page, 'owner@example.com')
    assert len(calls) == 1 and calls[0]['create_if_missing'] is False
