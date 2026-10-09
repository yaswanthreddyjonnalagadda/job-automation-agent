"""Only choose an email verification channel covered by the existing mail policy."""
from types import SimpleNamespace

import pytest

import config
import page_agent
from browser_automation import JobApplicationAssistant


@pytest.fixture(scope='module')
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    page = context.new_page()
    yield page
    context.close()


def chooser(page, *, allowed=True, options=None, captcha=False, host='employer.example'):
    options = options if options is not None else ['Email: a***@example.com', 'SMS text message']
    radios = ''.join(f'<label><input type=radio name=channel>{label}</label>' for label in options)
    html = '<div role=dialog><h1>Where would you like to receive a verification code?</h1>' + radios
    html += '<button onclick="window.requests=(window.requests||0)+1">Send Code</button></div>'
    if captcha:
        html += '<p>Verify that you are a human</p>'
    page.route('**/*', lambda r: r.fulfill(content_type='text/html', body=html))
    page.goto(f'https://{host}/login')
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = page_agent.safety.AgentValues()
    agent = page_agent.PageAgent(assistant, SimpleNamespace(),
        SimpleNamespace(ats_email='applicant@example.com'),
        config.UserProfile(email='applicant@example.com', check_gmail_for_confirmation=allowed),
        {}, SimpleNamespace(title='Engineer', company='Example', url=page.url))
    return agent


def test_authorized_email_choice_is_sent_once_without_selecting_sms(page):
    agent = chooser(page)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    assert agent.sign_in_step(page, controls)
    assert page.get_by_role('radio', name='Email: a***@example.com').is_checked()
    assert not page.get_by_role('radio', name='SMS text message').is_checked()
    assert page.evaluate('window.requests') == 1
    agent.sign_in_step(page, page_agent.parse_snapshot(agent.snapshot(page)))
    assert page.evaluate('window.requests') == 1


@pytest.mark.parametrize('kwargs', [
    {'allowed':False}, {'options':['SMS text message']},
    {'options':['Email: a***@example.com','Email: b***@example.com']},
    {'captcha':True}, {'host':'accounts.google.com'},
])
def test_unapproved_ambiguous_or_unsupported_delivery_never_sends(page, kwargs):
    agent = chooser(page, **kwargs)
    assert not agent.sign_in_step(page, page_agent.parse_snapshot(agent.snapshot(page)))
    assert not page.evaluate('Boolean(window.requests)')
    assert page.locator('input:checked').count() == 0


def test_uncertain_email_request_is_not_dispatched_again(page, monkeypatch):
    agent = chooser(page)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    original = agent.locate
    attempts = []
    def fail_send(**kwargs):
        attempts.append(True)
        raise TimeoutError('Simulated uncertain request')
    def locate(tab, ref):
        control = next(c for c in controls if c.ref == ref)
        return SimpleNamespace(click=fail_send) if control.name == 'Send Code' else original(tab, ref)
    monkeypatch.setattr(agent, 'locate', locate)
    assert not agent.sign_in_step(page, controls)
    assert not agent.sign_in_step(page, controls)
    assert attempts == [True]
    assert not page.evaluate('Boolean(window.requests)')
