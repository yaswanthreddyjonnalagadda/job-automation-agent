"""Verification recovery must send a fresh link and distinguish sign-in from success."""
from types import SimpleNamespace
import pytest
import config
import safety
from browser_automation import JobApplicationAssistant

HOST = 'tenant.wd5.myworkdayjobs.com'
URL = f'https://{HOST}/en-US/Careers/login'
EMAIL = 'owner@example.com'

@pytest.fixture(scope='module')
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()

@pytest.fixture
def page(browser):
    with browser.new_context() as context:
        yield context.new_page()


def assistant():
    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a._profile = config.UserProfile(email=EMAIL, check_gmail_for_confirmation=True)
    a._config = SimpleNamespace(ats_email=EMAIL)
    a.values = safety.AgentValues()
    return a

@pytest.mark.parametrize('body, expected, outcome', [
    ('<h1>Your account has been verified</h1>', True, 'verified'),
    ('<h1>Welcome</h1><p>Invalid Token</p>', False, 'failed'),
    ('<h1>Thank you</h1><p>This link has expired</p>', False, 'failed'),
    ('<h1>Welcome to careers</h1>', False, 'unconfirmed'),
    ('<body></body>', False, 'unconfirmed'),
    ('<h1>Sign In</h1><label>Email Address<input type=email></label>'
     '<label>Password<input type=password></label><button>Sign In</button>', True, 'sign_in_required'),
])
def test_link_outcome_requires_evidence(page, body, expected, outcome):
    a = assistant()
    a.why_not_read_a_code = lambda _page: None
    initial = '<h1>Sign In</h1><p>Verify your account before you sign in or request a verification email.</p>'
    page.context.route(f'https://{HOST}/**', lambda route: route.fulfill(
        body=body if '/activate/' in route.request.url else initial))
    page.goto(URL)
    a.verification_link_from_gmail = lambda _page: f'https://{HOST}/en-US/Careers/activate/token'
    import account_state
    assert account_state.read_state(page.locator("body").aria_snapshot()).kind == account_state.VERIFY_EMAIL, page.locator("body").aria_snapshot()
    assert a.verify_account_by_email_link(page) is expected
    assert a._email_verification_outcome == outcome


@pytest.mark.parametrize("sent", [False, True])
def test_resend_precedes_mail_search_unless_already_sent(page, sent):
    page.set_content('<h1>Sign In</h1><p>Verify your account before you sign in or request a verification email.</p>'
                     + ('<p>An email has been sent to you.</p>' if sent else '')
                     + '<label>Email Address<input type=email value="owner@example.com"></label>'
                     + '<input type=password><button onclick="this.dataset.clicked=1">Resend Account Verification</button>')
    a = assistant()
    a.why_not_read_a_code = lambda _page: None
    observed = []
    def find_link(_page):
        observed.append(page.get_by_role('button').get_attribute('data-clicked'))
        return None
    a.verification_link_from_gmail = find_link
    assert not a.verify_account_by_email_link(page)
    assert observed == [None if sent else '1']


@pytest.mark.parametrize("blocked", [True, False])
def test_mail_not_opened_without_permission_or_current_requirement(page, blocked):
    page.set_content('<h1>Sign In</h1><input type=password>'
                     + ('<p>Verify your account before you sign in.</p>' if blocked else ''))
    a = assistant()
    a.why_not_read_a_code = lambda _page: 'not authorized' if blocked else None
    a.verification_link_from_gmail = lambda _page: pytest.fail('mail must remain closed')
    assert not a.verify_account_by_email_link(page)


def test_resend_preserves_different_owner_entered_email(page):
    page.set_content('<h1>Sign In</h1><p>Verify your account before you sign in.</p>'
                     '<label>Email Address<input type=email value="different@example.com"></label>'
                     '<input type=password><button onclick="this.dataset.clicked=1">Resend Account Verification</button>')
    a = assistant()
    a.why_not_read_a_code = lambda _page: None
    a.verification_link_from_gmail = lambda _page: pytest.fail('wrong account mailbox search')
    assert not a.verify_account_by_email_link(page)
    assert page.locator('input[type=email]').input_value() == 'different@example.com'
    assert page.get_by_role('button').get_attribute('data-clicked') is None
