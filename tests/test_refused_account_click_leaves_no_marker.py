"""A Create Account click that never happened must not leave a 'dispatched' marker behind (f175).

Before pressing Create Account the agent records, durably, that it is about to (so a crash mid-click cannot make a
second account). On Marathon the guard refused the click -- nothing was pressed, no account exists -- but the marker
stayed, and every later run stopped with 'an earlier attempt may already have pressed Create Account'. The marker now
stays only when the press may really have happened.
"""
from types import SimpleNamespace

import checkpoint
from submission_guard import SubmissionGuardV0
from test_account_form_with_read_more import WORKDAY, serve as serve_workday  # noqa: F401
from test_account_step import Assistant, CREATE, agent, browser, page, serve, step  # noqa: F401
import pytest


class Store:
    """The durable checkpoint, as the tracker keeps it."""
    def __init__(self):
        self.payload = None

    def read_checkpoint(self, key):
        return self.payload

    def write_checkpoint(self, key, payload):
        self.payload = payload


def test_the_guard_says_whether_it_pressed_the_account_button(page):
    serve_workday(page, WORKDAY.format(extra="Confirm &amp; Send"))
    guard = SubmissionGuardV0(page.context)
    assert not guard.click_account_creation(page, page.locator("[data-automation-id=click_filter]"))
    assert guard.last_credential_click == "not_performed"
    serve_workday(page, WORKDAY.format(extra="Read More"))
    assert guard.click_account_creation(page, page.locator("[data-automation-id=click_filter]"))
    assert guard.last_credential_click == "performed"


class PressingAssistant(Assistant):
    """The press happens inside handle_auth_gate, where the guard records what became of it."""
    def __init__(self, outcome):
        super().__init__(gate=False)
        self._submission_guard = SimpleNamespace(last_credential_click="")
        self._outcome = outcome

    def handle_auth_gate(self, tab, email):
        self._submission_guard.last_credential_click = self._outcome
        return super().handle_auth_gate(tab, email)


def run_create_step(page, tmp_path, outcome):
    serve(page, CREATE)
    assistant = PressingAssistant(outcome)
    a = agent(assistant, tmp_path)
    a.tracker, a.key = Store(), "app-1"
    assert step(page, a) is False
    return a


def test_a_click_the_guard_never_let_through_leaves_nothing_pending(page, tmp_path):
    a = run_create_step(page, tmp_path, "not_performed")
    assert a._pending_account_creation("jobs.example.com") == (False, False)


@pytest.mark.parametrize("outcome", ["unknown", "performed", None])
def test_a_press_that_may_have_happened_stays_pending(page, tmp_path, outcome):
    a = run_create_step(page, tmp_path, outcome)
    assert a._pending_account_creation("jobs.example.com") == (True, False)
