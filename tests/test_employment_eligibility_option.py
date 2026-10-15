"""'Please select the appropriate option describing your employment eligibility' (Copart, Workday).

The list offers three statements -- authorized permanently / an active temporary work permit / I require sponsorship
before I am able to work. The agent left it for the owner every time: no concept matched the question, and a legal
statement is only ever written when the profile states it outright (f152). The owner has now stated it: a profile that
needs sponsorship chooses the sponsorship statement. A profile that does not still gets nothing here -- the agent
does not pick 'permanent' or 'temporary' for anyone on its own reading.
"""
from types import SimpleNamespace

import concept_matcher
import safety

QUESTION = "Please select the appropriate option describing your employment eligibility."
OPTIONS = ["Select One", "I am authorized to work permanently in the country.", "I have an active temporary work permit.",
           "I require sponsorship before I am able to work."]


def profile(**facts):
    return SimpleNamespace(**{"requires_visa_sponsorship": False, **facts})


def test_the_question_is_recognised():
    assert concept_matcher.match_concept(QUESTION) == "EMPLOYMENT_ELIGIBILITY"


def test_a_profile_that_needs_sponsorship_chooses_the_sponsorship_statement():
    value, source = concept_matcher.resolve_profile_value(
        "EMPLOYMENT_ELIGIBILITY", profile(requires_visa_sponsorship=True), OPTIONS, QUESTION)
    assert value == "I require sponsorship before I am able to work."
    assert source == "profile.requires_visa_sponsorship"


def test_the_statement_is_given_even_before_the_list_has_been_read():
    value, _ = concept_matcher.resolve_profile_value(
        "EMPLOYMENT_ELIGIBILITY", profile(requires_visa_sponsorship=True), None, QUESTION)
    assert value == "I require sponsorship before I am able to work"


def test_a_profile_that_does_not_need_sponsorship_is_left_for_the_owner():
    for facts in (profile(), profile(work_authorization="US Citizen"), profile(requires_visa_sponsorship="No")):
        value, _ = concept_matcher.resolve_profile_value("EMPLOYMENT_ELIGIBILITY", facts, OPTIONS, QUESTION)
        assert value == "", facts


def test_the_neighbouring_legal_questions_keep_their_own_concepts():
    assert concept_matcher.match_concept("Are you legally authorized to work in the United States?") == "WORK_AUTHORIZATION"
    assert concept_matcher.match_concept("Will you now or in the future require visa sponsorship?") == "VISA_SPONSORSHIP"


def test_the_chosen_statement_is_not_flagged_as_contradicting_the_profile():
    fields = [{"label": QUESTION, "value": "I require sponsorship before I am able to work."}]
    needs = SimpleNamespace(requires_visa_sponsorship=True, legally_eligible_to_work="Yes")
    assert safety.legal_answer_conflicts(fields, needs) == []
