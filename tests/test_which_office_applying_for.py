"""'Which location are you applying for?' with a list of offices is answered from the places the owner can work (f287).

Bitdeer on Breezy HR, 1 November 2026: the select offered 'San Jose, CA' and 'Austin, TX'. The owner's available work
locations name Texas and not California, so the answer is Austin; nothing in the logic names a state (geo_reference
maps TX to Texas). When two offices fit, or none does, the agent chooses nothing and the owner is asked.
"""
from types import SimpleNamespace

import pytest

import answer_engine

QUESTION = "Which location are you applying for*"


def ask(options, available="Texas, Virginia, Florida", locations=()):
    profile = SimpleNamespace(available_work_locations=available, locations=list(locations))
    return answer_engine.answer(answer_engine.Ask(question=QUESTION, options=options, role="combobox", profile=profile))


def test_the_one_office_in_a_state_the_owner_can_work_is_chosen():
    assert ask(["San Jose, CA", "Austin, TX"]) == ("Austin, TX", "profile.available_work_locations")


def test_two_fitting_offices_are_left_to_the_owner():
    assert ask(["Dallas, TX", "Austin, TX"]) == ("", "")


def test_no_fitting_office_is_left_to_the_owner():
    assert ask(["San Jose, CA", "Seattle, WA"]) == ("", "")


def test_a_state_written_in_full_still_matches():
    assert ask(["San Jose, California", "Richmond, Virginia"]) == ("Richmond, Virginia", "profile.available_work_locations")
