"""
Regression test for f200: CAPTCHA appearing after clicking create account is detected.
"""
import pytest
from playwright.sync_api import sync_playwright

import safety
from browser_automation import JobApplicationAssistant
from sites.workday import WorkdayAdapter
from submission_guard import SubmissionGuardV0


@pytest.fixture(scope="module")
def browser():
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


FORM_TRIGGERING_CAPTCHA = """<h2>Create Account</h2>
<form onsubmit="
  const f = document.createElement('iframe');
  f.setAttribute('src', 'https://newassets.hcaptcha.com/captcha/v1/');
  f.style.width = '300px';
  f.style.height = '100px';
  document.body.appendChild(f);
  return false;
">
<label>Email Address<input type="email" value="owner@example.com"></label>
<label>Password<input type="password" value="Synthetic-Password1!"></label>
<label>Verify New Password<input type="password" value="Synthetic-Password1!"></label>
<button type="submit">Create Account</button>
</form>"""


def test_captcha_appearing_after_submit_is_detected(page, monkeypatch):
    page.route("https://tenant.wd1.myworkdayjobs.com/**", lambda route: route.fulfill(
        content_type="text/html", body=FORM_TRIGGERING_CAPTCHA))
    page.goto("https://tenant.wd1.myworkdayjobs.com/en-US/Careers/apply")

    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    assistant._profile = None
    assistant.adapter = lambda _: WorkdayAdapter()
    assistant.detect_form_fields = lambda _: []
    assistant._submission_guard = SubmissionGuardV0(page.context)
    monkeypatch.setattr(assistant, "_read_ats_password", lambda: "Synthetic-Password1!")

    res = assistant.fill_create_account_form(page, "owner@example.com")
    assert res is False
    assert "CAPTCHA is on the form" in assistant._account_form_failure

