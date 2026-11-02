"""A bare 'Yes' among 'Yes, without restrictions' and 'Yes, with company sponsorship' follows the profile (f184).

Invesco (Workday): 'Are you legally eligible to work in the country where this job is located?' offers Select One / Yes,
without restrictions / Yes, with company sponsorship / No. The agent's answer was 'Yes', which fits both rows, so it
refused to guess and stopped. Worse, the shared authorization rule resolved that question to 'Yes, without restrictions'
for an owner who needs sponsorship -- a false statement the agent only avoided because the list was closed when it chose.
An owner who needs sponsorship is 'Yes, with company sponsorship'; one who does not is 'Yes, without restrictions'; and
if the profile is silent, or only the false row is offered, the question stays the owner's.
"""
from types import SimpleNamespace

import pytest

import concept_matcher
import option_match
import page_agent

QUESTION = "Are you legally eligible to work in the country where this job is located?*"
INVESCO = ["Select One", "Yes, without restrictions", "Yes, with company sponsorship", "No"]


@pytest.mark.parametrize("needs, options, expected", [
    (True, INVESCO, 2),                                                    # needs sponsorship: the sponsored row
    (False, INVESCO, 1),                                                   # does not: the unrestricted row
    (True, ["Yes, without restrictions", "No"], None),                     # only the false row: never taken
    (False, ["Yes, with company sponsorship", "No"], None),                # only the sponsored row: not the owner's
    (None, INVESCO, None),                                                 # the profile says nothing: never assumed
])
def test_the_sponsorship_rows_follow_the_profile(needs, options, expected):
    decided, index = option_match.sponsorship_choice(options, "Yes", needs)
    assert decided and index == expected


@pytest.mark.parametrize("options", [
    ["Yes", "No"],
    ["Select One", "Yes, I am authorized to work in the US", "No"],
    ["Yes, willing to travel", "Yes, with notice", "No"],
])
def test_lists_that_do_not_speak_of_sponsorship_are_left_to_the_usual_matcher(options):
    assert option_match.sponsorship_choice(options, "Yes", True) == (False, None)


def test_only_a_bare_yes_is_decided_this_way():
    assert option_match.sponsorship_choice(INVESCO, "No", True) == (False, None)
    assert option_match.sponsorship_choice(INVESCO, "Yes, with company sponsorship", True) == (False, None)


def profile(needs):
    return SimpleNamespace(requires_visa_sponsorship=needs, legally_eligible_to_work="Yes", authorized_for_any_employer="Yes")


def test_the_authorization_rule_never_answers_without_restrictions_for_an_owner_who_needs_sponsorship():
    value, _ = concept_matcher.resolve_profile_value("WORK_AUTHORIZATION", profile(True), INVESCO, QUESTION)
    assert value == "Yes, with company sponsorship"
    value, _ = concept_matcher.resolve_profile_value("WORK_AUTHORIZATION", profile(False), INVESCO, QUESTION)
    assert value == "Yes, without restrictions"
    value, _ = concept_matcher.resolve_profile_value("WORK_AUTHORIZATION", profile(True),
                                                     ["Select One", "Yes, without restrictions", "No"], QUESTION)
    assert value == ""


def test_plain_yes_no_authorization_is_unchanged():
    value, _ = concept_matcher.resolve_profile_value("WORK_AUTHORIZATION", profile(True), ["Yes", "No"], QUESTION)
    assert value == "Yes"


def agent_with(profile_obj):
    a = page_agent.PageAgent.__new__(page_agent.PageAgent)
    a.profile = profile_obj
    a.assistant = SimpleNamespace(_best_option=lambda names, values: None)
    return a


def test_the_agent_picks_the_row_its_profile_supports():
    offered = [(str(i), t) for i, t in enumerate(INVESCO)]
    assert agent_with(profile(True))._pick(offered, "Yes") == 2
    assert agent_with(profile(False))._pick(offered, "Yes") == 1
    assert agent_with(SimpleNamespace())._pick(offered, "Yes") is None       # a profile that never said: left for the owner
