"""A consent box labelled only 'Yes, I consent' means the text above it (f179; the owner's decision of 2 October 2026).

Marathon Petroleum (Workday): the box under 'I agree to creating this account ... I agree to receive recurring calls and
text messages ... regarding my application' is labelled only 'Yes, I consent'. The agent judged the label alone, never
ticked the box, and pressed Create Account; Workday answered 'Please check the box to continue' on every run. The
owner's standing decision: a required consent that agrees to creating the account, and to calls and texts about the
application, is ticked. Marketing, sharing data, terms, declarations stay the owner's -- with the words shown.
"""
from types import SimpleNamespace

import pytest

import safety
from browser_automation import JobApplicationAssistant
from submission_guard import SubmissionGuardV0
from test_account_creation_gateway import browser, page, serve  # noqa: F401

ACCOUNT = "I agree to creating this account to allow me to apply for positions with Example Co."
CALLS_AND_TEXTS = ("By providing my phone number on my application or during creation of this account, I agree to "
                   "receive recurring calls and text messages, including by automated means, regarding my "
                   "application or employment opportunities from Example Co and its partners, vendors, or affiliates.")
MARKETING = "I agree to receive marketing text messages and promotions about offers from Example Co and its partners."

FORM = """<form onsubmit="window.created = (window.created || 0) + 1; return false">
<label>Email Address<input type="email"></label>
<label>Password<input type="password"></label>
<label>Verify New Password<input type="password"></label>
<div><div><p>{paragraphs}</p></div><button type="button">Read More</button></div>
<div><input type="checkbox" id="c"><label for="c">Yes, I consent</label></div>
<button type="submit" onclick="if (!document.getElementById('c').checked) {{ window.refused = true; return false; }}">Create Account</button>
</form>"""


def assistant(page):
    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a.values = safety.AgentValues()
    a._profile = SimpleNamespace(accept_application_privacy_prompts=True, sign_attestations=False, full_name="", email="")
    a.detect_form_fields = lambda _: []
    a._submission_guard = SubmissionGuardV0(page.context)
    a._read_ats_password = lambda: "Synthetic-Password1!"
    return a


def run(page, paragraphs):
    serve(page, FORM.format(paragraphs=paragraphs))
    a = assistant(page)
    a.fill_create_account_form(page, "owner@example.com")
    return a


def test_a_consent_only_to_creating_the_account_is_ticked(page):
    run(page, ACCOUNT)
    assert page.evaluate("window.refused") is None
    assert page.evaluate("window.created") == 1


def test_calls_and_texts_about_the_application_inside_the_account_consent_are_agreed_to(page):
    run(page, f"{ACCOUNT} {CALLS_AND_TEXTS}")
    assert page.evaluate("window.refused") is None
    assert page.evaluate("window.created") == 1


def test_a_consent_that_also_agrees_to_marketing_is_left_for_the_owner_with_its_words(page):
    a = run(page, f"{ACCOUNT} {MARKETING}")
    assert page.evaluate("window.created") is None and page.evaluate("window.refused") is None  # nothing pressed
    assert page.locator("#c").is_checked() is False
    assert "marketing text messages" in a._account_form_failure
    assert "tick it" in a._account_form_failure.lower()


@pytest.mark.parametrize("contact, allowed", [
    (CALLS_AND_TEXTS, True),
    ("I consent to SMS about my application.", True),
    ("I agree to receive text messages with marketing and promotions.", False),
    ("I agree to receive calls about my application and to share my data with third parties.", False),
    ("I agree to receive the newsletter.", False),
    ("I accept the terms and conditions.", False),
    ("I certify that my answers are true.", False),
])
def test_only_calls_and_texts_about_the_application_ride_along_with_account_consent(contact, allowed):
    assert safety.is_account_creation_consent(f"{ACCOUNT} {contact}") is allowed
    assert safety.is_account_creation_consent(contact) is False        # never on their own: account consent only
