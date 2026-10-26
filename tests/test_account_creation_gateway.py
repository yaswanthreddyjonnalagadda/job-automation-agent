"""Account creation must work with final-submission containment armed."""
from types import SimpleNamespace

import pytest

import login_guard
import safety
from browser_automation import JobApplicationAssistant
from sites.base import SiteAdapter
from sites.workday import WorkdayAdapter
from submission_guard import SubmissionGuardV0


@pytest.fixture(scope="module")
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


FORM = """<h2>Create Account</h2>
<form onsubmit="window.created=(window.created||0)+1; return false">
<label>Email Address<input type="email" value="owner@example.com"></label>
<label>Password<input type="password" value="Synthetic-Password1!"></label>
<label>Verify New Password<input type="password" value="Synthetic-Password1!"></label>
<button type="submit">Create Account</button></form>"""


def serve(page, body):
    page.route("https://tenant.wd1.myworkdayjobs.com/**", lambda route: route.fulfill(
        content_type="text/html", body=body))
    page.goto("https://tenant.wd1.myworkdayjobs.com/en-US/Careers/apply")


def test_real_account_filler_reaches_application_with_guard_armed(page, monkeypatch):
    serve(page, FORM.replace('return false', "document.body.innerHTML = "
          "'<h2 data-automation-id=progressBarActiveStep>My Information</h2><form id=application>"
          "<button>Submit Application</button></form>';"
          "document.getElementById('application').onsubmit=()=>{window.sent=true;return false};return false"))
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    assistant._profile = None
    assistant.adapter = lambda _: WorkdayAdapter()
    assistant.detect_form_fields = lambda _: []
    assistant._submission_guard = SubmissionGuardV0(page.context)
    monkeypatch.setattr(assistant, "_read_ats_password", lambda: "Synthetic-Password1!")
    assert assistant.fill_create_account_form(page, "owner@example.com")
    assert page.evaluate("window.created") == 1
    assert assistant.adapter(page).candidate_account_state(page) == "application"
    page.get_by_role("button", name="Submit Application").click()
    assert page.evaluate("window.sent") is None
    assert assistant._submission_guard.denials(page)[-1]["label"] == "Submit Application"


@pytest.mark.parametrize("extra", [
    '<input type="file">', '<textarea aria-label="Cover letter"></textarea>',
    '<input aria-label="First Name" value="Candidate">',
    '<button>Submit Application</button>',
    '<button>Done</button>',
])
def test_account_gateway_rejects_mixed_application_forms(page, extra):
    serve(page, FORM.replace('</form>', extra + '</form>'))
    guard = SubmissionGuardV0(page.context)
    assert not guard.click_account_creation(page, page.get_by_role("button", name="Create Account"))
    assert page.evaluate("window.created") is None


def test_account_gateway_requires_filled_matching_credentials(page):
    serve(page, FORM.replace('value="Synthetic-Password1!"', 'value=""', 1))
    guard = SubmissionGuardV0(page.context)
    assert not guard.click_account_creation(page, page.get_by_role("button", name="Create Account"))
    assert page.evaluate("window.created") is None


def test_sign_in_gateway_accepts_prior_read_only_username_on_password_step(page):
    serve(page, '<form onsubmit="window.signedIn=true;return false">'
          '<input type="text" name="username" autocomplete="username" '
          'value="owner@example.com" readonly style="display:none">'
          '<input type="password" name="password" autocomplete="current-password" '
          'value="Synthetic-Password1!">'
          '<button type="submit">LOG IN</button></form>')
    guard = SubmissionGuardV0(page.context)
    assert guard.click_account_sign_in(page, page.get_by_role("button", name="LOG IN"))
    assert page.evaluate("window.signedIn") is True


@pytest.mark.parametrize("username", [
    '<input type="text" name="username" autocomplete="username" '
    'value="owner@example.com" style="display:none">',
    '<input type="text" name="username" autocomplete="username" '
    'readonly style="display:none">',
])
def test_sign_in_gateway_rejects_unaccepted_prior_username(page, username):
    serve(page, '<form onsubmit="window.signedIn=true;return false">' + username
          + '<input type="password" value="Synthetic-Password1!">'
          '<button type="submit">LOG IN</button></form>')
    guard = SubmissionGuardV0(page.context)
    assert not guard.click_account_sign_in(page, page.get_by_role("button", name="LOG IN"))
    assert page.evaluate("window.signedIn") is None


def test_account_gateway_rejects_file_fields_associated_from_outside_form(page):
    serve(page, FORM.replace('<form ', '<form id="registration" ') +
          '<input type="file" form="registration" style="display:none">')
    guard = SubmissionGuardV0(page.context)
    assert not guard.click_account_creation(page, page.get_by_role("button", name="Create Account"))
    assert page.evaluate("window.created") is None


@pytest.mark.parametrize("label", ["Submit", "Submit Application", "Verify", "Finish"])
def test_account_gateway_never_authorizes_other_submit_actions(page, label):
    serve(page, FORM.replace('>Create Account</button>', '>' + label + '</button>'))
    guard = SubmissionGuardV0(page.context)
    assert not guard.click_account_creation(page, page.get_by_role("button", name=label, exact=True))
    assert page.evaluate("window.created") is None


def test_account_gateway_requires_explicit_call_and_does_not_leave_authorization(page):
    serve(page, FORM)
    guard = SubmissionGuardV0(page.context)
    button = page.get_by_role("button", name="Create Account")
    button.click()
    assert page.evaluate("window.created") is None
    assert guard.click_account_creation(page, button)
    assert page.evaluate("window.created") == 1
    button.click()
    assert page.evaluate("window.created") == 1


def test_account_gateway_handles_navigation_after_an_earlier_denial(page):
    serve(page, FORM.replace('return false', "location.href='/registered';return false"))
    page.route("**/registered", lambda route: route.fulfill(
        content_type="text/html", body='<h2>Candidate Home</h2><form onsubmit="window.sent=true;return false">'
        '<button>Submit Application</button></form>'))
    guard = SubmissionGuardV0(page.context)
    button = page.get_by_role("button", name="Create Account")
    button.click()
    assert guard.denial_count(page) == 1
    assert guard.click_account_creation(page, button)
    assert page.url.endswith('/registered')
    page.get_by_role("button", name="Submit Application").click()
    assert page.evaluate("window.sent") is None


def test_denied_creation_does_not_spend_a_creation_attempt(page, monkeypatch):
    serve(page, FORM)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    assistant._profile = None
    assistant.adapter = lambda _: SiteAdapter()
    assistant.detect_form_fields = lambda _: []
    assistant._click_resiliently = lambda *args, **kwargs: False
    assistant._submission_guard = SimpleNamespace(click_account_creation=lambda *args: False)
    monkeypatch.setattr(assistant, "_read_ats_password", lambda: "Synthetic-Password1!")
    attempts = []
    monkeypatch.setattr(login_guard, "record_account_attempt", lambda *args: attempts.append(args))
    assert not assistant.fill_create_account_form(page, "owner@example.com")
    assert attempts == []
