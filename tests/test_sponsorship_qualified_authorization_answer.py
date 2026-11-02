"""
"No, I need sponsorship now" to "Are you authorized to work...?" is the sponsorship answer, not a denial (f191).

Datadog, 18 October: the work-authorization list offers "Yes", "No, I need sponsorship now" and "No, I will need
sponsorship in the future". For an owner who is authorized today but needs an H-1B transfer, the sponsored row is the
true one. safety.legal_answer_conflicts read any leading "No" as "not authorized" and flagged it, and the truthfulness
declaration was then refused. An option that says what the applicant needs about sponsorship is judged against
whether they need it (option_match.sponsorship_said, shared with the option choice); a plain "No" is still a denial.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import option_match  # noqa: E402
import safety  # noqa: E402

QUESTION = "Are you legally authorized to work in the United States?*"


class Profile:
    def __init__(self, needs: bool, eligible: str = "Yes"):
        self.requires_visa_sponsorship = needs
        self.legally_eligible_to_work = eligible


def conflicts(value: str, needs: bool) -> list[str]:
    return safety.legal_answer_conflicts([{"label": QUESTION, "value": value}], Profile(needs))


NEEDS = ["No, I need sponsorship now", "No, I will need sponsorship in the future", "Yes, with company sponsorship",
         "Yes, but I require visa sponsorship", "I will require sponsorship"]
DOES_NOT_NEED = ["Yes, without restrictions", "Yes, I do not need sponsorship", "Yes, without sponsorship",
                 "I don't need sponsorship", "Yes, I will not require sponsorship"]


@pytest.mark.parametrize("value", NEEDS)
def test_options_that_say_sponsorship_is_needed(value):
    assert option_match.sponsorship_said(value) is True
    assert conflicts(value, needs=True) == []
    assert conflicts(value, needs=False), value


@pytest.mark.parametrize("value", DOES_NOT_NEED)
def test_options_that_say_sponsorship_is_not_needed(value):
    assert option_match.sponsorship_said(value) is False
    assert conflicts(value, needs=False) == []
    assert conflicts(value, needs=True), value


@pytest.mark.parametrize("value", ["Yes", "No", "Prefer not to say", "United States"])
def test_options_that_do_not_speak_of_sponsorship(value):
    assert option_match.sponsorship_said(value) is None


def test_a_plain_no_is_still_a_denial_of_authorization():
    assert conflicts("No", needs=True)
    assert conflicts("No", needs=False)


def test_a_plain_yes_is_still_fine():
    assert conflicts("Yes", needs=True) == []


def test_the_shared_choice_still_picks_the_sponsored_row():
    rows = ["Yes, without restrictions", "Yes, with company sponsorship"]
    assert option_match.sponsorship_choice(rows, "Yes", True) == (True, 1)
    assert option_match.sponsorship_choice(rows, "Yes", False) == (True, 0)
