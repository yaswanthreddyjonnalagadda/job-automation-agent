"""A marketing opt-in the site ticked is unticked before an account form is sent (f276).

Walmart, 28 October 2026: the sign-up form draws "Send me emails about new arrivals, hot items, daily savings and more."
already ticked. The owner's rule is that marketing opt-ins are never given for them; the code left such a box unticked but
never unticked one the site had ticked, so Continue would have subscribed them.
"""
import pytest

import safety
from browser_automation import JobApplicationAssistant
from marketing_opt_in import is_marketing_opt_in
from sites.base import SiteAdapter

WALMART_MARKETING = "Send me emails about new arrivals, hot items, daily savings and more."


@pytest.mark.parametrize("label", [
    WALMART_MARKETING, "Keep me updated with our newsletter", "I want to hear more about career opportunities",
    "Sign me up for job alerts", "Send me special offers", "Email me about upcoming events",
    "I agree to receive promotional messages", "Yes, send me marketing communications",
])
def test_marketing_wordings_are_recognised(label):
    assert is_marketing_opt_in(label)


@pytest.mark.parametrize("label", [
    "Keep me signed in", "Remember me", "I agree to creating this account to allow me to apply for positions",
    "I have read and accept the privacy notice", "I consent to calls and texts regarding my application",
    "I certify that the information is true", "Email me about my application status", "",
])
def test_other_boxes_are_not_marketing(label):
    assert not is_marketing_opt_in(label)


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


@pytest.fixture
def agent(monkeypatch):
    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a.values = safety.AgentValues()
    a._profile = None
    a.adapter = lambda _page: SiteAdapter()
    monkeypatch.setenv("SITE_PREFILL_POLICY", "correct")
    return a


FORM = """<form>
<label><input type="checkbox" id="signed" checked> Keep me signed in</label>
<label><input type="checkbox" id="mail" checked> %s</label>
<label><input type="checkbox" id="privacy" checked> I have read and accept the privacy notice</label>
</form>"""


def test_a_marketing_box_the_site_ticked_is_unticked_and_nothing_else_is_touched(page, agent):
    page.set_content(FORM % WALMART_MARKETING)
    assert agent._clear_site_ticked_marketing(page) == []
    assert page.locator("#mail").is_checked() is False
    assert page.locator("#signed").is_checked() is True and page.locator("#privacy").is_checked() is True


def test_a_marketing_box_that_was_not_ticked_is_left_alone(page, agent):
    page.set_content((FORM % WALMART_MARKETING).replace('id="mail" checked', 'id="mail"'))
    assert agent._clear_site_ticked_marketing(page) == []
    assert page.locator("#mail").is_checked() is False


def test_under_the_leave_policy_the_sites_own_value_stands(page, agent, monkeypatch):
    """The owner's SITE_PREFILL_POLICY=leave keeps what the site drew, on every form."""
    monkeypatch.setenv("SITE_PREFILL_POLICY", "leave")
    page.set_content(FORM % WALMART_MARKETING)
    assert agent._clear_site_ticked_marketing(page) == []
    assert page.locator("#mail").is_checked() is True


def test_a_box_that_ticks_itself_again_is_reported_so_the_form_is_not_sent(page, agent):
    page.set_content(FORM % WALMART_MARKETING)
    page.evaluate("document.getElementById('mail').addEventListener('change', e => { e.target.checked = true })")
    stuck = agent._clear_site_ticked_marketing(page)
    assert stuck and "new arrivals" in stuck[0].lower()


def test_a_box_labelled_only_by_aria_is_found_too(page, agent):
    page.set_content('<input type="checkbox" id="mail" checked aria-label="%s">' % WALMART_MARKETING)
    assert agent._clear_site_ticked_marketing(page) == []
    assert page.locator("#mail").is_checked() is False


CREATE_FORM = """<html><body><h1>Create Account</h1>
<label for="email">Email Address</label><input id="email" type="email">
<label for="pw">Password</label><input id="pw" type="password">
<label for="pw2">Verify New Password</label><input id="pw2" type="password">
<label><input type="checkbox" id="agree"> I agree to creating this account to allow me to apply for positions with Acme.</label>
<label><input type="checkbox" id="mail" checked> MARKETING</label>
<button id="create">Create Account</button>
<script>
  window.created = false;
  document.getElementById('create').addEventListener('click', () => {
    window.marketing_at_create = document.getElementById('mail').checked;
    document.body.innerHTML = '<h1>Candidate Home</h1>'; window.created = true;
  });
</script></body></html>"""


def serve_create_form(page, marketing=WALMART_MARKETING):
    body = CREATE_FORM.replace("MARKETING", marketing)
    page.route("https://careers.example-ats.com/**", lambda route: route.fulfill(status=200, content_type="text/html", body=body))
    page.goto("https://careers.example-ats.com/careers/apply")


def test_the_create_form_is_sent_with_the_marketing_box_the_site_ticked_unticked(page, agent, monkeypatch):
    """The whole flow, not the helper alone: the state of the box when Create Account is pressed."""
    monkeypatch.setattr(JobApplicationAssistant, "_read_ats_password", staticmethod(lambda: "s3cret-ATS"))
    serve_create_form(page)
    assert agent.fill_create_account_form(page, "owner@example.com") is True
    assert page.evaluate("window.created") is True
    assert page.evaluate("window.marketing_at_create") is False


def test_the_create_form_is_held_when_the_site_will_not_let_the_box_go(page, agent, monkeypatch):
    monkeypatch.setattr(JobApplicationAssistant, "_read_ats_password", staticmethod(lambda: "s3cret-ATS"))
    serve_create_form(page)
    page.evaluate("document.getElementById('mail').addEventListener('change', e => { e.target.checked = true })")
    assert agent.fill_create_account_form(page, "owner@example.com") is False
    assert page.evaluate("window.created") is False            # nothing was sent with the box ticked
