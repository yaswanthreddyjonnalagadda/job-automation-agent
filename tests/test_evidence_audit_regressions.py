"""Synthetic reproductions from the October evidence audit; no model requests."""
import json
from types import SimpleNamespace

import pytest

import account_state as A
import config
import option_match
import page_agent
from claude_integration import ClaudeClient
from test_workday_prompt_textbox import agent, browser, page


@pytest.mark.parametrize("needs,expected", [(True, "Yes, with sponsorship"), (False, None), (None, None)])
def test_native_selection_obeys_policy_before_generic_match(page, needs, expected):
    page.set_content('<label>Eligibility<select><option></option><option>Yes, with sponsorship</option>'
                     '<option>No</option></select></label>')
    a = agent()
    a.profile = SimpleNamespace(requires_visa_sponsorship=needs)
    a.read_when_loaded(page)
    control = next(c for c in a._page_controls(page) if c.options)
    ok = a._choose_exact(page, control, "Yes")
    assert ok is bool(expected)
    assert page.locator('select').input_value() == (expected or "")


@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize("message", ["Password is invalid: must include at least one symbol",
                                         "Password is invalid: minimum length is 10 characters"])
def test_signup_validation_is_not_credential_rejection(count, message):
    snapshot = '- heading "Create Account"\n- alert: ' + message + '\n- textbox "Password"'
    state = A.read_state(snapshot, password_boxes=count)
    assert state.kind == A.CREATE_FORM
    assert state.form_error == message
    for memory in (A.Memory(), A.Memory(account_exists=True), A.Memory(created=True)):
        assert A.next_step(state, memory).action != A.RESET_PASSWORD


@pytest.mark.parametrize("heading,message,expected", [
    ("Sign In", "Password is invalid", A.WRONG_PASSWORD),
    ("Create Account", "Your account is locked. Password is invalid", A.LOCKED),
    ("Create Account", "This email address is already registered. Password is invalid", A.ACCOUNT_EXISTS),
])
def test_explicit_auth_evidence_keeps_its_meaning(heading, message, expected):
    state = A.read_state(f'- heading "{heading}"\n- alert: {message}\n- textbox "Password"', password_boxes=1)
    assert state.kind == expected


@pytest.mark.parametrize("question,answer,options,needs,expected", [
    ("Highest degree?", "Bachelor's", ["High School", "Bachelor's Degree"], None, "Bachelor's Degree"),
    ("Notice period?", "2 weeks", ["Immediate", "Within 2 weeks", "1 month"], None, "Within 2 weeks"),
    ("Years of experience?", "2 weeks", ["Immediate", "Within 2 weeks", "1 month"], None, ""),
    ("Notice period?", "2 weeks", ["Within 2 weeks", "14 days"], None, ""),
    ("Eligible to work?", "Yes", ["Yes, with sponsorship", "No"], False, ""),
    ("Eligible to work?", "Yes", ["Yes, with sponsorship", "No"], None, ""),
    ("Eligible to work?", "Yes", ["Yes, with sponsorship", "No"], True, "Yes, with sponsorship"),
    ("Choose?", "unrelated", ["Only choice"], None, ""),
])
def test_ai_option_answers_use_bounded_shared_resolution(question, answer, options, needs, expected):
    client = ClaudeClient.__new__(ClaudeClient)
    client._call = lambda **kw: json.dumps({"answer": answer})
    profile = config.UserProfile(requires_visa_sponsorship=needs)
    assert client.answer_single_question(question, options=options, profile=profile) == expected
