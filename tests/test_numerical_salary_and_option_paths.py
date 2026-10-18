"""Numerical salary / expected earnings recognition and unified sponsorship-qualified option selection (f187).

1. Invesco (Workday): 'What would you expect to earn per year in the position for which you have applied? Please provide
   a numerical value.*' and Altera Digital Health: 'What is your desired annual compensation?*' were missed by
   DESIRED_SALARY, causing the open-ended AI writer to type a multi-sentence paragraph into a numeric salary box.
   When a salary question asks for a numerical value ('numerical value', 'numbers only', 'single number'), DESIRED_SALARY
   resolves to plain numeric `salary_min` ('125000') rather than '$125,000 - $185,000'.
2. Sponsorship-qualified 'Yes' choices ('Yes, without restrictions' vs 'Yes, with company sponsorship') must follow
   `profile.requires_visa_sponsorship` across radio button groups (`_control_for`, `do`), cached `native_select`
   recipes, and group choices (`_choose_inside`), not only custom dropdowns (`_pick`).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import concept_matcher
import page_agent
from browser_automation import JobApplicationAssistant


INVESCO_SALARY_Q = (
    "What would you expect to earn per year in the position for which you have applied? "
    "Please provide a numerical value.*"
)
ALTERA_SALARY_Q = "What is your desired annual compensation?*"
INVESCO_OPTIONS = ["Select One", "Yes, without restrictions", "Yes, with company sponsorship", "No"]


@pytest.mark.parametrize(
    "question",
    [
        INVESCO_SALARY_Q,
        ALTERA_SALARY_Q,
        "What are your expected annual earnings for this role?",
        "Desired base salary",
    ],
)
def test_expected_earnings_and_desired_annual_compensation_match_desired_salary(question: str):
    assert concept_matcher.match_concept(question) == "DESIRED_SALARY"


def test_numerical_salary_question_returns_plain_minimum_number():
    profile = SimpleNamespace(salary_min=125_000, salary_max=185_000)
    val, src = concept_matcher.resolve_profile_value(
        "DESIRED_SALARY", profile, question=INVESCO_SALARY_Q
    )
    assert val == "125000"
    assert src == "profile.salary"


def test_non_numerical_salary_question_retains_formatted_range():
    profile = SimpleNamespace(salary_min=125_000, salary_max=185_000)
    val, src = concept_matcher.resolve_profile_value(
        "DESIRED_SALARY", profile, question=ALTERA_SALARY_Q
    )
    assert val == "$125,000 - $185,000"
    assert src == "profile.salary"


def _make_agent(needs_sponsorship):
    ag = page_agent.PageAgent.__new__(page_agent.PageAgent)
    ag.profile = SimpleNamespace(
        requires_visa_sponsorship=needs_sponsorship,
        legally_eligible_to_work="Yes",
        authorized_for_any_employer="Yes",
    )
    ag.assistant = SimpleNamespace(_best_option=JobApplicationAssistant._best_option)
    return ag


def test_radio_control_for_and_match_option_respect_sponsorship_profile():
    q = "Are you legally eligible to work in the country where this job is located?*"
    radios = [
        page_agent.Control(ref="r1", role="radio", name="Yes, without restrictions", group=q),
        page_agent.Control(ref="r2", role="radio", name="Yes, with company sponsorship", group=q),
        page_agent.Control(ref="r3", role="radio", name="No", group=q),
    ]
    sponsored_agent = _make_agent(True)
    chosen = sponsored_agent._control_for(q, "Yes", radios)
    assert chosen is not None and chosen.ref == "r2"

    unrestricted_agent = _make_agent(False)
    chosen_unrestricted = unrestricted_agent._control_for(q, "Yes", radios)
    assert chosen_unrestricted is not None and chosen_unrestricted.ref == "r1"

    # When only the false statement is offered for a sponsored candidate, refuse to pick it.
    only_unrestricted = [
        page_agent.Control(ref="r1", role="radio", name="Yes, without restrictions", group=q),
        page_agent.Control(ref="r3", role="radio", name="No", group=q),
    ]
    assert sponsored_agent._control_for(q, "Yes", only_unrestricted) is None
    assert sponsored_agent._match_option(["Yes, without restrictions", "No"], "Yes") is None

