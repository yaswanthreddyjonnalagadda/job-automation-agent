"""
Regression tests for Candidate Profile Creation Gateway and iCIMS Step Finality (f198).

Covers:
1. SubmissionGuardV0.click_candidate_profile authorises profile creation on ATS profile builder steps
   (e.g. iCIMS) containing name, login, email, password, and file upload controls.
2. Rejection of candidate profile clicks when passwords mismatch or email is missing.
3. Rejection of candidate profile clicks when final application submit controls are present.
4. Assistant.fill_create_account_form uses click_candidate_profile fallback when form contains profile fields.
5. Assistant.submission_step_finality recognizes vetted iCIMS step markers:
   "Step N of M. <Step Name>(Current Step)" -> NON_FINAL when N < M, FINAL when N >= M.
"""
from types import SimpleNamespace
import pytest

import safety
from browser_automation import JobApplicationAssistant
from sites.icims import IcimsAdapter
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


ICIMS_PROFILE_FORM = """
<html><body>
<h2>Build your profile</h2>
<form onsubmit="window.created = (window.created || 0) + 1; return false">
  <input type="file" aria-label="Resume">
  <label>First Name<input id="first" value="Yaswanth"></label>
  <label>Last Name<input id="last" value="Jonnalagadda"></label>
  <label>Email<input type="email" id="email" value="applicant@example.com"></label>
  <label>Login<input id="login" value="applicant@example.com"></label>
  <label>Password<input type="password" id="pw" value="Synthetic-Password1!"></label>
  <label>Password (Re-enter)<input type="password" id="pw2" value="Synthetic-Password1!"></label>
  <button type="submit">Submit Profile</button>
</form>
</body></html>
"""


def serve_profile(page, body=ICIMS_PROFILE_FORM):
    def handle_route(route):
        if "step2" in route.request.url:
            route.fulfill(
                content_type="text/html",
                body='<html><body><h2>Candidate Profile</h2><p>Step 2 of 3</p></body></html>',
            )
        else:
            route.fulfill(content_type="text/html", body=body)
    page.route("https://jobs-tenant.icims.com/**", handle_route)
    page.goto("https://jobs-tenant.icims.com/jobs/1/candidate?from=profilebuilder")


def test_candidate_profile_gateway_authorizes_icims_profile_form(page):
    serve_profile(page)
    guard = SubmissionGuardV0(page.context)
    button = page.get_by_role("button", name="Submit Profile")

    # Regular un-authorized click must be blocked by submission guard
    button.click()
    assert page.evaluate("window.created") is None
    assert guard.denial_count(page) == 1

    # Dedicated account creation gateway rejects mixed profile form as expected
    assert not guard.click_account_creation(page, button)
    assert page.evaluate("window.created") is None

    # Candidate profile gateway authorises it cleanly
    assert guard.click_candidate_profile(page, button)
    assert page.evaluate("window.created") == 1


def test_candidate_profile_gateway_authorizes_input_submit_control(page):
    serve_profile(page, ICIMS_PROFILE_FORM.replace(
        '<button type="submit">Submit Profile</button>',
        '<input type="submit" value="Submit Profile">'
    ))
    guard = SubmissionGuardV0(page.context)
    button = page.locator('input[type="submit"]')
    assert guard.click_candidate_profile(page, button)
    assert page.evaluate("window.created") == 1


def test_candidate_profile_gateway_authorizes_update_profile(page):
    serve_profile(page, ICIMS_PROFILE_FORM.replace(
        '<button type="submit">Submit Profile</button>',
        '<input type="submit" value="Update Profile">'
    ))
    guard = SubmissionGuardV0(page.context)
    assert guard.click_candidate_profile(page, page.locator('input[type="submit"]'))
    assert page.evaluate("window.created") == 1


def test_candidate_profile_gateway_rejects_mismatched_passwords(page):
    serve_profile(page, ICIMS_PROFILE_FORM.replace('value="Synthetic-Password1!"', 'value="DiffPass1!"', 1))
    guard = SubmissionGuardV0(page.context)
    button = page.get_by_role("button", name="Submit Profile")
    assert not guard.click_candidate_profile(page, button)
    assert page.evaluate("window.created") is None


def test_candidate_profile_gateway_rejects_missing_email(page):
    serve_profile(page, ICIMS_PROFILE_FORM.replace('value="applicant@example.com"', 'value=""', 1))
    guard = SubmissionGuardV0(page.context)
    button = page.get_by_role("button", name="Submit Profile")
    assert not guard.click_candidate_profile(page, button)
    assert page.evaluate("window.created") is None


def test_candidate_profile_gateway_rejects_form_with_final_submit(page):
    serve_profile(page, ICIMS_PROFILE_FORM.replace(
        '</form>', '<button type="submit">Submit Application</button></form>'))
    guard = SubmissionGuardV0(page.context)
    button = page.get_by_role("button", name="Submit Profile")
    assert not guard.click_candidate_profile(page, button)
    assert page.evaluate("window.created") is None


def test_fill_create_account_form_succeeds_on_icims_profile(page, monkeypatch):
    form_with_transition = ICIMS_PROFILE_FORM.replace(
        'return false',
        "window.created = 1; location.href = '/jobs/1/candidate/step2'; return false"
    )
    serve_profile(page, form_with_transition)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    assistant._profile = None
    assistant.adapter = lambda _: IcimsAdapter()
    assistant.detect_form_fields = lambda _: []
    assistant._submission_guard = SubmissionGuardV0(page.context)
    monkeypatch.setattr(assistant, "_read_ats_password", lambda: "Synthetic-Password1!")

    assert assistant.fill_create_account_form(page, "applicant@example.com")
    assert page.url.endswith('/candidate/step2')


ICIMS_STEP_1 = """
<ul>
  <li role="listitem">Step 1 of 3. Basic Information(Current Step)</li>
  <li role="listitem">Step 2 of 3. Candidate Profile(Incomplete Step)</li>
  <li role="listitem">Step 3 of 3. EEO(Incomplete Step)</li>
</ul>
"""

ICIMS_STEP_2 = """
<ul>
  <li role="listitem">Step 1 of 3. Basic Information(Completed Step)</li>
  <li role="listitem">Step 2 of 3. Candidate Profile(Current Step)</li>
  <li role="listitem">Step 3 of 3. EEO(Incomplete Step)</li>
</ul>
"""

ICIMS_STEP_3 = """
<ul>
  <li role="listitem">Step 1 of 3. Basic Information(Completed Step)</li>
  <li role="listitem">Step 2 of 3. Candidate Profile(Completed Step)</li>
  <li role="listitem">Step 3 of 3. EEO(Current Step)</li>
</ul>
"""


def test_icims_step_finality_classification(page):
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)

    # Step 1 of 3
    page.set_content(ICIMS_STEP_1)
    assert assistant.submission_step_finality(page) == "NON_FINAL"

    # Step 2 of 3
    page.set_content(ICIMS_STEP_2)
    assert assistant.submission_step_finality(page) == "NON_FINAL"

    # Step 3 of 3 (final step)
    page.set_content(ICIMS_STEP_3)
    assert assistant.submission_step_finality(page) == "FINAL"

