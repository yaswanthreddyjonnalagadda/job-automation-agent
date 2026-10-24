"""Consent and availability questions follow the owner's standing decisions, in any wording (f229).

The agent used to meet each new kind of consent (an AI notetaker, sharing the resume with partners, marketing emails) as
a question of its own, and left it blank for the owner on every form. These are now concepts in reference/concepts.json
answered from profile fields the owner sets once. Empty = never guessed: the question comes to the owner.
"""
import pytest

import concept_matcher as cm
import config

NOTETAKER = [
    "As part of our interview process, we may use AI notetakers to transcribe conversations for accuracy and efficiency. "
    "Please see our candidate privacy policy for more information on how we process your data. Your decision to opt in or "
    "out of this tooling will not impact your candidacy.",
    "To ensure we can focus fully on our conversation, we use an AI tool to record and transcribe interviews. Do you consent?",
    "May we use an AI note taker during your interview?",
]
PARTNERS = [
    "Please share my resume and contact information with external Palantir partners for job seeking purposes.",
    "Do you agree that we may share your data with our affiliates and other third parties?",
    "I authorize the company to share my information with third-party partners.",
]
MARKETING = [
    "I would like to receive marketing emails and join the talent community.",
    "Would you like to receive newsletters and job alerts from us?",
    "Please add me to your talent network.",
]
CITIES = [
    "In what cities are you available to work?",
    "Which locations are you willing to work in?",
    "Where are you available to work?",
]


def owner(**kw):
    return config.UserProfile(**kw)


@pytest.mark.parametrize("question", NOTETAKER)
def test_an_ai_notetaker_question_follows_the_owners_decision(question):
    assert cm.match_concept(question) == "AI_NOTETAKER_CONSENT"
    options = ["Yes, I consent", "No, I do not consent"]
    assert cm.resolve_profile_value("AI_NOTETAKER_CONSENT", owner(ai_notetaker_consent="Yes"), options, question)[0] == "Yes, I consent"
    assert cm.resolve_profile_value("AI_NOTETAKER_CONSENT", owner(ai_notetaker_consent="No"), options, question)[0] == "No, I do not consent"


@pytest.mark.parametrize("question", PARTNERS)
def test_sharing_data_with_partners_is_never_mistaken_for_a_resume_upload(question):
    assert cm.match_concept(question) == "SHARE_DATA_WITH_PARTNERS"
    assert cm.resolve_profile_value("SHARE_DATA_WITH_PARTNERS", owner(share_data_with_partners="No"), ["Yes", "No"], question)[0] == "No"


@pytest.mark.parametrize("question", MARKETING)
def test_marketing_and_talent_community_follow_the_owners_decision(question):
    assert cm.match_concept(question) == "MARKETING_OPT_IN"
    assert cm.resolve_profile_value("MARKETING_OPT_IN", owner(marketing_opt_in="No"), ["Yes", "No"], question)[0] == "No"


@pytest.mark.parametrize("question", CITIES)
def test_where_the_owner_is_available_to_work_is_answered_with_what_they_gave(question):
    assert cm.match_concept(question) == "AVAILABLE_WORK_LOCATIONS"
    places = "Texas, Virginia, Atlanta"
    assert cm.resolve_profile_value("AVAILABLE_WORK_LOCATIONS", owner(available_work_locations=places), None, question)[0] == places


@pytest.mark.parametrize("concept, field", [("AI_NOTETAKER_CONSENT", "ai_notetaker_consent"), ("SHARE_DATA_WITH_PARTNERS", "share_data_with_partners"),
                                            ("MARKETING_OPT_IN", "marketing_opt_in"), ("AVAILABLE_WORK_LOCATIONS", "available_work_locations")])
def test_a_decision_the_owner_has_not_made_is_never_guessed(concept, field):
    assert getattr(config.UserProfile(), field) == ""
    assert cm.resolve_profile_value(concept, config.UserProfile(), ["Yes", "No"], "x")[0] == ""


def test_the_new_concepts_do_not_take_over_ordinary_questions():
    assert cm.match_concept("Upload your resume") == "RESUME_CV"
    assert cm.match_concept("Are you a resident of California?") == "RESIDES_IN"
    assert cm.match_concept("Are you available to work on weekends?") != "AVAILABLE_WORK_LOCATIONS"
    assert cm.match_concept("Can you start immediately?") not in ("MARKETING_OPT_IN", "SHARE_DATA_WITH_PARTNERS", "AI_NOTETAKER_CONSENT")
