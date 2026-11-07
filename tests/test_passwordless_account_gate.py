"""A registration gate with no password field at all (ADT Careers on Oracle/Phenom, 6 November 2026):
"Authentication screen. You don't need to have an account" asks only for an email and a consent
checkbox, then Next. `handle_auth_gate`'s pw_count==0 branch returned False immediately, and
`fill_create_account_form`'s own entry guard required at least one password field, so this page
never reached the already-permissive acknowledgement logic (acknowledgements.is_acknowledgement,
f336) that would otherwise tick a routine "I agree with the terms and conditions" box on its own --
the page fell to generic filling, which pressed Next past the unticked box and looped (f337's
alternating-page symptom) until the circuit breaker caught it.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import safety
from browser_automation import JobApplicationAssistant
from submission_guard import SubmissionGuardV0
from test_account_creation_gateway import browser, page, serve  # noqa: F401

NO_PASSWORD_FORM = """<h2>Authentication screen</h2>
<form onsubmit="window.created=(window.created||0)+1; return false">
<label>Email Address<input type="email"></label>
<label><input type="checkbox"> I agree with the terms and conditions</label>
<button type="submit">Next</button></form>"""


def test_a_passwordless_gate_ticks_the_terms_box(page):
    """The box gets ticked through the already-authorized acknowledgement path (f336) once the form
    is even recognised. Whether SubmissionGuardV0 then approves the final click on a 0-password form
    is a separate, stricter question this test does not depend on -- out of scope here."""
    serve(page, NO_PASSWORD_FORM)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    assistant._profile = None
    assistant.adapter = lambda _: SimpleNamespace(
        candidate_account_state=lambda _: "", create_account_extras=lambda *_: None,
        overlay_selector=lambda: "")
    assistant.detect_form_fields = lambda _: []
    assistant._submission_guard = SubmissionGuardV0(page.context)
    assistant._read_ats_password = lambda: "Synthetic-Password1!"

    assistant.fill_create_account_form(page, "owner@example.com")
    assert page.locator("input[type=checkbox]").is_checked()
    assert assistant._account_form_failure != "no Create Account button was found on the form"


def test_a_passwordless_gate_is_routed_to_the_account_form_filler(page):
    """handle_auth_gate's own pw_count==0 bail must not short-circuit before the careful filler runs."""
    page.set_content(
        '<form><h2>Authentication screen</h2><input type=email>'
        '<label><input type=checkbox> I agree with the terms and conditions</label>'
        '<button>Next</button></form>'
    )
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.adapter = lambda _: SimpleNamespace(candidate_account_state=lambda _: "")
    assistant._read_ats_password = lambda: "Synthetic-Password1!"
    assistant._account_exists_message = lambda _: False
    assistant.account_on_record = lambda: False
    assistant.fill_create_account_form = Mock(return_value=True)
    assert assistant.handle_auth_gate(page, "owner@example.com")
    assistant.fill_create_account_form.assert_called_once()
