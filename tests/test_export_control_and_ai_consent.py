"""Export control, foreign residency, J-1 visa history, and AI screening consent (f333).

Analog Devices Inc., 4 November 2026: The application stopped on Step 3 (Application Questions) with five
unanswered questions:
1) Export control Group D country citizenship check (countries listed in container prose)
2) Permanent resident status in a country outside the US
3) J-1 / J-2 exchange visitor visa history
4) AI tools recruitment evaluation consent
5) AI tools future job matching consent

All five are now answered deterministically from the profile and concepts with zero model calls.
"""
import pytest

import answer_engine as ae
import concept_matcher as cm
import config


def test_group_d_export_control_country_list_is_answered_no():
    profile = config.UserProfile(country_of_citizenship="India", country="United States")
    q = "Are you a citizen of any of the following countries: Group D ("
    container = (
        "Are you a citizen of any of the following countries: Group D (Armenia, Azerbaijan, Belarus, Burma (Myanmar), "
        "Cambodia, China (PRC), Georgia, Hong Kong, Iraq, Kazakhstan, Kyrgyzstan, Laos, Libya, Macau, Moldova, Mongolia, "
        "Nicaragua, Russia, Tajikistan, Turkmenistan, Ukraine (RU Occupied regions, Donetsk, Kherson, Luhansk, Zaporizhzhia), "
        "Uzbekistan, Venezuela, Vietnam)*"
    )
    ask = ae.Ask(question=q, container=container, options=["Yes", "No"], role="combobox", profile=profile)
    val, src = ae.answer(ask)
    assert val == "No"
    assert src == "profile.country_of_citizenship"


def test_foreign_permanent_resident_is_answered_no():
    profile = config.UserProfile(foreign_permanent_resident="No", country_of_citizenship="India", country="United States")
    q = "2. Do you hold Legal Permanent Resident Status in a country outside the US?"
    ask = ae.Ask(question=q, options=["Yes", "No"], role="combobox", profile=profile)
    val, src = ae.answer(ask)
    assert val == "No"
    assert src == "profile.foreign_permanent_resident"

    # Also test concept matcher
    concept = cm.match_concept(q)
    assert concept == "FOREIGN_PERMANENT_RESIDENT"
    matched_val, matched_src = cm.resolve_profile_value(concept, profile, ["Yes", "No"], q)
    assert matched_val == "No"
    assert matched_src == "profile.foreign_permanent_resident"


def test_j1_exchange_visitor_visa_history_is_answered_no():
    profile = config.UserProfile(held_j1_visa="No", work_authorization="H-1B")
    q = "If Yes, have you ever held a J-1 or J-2 exchange visitor visa?"
    ask = ae.Ask(question=q, options=["Yes", "No"], role="combobox", profile=profile)
    val, src = ae.answer(ask)
    assert val == "No"
    assert src == "profile.held_j1_visa"

    # Also test concept matcher
    concept = cm.match_concept(q)
    assert concept == "HELD_J1_VISA"
    matched_val, matched_src = cm.resolve_profile_value(concept, profile, ["Yes", "No"], q)
    assert matched_val == "No"
    assert matched_src == "profile.held_j1_visa"


def test_ai_recruitment_screening_consent_is_answered_yes():
    profile = config.UserProfile(ai_recruitment_consent="Yes", accept_application_privacy_prompts=True)
    q = (
        "To support an efficient recruitment process, ADI may use artificial intelligence (AI) tools to assess how your "
        "candidate information aligns with the role requirements. These tools do not make decisions about a candidate’s "
        "status — every decision that leads ADI to select a candidate for this role involves human review or intervention. "
        "You have the right to choose whether your data is processed using AI tools: By selecting “Yes,” you consent to "
        "the use of AI tools to evaluate your candidate information. By selecting “No,” you decline consent."
    )
    ask = ae.Ask(question=q, options=["Yes", "No"], role="combobox", profile=profile)
    val, src = ae.answer(ask)
    assert val == "Yes"
    assert src == "profile.ai_recruitment_consent"

    concept = cm.match_concept(q)
    assert concept == "AI_RECRUITMENT_CONSENT"
    matched_val, matched_src = cm.resolve_profile_value(concept, profile, ["Yes", "No"], q)
    assert matched_val == "Yes"
    assert matched_src == "profile.ai_recruitment_consent"


def test_ai_job_matching_consent_is_answered_yes():
    profile = config.UserProfile(ai_recruitment_consent="Yes")
    q = (
        "ADI would like to consider you for additional and future job openings using AI tools that may compare your "
        "application and other relevant information to the requirements of those job openings. If a match is found, "
        "our recruiters may contact you about these opportunities. Your consent is voluntary and will not affect your "
        "current application. By selecting \"Yes,\" you consent to the use of your data for this purpose."
    )
    ask = ae.Ask(question=q, options=["Yes", "No"], role="combobox", profile=profile)
    val, src = ae.answer(ask)
    assert val == "Yes"
    assert src == "profile.ai_recruitment_consent"

    concept = cm.match_concept(q)
    assert concept == "AI_RECRUITMENT_CONSENT"
    matched_val, matched_src = cm.resolve_profile_value(concept, profile, ["Yes", "No"], q)
    assert matched_val == "Yes"
    assert matched_src == "profile.ai_recruitment_consent"


def test_unset_profile_decisions_are_never_guessed():
    bare_profile = config.UserProfile()
    assert getattr(bare_profile, "foreign_permanent_resident") == ""
    assert getattr(bare_profile, "held_j1_visa") == ""
    assert getattr(bare_profile, "ai_recruitment_consent") == ""

    # With empty profile and no privacy fallback, AI recruitment question stays unanswered
    q_ai = "Do you consent to AI evaluating your application?"
    val, src = ae.answer(ae.Ask(question=q_ai, options=["Yes", "No"], role="combobox", profile=bare_profile))
    assert val == ""


def test_empty_residency_and_j1_facts_are_left_for_the_owner_not_answered_no():
    bare = config.UserProfile(country_of_citizenship="India", country="United States")
    for q in ("2. Do you hold Legal Permanent Resident Status in a country outside the US?",
              "If Yes, have you ever held a J-1 or J-2 exchange visitor visa?"):
        val, _src = ae.answer(ae.Ask(question=q, options=["Yes", "No"], role="combobox", profile=bare))
        assert val == ""


def test_owner_yes_for_foreign_permanent_residency_is_answered_yes():
    profile = config.UserProfile(foreign_permanent_resident="Yes", country_of_citizenship="India")
    q = "2. Do you hold Legal Permanent Resident Status in a country outside the US?"
    val, src = ae.answer(ae.Ask(question=q, options=["Yes", "No"], role="combobox", profile=profile))
    assert (val, src) == ("Yes", "profile.foreign_permanent_resident")


def test_general_privacy_permission_does_not_decide_ai_screening_consent():
    profile = config.UserProfile(accept_application_privacy_prompts=True)
    q = "Do you consent to AI tools evaluating your candidate information?"
    val, _src = ae.answer(ae.Ask(question=q, options=["Yes", "No"], role="combobox", profile=profile))
    assert val == ""
