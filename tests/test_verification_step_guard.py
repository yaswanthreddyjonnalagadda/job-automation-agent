"""A candidate verification / OTP passcode step's verify button is not presumed to be the final submission.

Oracle Cloud Candidate Experience (ADT Careers): candidate identity verification challenge presents a form
with 6 pin-code digit inputs and a 'VERIFY' button. SubmissionGuardV0's soleRemainingAction heuristic
presumed 'VERIFY' was the final application submit button because it was the only forward action in the form,
intercepting and denying the click.
"""
import pytest

from submission_guard import SubmissionGuardV0

OTP_FORM = (
    '<header><h1>Confirm Your Identity</h1></header>'
    '<p>The verification code was sent to this email address. When you get the code, type the code into the field to confirm your identity.</p>'
    '<fieldset>'
    '<legend>Verification Code</legend>'
    '<input type="text" id="pin-code-1" aria-label="Enter verification code digit 1 of six." value="1">'
    '<input type="text" id="pin-code-2" aria-label="Enter verification code digit 2 of six." value="2">'
    '<input type="text" id="pin-code-3" aria-label="Enter verification code digit 3 of six." value="3">'
    '<input type="text" id="pin-code-4" aria-label="Enter verification code digit 4 of six." value="4">'
    '<input type="text" id="pin-code-5" aria-label="Enter verification code digit 5 of six." value="5">'
    '<input type="text" id="pin-code-6" aria-label="Enter verification code digit 6 of six." value="6">'
    '</fieldset>'
)


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
    markup = f'<main><form class="pin-code-verification" onsubmit="window.went = true; return false">{form_html}</form></main>'
    page.route("**/*", lambda route: route.fulfill(body=markup, content_type="text/html"))
    page.goto("https://careers.example-employer.test/us/en/jobs/apply/email")
    guard = SubmissionGuardV0(page.context)
    guard.activate(page)
    page.get_by_role("button", name=label, exact=True).click()
    return bool(page.evaluate("window.went")), guard.denials(page)


def otp_form(label, button_type="button"):
    onclick = ' onclick="window.went = true"' if button_type == "button" else ""
    return (f'{OTP_FORM}<button type="{button_type}"{onclick}>{label}</button>'
            '<button type="button">Send New Code</button>')


@pytest.mark.parametrize("button_type", ["button", "submit"])
@pytest.mark.parametrize("label", ["VERIFY", "Verify", "Verify Code", "Confirm", "Confirm Code"])
def test_a_verification_step_verify_button_is_pressed(page, label, button_type):
    went, denials = press(page, otp_form(label, button_type), label)
    assert went and not denials
