"""The consent box is re-checked just before Create Account is pressed (f178).

Marathon (Workday): the run pressed Create Account and the site answered 'Please check the box to continue'. Workday
redraws the form as boxes are ticked and passwords typed, so a box ticked early can be clear again by the time the
button is pressed. The agent re-checked the email and the passwords before pressing, but not the boxes it had ticked.
"""
import safety
from browser_automation import JobApplicationAssistant
from submission_guard import SubmissionGuardV0
from test_account_creation_gateway import browser, page, serve  # noqa: F401

CONSENT = "I agree to creating this account to allow me to apply for positions with Acme."

FORM = """<form onsubmit="window.created = (window.created || 0) + 1; return false">
<label>Email Address<input type="email"></label>
<label>Password<input type="password"></label>
<label>Verify New Password<input type="password"></label>
<label><input id="c" type="checkbox"> """ + CONSENT + """</label>
<button type="submit" onclick="if (!document.getElementById('c').checked) {{ window.refused = true; return false; }}">Create Account</button>
</form>"""


def assistant(page):
    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a.values = safety.AgentValues()
    a._profile = None
    a.detect_form_fields = lambda _: []
    a._submission_guard = SubmissionGuardV0(page.context)
    a._read_ats_password = lambda: "Synthetic-Password1!"
    return a


def test_a_box_cleared_by_a_redraw_is_ticked_again_before_the_press(page):
    serve(page, FORM)
    a = assistant(page)
    real = a._email_kept
    calls = []

    def redraw_after_the_last_field_check(boxes, email):
        result = real(boxes, email)
        calls.append(1)
        if len(calls) == 2:          # the second look is the one right before the press
            page.evaluate("document.getElementById('c').checked = false")
        return result

    a._email_kept = redraw_after_the_last_field_check
    a.fill_create_account_form(page, "owner@example.com")
    assert page.evaluate("window.refused") is None, "Create Account was pressed with the consent box clear"
    assert page.evaluate("window.created") == 1


def test_a_box_that_will_not_stay_ticked_stops_the_run_with_a_reason(page):
    serve(page, FORM)
    page.evaluate("document.getElementById('c').addEventListener('click', e => e.preventDefault())")
    a = assistant(page)
    assert not a.fill_create_account_form(page, "owner@example.com")
    assert "box" in a._account_form_failure.lower() and "tick" in a._account_form_failure.lower()
    assert page.evaluate("window.created") is None
