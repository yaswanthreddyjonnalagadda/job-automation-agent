"""HonorHealth (Workday), 25 September: Create Account was pressed with the required
"I agree to creating this account to allow me to apply for positions with HonorHealth." box unticked,
and Workday answered "Please check the box to continue".

Two causes, both classes:
- the agent's consent rule knew privacy notices only, so consent to creating the very account the
  owner asked for was passed over in silence -- a required control no rule covers;
- Workday draws its checkbox under an overlay, so a normal tick lands on the overlay and the state does
  not change; the agent assumed a click had ticked it.

The owner approved the safety.py change on 25 September 2026: consent to creating the account only --
never terms, attestations, signatures, marketing or data-sharing boxes.
"""
import json
import re

import pytest

import safety
from browser_automation import JobApplicationAssistant
from sites.base import SiteAdapter

WORKDAY_LABEL = "I agree to creating this account to allow me to apply for positions with HonorHealth."


@pytest.mark.parametrize("text", [
    WORKDAY_LABEL,
    "I agree to create an account with Acme Corp",
    "By checking this box, I consent to the creation of an account.",
    "I acknowledge that I am creating an account with this employer",
    "I accept the creation of this account so I can apply",
    "i agree to creating this account to allow me to apply for positions with honorhealth.",
])
def test_consent_to_creating_the_account_is_accepted(text):
    assert safety.is_account_creation_consent(text)
    assert not safety.is_attestation(text)


@pytest.mark.parametrize("text", [
    "I agree to the terms and conditions and to creating this account",          # terms stay the candidate's
    "I certify that I agree to creating this account",                             # an attestation
    "I agree to creating this account and to receive job alerts",                  # a marketing opt-in
    "I agree to creating this account and to share my data with third parties",    # data sharing
    "I agree to creating this account. The information I gave is true and accurate.",
    "I agree to creating this account by typing my name below",                    # a signature
    "I agree",
    "I agree to the privacy policy",                                               # a privacy notice: its own rule
    "Create account",                                                              # a button, not a consent
    "",
])
def test_nothing_else_is_taken_for_it(text):
    assert not safety.is_account_creation_consent(text)


def test_the_accounts_own_consent_check_takes_it_and_still_refuses_the_rest():
    consent = JobApplicationAssistant._is_account_consent
    assert consent(WORKDAY_LABEL.lower(), "")
    assert consent("i have read and accept the privacy policy", "")                    # unchanged
    assert not consent("i agree to the terms and conditions", "")                      # unchanged
    assert not consent("i certify that this is true", "")                              # unchanged
    assert not consent("", WORKDAY_LABEL)          # nearby text alone never counts: only the box's own label


# --- the form itself -----------------------------------------------------------------------

HOST = "careers.example-ats.com"

FORM = """<html><body><h1>Create Account</h1><div id="alert" role="alert"></div>
<label for="email">Email Address<span>*</span></label><input id="email" type="email">
<label for="pw">Password<span>*</span></label><input id="pw" type="password">
<label for="pw2">Verify New Password<span>*</span></label><input id="pw2" type="password">
<div style="position:relative;height:30px">
  <input type="checkbox" id="agree" aria-checked="false" data-automation-id="createAccountCheckbox"
         style="position:absolute;left:0;top:0;width:24px;height:24px;opacity:0">
  <span id="cover" style="position:absolute;left:0;top:0;width:24px;height:24px;z-index:5"></span>
  <label for="agree" style="position:absolute;left:40px;top:0" id="text">LABEL</label>
</div>
<button id="create">Create Account</button>
<script>
  // Workday's box: the mouse reaches an overlay, not the input, so clicking it ticks nothing.
  document.getElementById('cover').addEventListener('click', e => e.stopPropagation());
  document.getElementById('text').addEventListener('click', e => e.preventDefault());
  window.created = false;
  document.getElementById('create').addEventListener('click', () => {
    if (!document.getElementById('agree').checked) {
      document.getElementById('alert').textContent = 'Please check the box to continue'; return;
    }
    document.body.innerHTML = '<h1>Candidate Home</h1>'; window.created = true;
  });
</script></body></html>"""


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
    monkeypatch.setattr(JobApplicationAssistant, "_read_ats_password", staticmethod(lambda: "s3cret-ATS"))
    return a


def serve(page, label):
    body = FORM.replace("LABEL", label)
    page.route(f"https://{HOST}/**", lambda route: route.fulfill(status=200, content_type="text/html", body=body))
    page.goto(f"https://{HOST}/careers/apply")


def test_the_account_is_created_with_the_consent_box_ticked_though_the_mouse_cannot_reach_it(page, agent):
    serve(page, WORKDAY_LABEL)
    assert agent.fill_create_account_form(page, "owner@example.com") is True
    assert page.evaluate("window.created") is True


def test_a_terms_box_is_left_for_the_owner_and_nothing_is_created(page, agent):
    serve(page, "I agree to the terms and conditions of this site")
    assert agent.fill_create_account_form(page, "owner@example.com") is False
    assert page.evaluate("window.created") is False
    assert page.locator("#agree").is_checked() is False


def test_a_marketing_box_is_never_ticked(page, agent):
    serve(page, "I agree to receive job alerts and to creating this account")
    agent.fill_create_account_form(page, "owner@example.com")
    assert page.locator("#agree").is_checked() is False


def test_a_box_that_cannot_be_ticked_at_all_is_left_for_the_owner(page, agent):
    """Not even a script can tick it (disabled): the agent says so instead of pressing Create."""
    serve(page, WORKDAY_LABEL)
    page.evaluate("document.getElementById('agree').disabled = true")
    assert agent.fill_create_account_form(page, "owner@example.com") is False
    assert page.evaluate("window.created") is False


def test_a_tick_helper_reports_what_the_box_ended_as(page):
    page.set_content('<input type="checkbox" id="a"><input type="checkbox" id="b" disabled>')
    assert JobApplicationAssistant._tick_checkbox(page.locator("#a")) is True
    assert JobApplicationAssistant._tick_checkbox(page.locator("#b")) is False
