"""Every way the Create Account form can fail says why, and the run stops on that reason (f176).

Marathon (Workday): the account step gave up without a word, the page agent read the account form as an ordinary
page, pressed its 'Create Account' button the normal way, and the submission guard -- rightly -- refused: the stop
said 'final submission is only available through the verified gateway', which named nothing that had gone wrong.
Of the dozen ways the form-filler can return False, all but two kept their reason to themselves.
"""
from types import SimpleNamespace

import pytest

import safety
from browser_automation import JobApplicationAssistant
from submission_guard import SubmissionGuardV0
from test_account_creation_gateway import browser, page, serve  # noqa: F401
from test_account_step import Assistant, CREATE, agent, step  # noqa: F401
from test_account_step import browser as step_browser, page as step_page  # noqa: F401

BASE = """<form onsubmit="return false">
<label>Email Address<input type="email"></label>
<label>Password<input type="password"></label>
<label>Verify New Password<input type="password"></label>
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


def test_no_password_configured_is_named(page):
    serve(page, BASE.format(extra="", button="<button>Create Account</button>"))
    a = assistant_for(page, password="")
    assert not fill(page, a)
    assert "ATS_PASSWORD" in a._account_form_failure


def test_a_form_with_no_create_button_is_named(page):
    serve(page, BASE.format(extra="", button=""))
    a = assistant_for(page)
    assert not fill(page, a)
    assert "Create Account button" in a._account_form_failure


def test_a_box_only_the_owner_may_tick_is_named(page):
    box = '<label><input type="checkbox"> I accept the Terms and Conditions</label>'
    serve(page, BASE.format(extra=box, button="<button>Create Account</button>"))
    a = assistant_for(page, profile=SimpleNamespace(sign_attestations=False, accept_application_privacy_prompts=True,
                                                    full_name="", email=""))
    assert not fill(page, a)
    assert "tick" in a._account_form_failure.lower() and "terms" in a._account_form_failure.lower()


def test_a_good_form_leaves_no_failure_behind(page):
    serve(page, BASE.format(extra="", button="<button type='submit'>Create Account</button>"))
    a = assistant_for(page)
    a._account_form_failure = "left over from an earlier try"
    fill(page, a)
    assert a._account_form_failure != "left over from an earlier try"


def test_the_run_stops_on_the_reason_and_never_presses_the_form_as_a_page(step_page, tmp_path):
    from test_account_step import serve as serve_step
    serve_step(step_page, CREATE)
    assistant = Assistant(gate=False)
    assistant._account_form_failure = "the email box did not keep the address"
    a = agent(assistant, tmp_path)
    assert step(step_page, a) is False
    assert "the email box did not keep the address" in a.account_blocker
    assert "Create Account" in a.account_blocker
