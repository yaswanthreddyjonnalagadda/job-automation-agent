"""Applied to this employer before? Then sign in -- never make a second account (owner's rule, 17 October 2026).

The owner: prefer Google; otherwise, when we have applied to the same client before, sign in without creating an
account; only a client we have never applied to gets a new account. The agent only knew an account by the exact
employer string in its accounts table, so 'Marathon Petroleum' and 'Marathon Petroleum Company LP', or an application
sent before the account was recorded, looked like a stranger and the agent went to Create Account (f173).
"""
import pytest

import employer_names
from job_tracker import JobTracker
from test_account_step import Assistant, CREATE, agent, browser, page, serve, step  # noqa: F401


@pytest.mark.parametrize("a, b", [
    ("Marathon Petroleum Company LP", "marathon petroleum"),
    ("Copart, Inc", "COPART INC."),
    ("Chipotle Mexican Grill, Inc.", "Chipotle Mexican Grill"),
    ("The Acme Corporation", "Acme Corp"),
])
def test_one_employer_written_two_ways_is_one_employer(a, b):
    assert employer_names.same_employer(a, b)


@pytest.mark.parametrize("a, b", [
    ("Marathon Petroleum", "Marathon Oil"),
    ("Axon", "Axon Enterprise Staffing"),
    ("", "Acme"),
    ("Inc", "LLC"),
])
def test_different_or_empty_employers_are_never_the_same(a, b):
    assert not employer_names.same_employer(a, b)


@pytest.fixture
def tracker(tmp_path):
    return JobTracker(tmp_path / "applications.db")


def apply_to(tracker, key, company, status):
    tracker.create(dedup_key=key, title="Network Engineer", company=company, location="", url=f"https://x.example/{key}")
    tracker.update_status(key, status, notes="")


def test_a_submitted_application_to_the_same_employer_counts_as_applied_before(tracker):
    apply_to(tracker, "k1", "Marathon Petroleum Company LP", "submitted")
    assert tracker.applied_before("Marathon Petroleum")
    assert tracker.applied_before("MARATHON PETROLEUM CO")


def test_an_application_that_never_got_in_or_another_employer_does_not(tracker):
    apply_to(tracker, "k1", "Marathon Petroleum Company LP", "needs_user_review")
    apply_to(tracker, "k2", "Marathon Oil Corporation", "submitted")
    assert not tracker.applied_before("Marathon Petroleum")
    assert not tracker.applied_before("")


def test_a_create_form_with_an_employer_applied_to_before_goes_to_sign_in(page, tmp_path):
    serve(page, CREATE)
    assistant = Assistant(exists=False)
    assistant.account_expected = lambda: True
    assert step(page, agent(assistant, tmp_path)) is True
    assert assistant.calls == [("login_page",)]


def test_a_stranger_employer_still_gets_its_account_made(page, tmp_path):
    serve(page, CREATE)
    assistant = Assistant(exists=False, gate=True)
    assistant.account_expected = lambda: False
    assert step(page, agent(assistant, tmp_path)) is True
    assert assistant.calls == [("gate", "jane@example.com")]
