"""The site's own words reach the owner when Create Account is refused (f177).

Workday shows a form error as <p data-automation-id="inputAlert">Please check the box to continue</p> under the field
and marks the field aria-invalid; it is neither role=alert nor class '...error'. The agent read none of it: Marathon's
stop said only 'the site kept the form open', so the cause (and the fix) stayed hidden for four runs.
"""
import pytest

import safety
from browser_automation import JobApplicationAssistant
from submission_guard import SubmissionGuardV0
from test_account_creation_gateway import browser, page, serve  # noqa: F401

ALERT = ('<p data-automation-id="inputAlert" id="error1-input-9"><span>Error :</span>'
         'Please check the box to continue</p>')


def assistant(page):
    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a.values = safety.AgentValues()
    a._profile = None
    a.detect_form_fields = lambda _: []
    a._submission_guard = SubmissionGuardV0(page.context)
    a._read_ats_password = lambda: "Synthetic-Password1!"
    return a


def test_a_workday_input_alert_is_read_as_an_error_text(page):
    page.set_content("<form><input aria-invalid='true'>" + ALERT + "</form>")
    assert any("Please check the box to continue" in t for t in assistant(page)._visible_error_texts(page))


def test_an_ordinary_page_has_no_error_text(page):
    page.set_content("<form><input><p>Choose a password.</p></form>")
    assert assistant(page)._visible_error_texts(page) == []


FORM = """<form onsubmit="var p = document.createElement('p'); p.setAttribute('data-automation-id', 'inputAlert');
    p.textContent = 'Please check the box to continue'; this.appendChild(p); return false">
<label>Email Address<input type="email" value="owner@example.com"></label>
<label>Password<input type="password" value="Synthetic-Password1!"></label>
<label>Verify New Password<input type="password" value="Synthetic-Password1!"></label>
<button type="submit">Create Account</button></form>"""


def test_a_refused_create_account_carries_the_sites_own_words(page):
    serve(page, FORM)
    a = assistant(page)
    assert not a.fill_create_account_form(page, "owner@example.com")
    assert "Please check the box to continue" in a._account_form_failure
