"""A terms-and-conditions step's own consent button is not presumed to be the final submission (f274).

Walmart, 27 October 2026, Terms and Conditions at 86%: the agent had signed the page, and its next press, "Agree" beside
"Back", was stopped as 'final submission is only available through the verified gateway or human handoff'. The guard
presumes a form's only forward button is the last click (some sites hide a real submit under a custom label), and on
this page "Agree" was the only one. The owner's decision of 30 September is that terms and privacy notices are accepted.

The exception is narrow: the button's whole label is a consent word, its form is about terms, privacy or consent, and
nothing else in that form carries a submit label. Everything else keeps the old rule.
"""
import pytest

from submission_guard import SubmissionGuardV0

TERMS = ("<h1>Terms and Conditions</h1><p>Please read the terms and conditions before you continue.</p>"
         '<label><input type="checkbox" checked> Yes, I have read and consent to the terms and conditions above.</label>')


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    instance = context.new_page()
    yield instance
    context.close()


def press(page, form_html, label, button_type="button"):
    """Serve a page with `form_html`, click the button labelled `label`, return (it went through, guard denials)."""
    markup = f'<main><form onsubmit="window.went = true; return false">{form_html}</form></main>'
    page.route("**/*", lambda route: route.fulfill(body=markup, content_type="text/html"))
    page.goto("https://careers.example-employer.test/us/en/jobs/apply")
    guard = SubmissionGuardV0(page.context)
    guard.activate(page)
    page.get_by_role("button", name=label, exact=True).click()
    return bool(page.evaluate("window.went")), guard.denials(page)


def consent_form(label, button_type="button", extra=""):
    onclick = ' onclick="window.went = true"' if button_type == "button" else ""
    return (f'{TERMS}{extra}<button type="{button_type}"{onclick}>{label}</button>'
            '<button type="button">Back</button>')


@pytest.mark.parametrize("button_type", ["button", "submit"])
@pytest.mark.parametrize("label", ["Agree", "I Agree", "Accept", "Accept and Continue", "I accept"])
def test_a_terms_step_consent_button_is_pressed(page, label, button_type):
    went, denials = press(page, consent_form(label, button_type), label)
    assert went and not denials


@pytest.mark.parametrize("variant", ["agree_and_submit", "disagree", "no_terms_wording", "submit_beside_it",
                                     "agree_and_submit_application"])
def test_anything_else_keeps_the_old_rule(page, variant):
    if variant == "agree_and_submit":
        form, label = consent_form("Agree and Submit", "submit"), "Agree and Submit"
    elif variant == "agree_and_submit_application":
        form, label = consent_form("Agree & Submit Application"), "Agree & Submit Application"
    elif variant == "disagree":
        form, label = consent_form("Disagree"), "Disagree"
    elif variant == "no_terms_wording":
        # the same button on a form that says nothing about terms, privacy or consent: nothing proves it is a consent step
        form = '<p>Review your answers.</p><button type="submit">Agree</button><button type="button">Back</button>'
        label = "Agree"
    else:
        # a real submit control in the same form: that one is the final click, and it stays guarded
        form = consent_form("Agree", "button", extra='<button type="submit">Submit Application</button>')
        label = "Submit Application"
    went, denials = press(page, form, label)
    assert not went and denials
