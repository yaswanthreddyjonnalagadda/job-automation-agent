"""A registration gate with no password field at all (ADT Careers, 6 November 2026): Phenom People's
'Authentication screen. You don't need to have an account' widget asks only for an email and a consent
checkbox, then Next. `handle_auth_gate` required at least one password field to even look at the page,
so a required-but-candidate-only 'terms and conditions' checkbox there never got held for the candidate --
the page fell to generic filling, which pressed Next past the unticked box and looped until the circuit
breaker caught it three attempts later.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import safety
from browser_automation import JobApplicationAssistant
from submission_guard import SubmissionGuardV0
from test_account_creation_gateway import browser, page, serve  # noqa: F401

NO_PASSWORD = """<form onsubmit="return false">
<label>Email Address<input type="email"></label>
{extra}
{button}</form>"""


def assistant_for(page, profile=None, password="Synthetic-Password1!"):
    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a.values = safety.AgentValues()
    a._profile = profile
    a.detect_form_fields = lambda _: []
    a._submission_guard = SubmissionGuardV0(page.context)
    a._read_ats_password = lambda: password
    return a


def fill(page, a):
    return a.fill_create_account_form(page, "owner@example.com")


def test_a_terms_box_on_a_passwordless_gate_is_held_for_the_candidate(page):
    box = '<label><input type="checkbox"> I agree with the terms and conditions</label>'
    serve(page, NO_PASSWORD.format(extra=box, button="<button>Next</button>"))
    a = assistant_for(page)
    assert not fill(page, a)
    assert "tick" in a._account_form_failure.lower() and "terms" in a._account_form_failure.lower()


def test_a_passwordless_gate_with_nothing_held_looks_past_create_account_wording(page):
    """Button-matching only: whether SubmissionGuardV0 then approves the click is out of scope here."""
    serve(page, NO_PASSWORD.format(extra="", button="<button>Next</button>"))
    a = assistant_for(page)
    fill(page, a)
    assert a._account_form_failure != "no Create Account button was found on the form"


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
