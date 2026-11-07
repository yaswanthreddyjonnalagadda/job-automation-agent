"""Tests for Phase 4: Semantic Answer Engine, Canonical Intents, Control Mapping, and Review Queue."""
from __future__ import annotations

import pytest

from services.matcher.answer_engine import (
    AnswerSource,
    CanonicalIntent,
    SemanticAnswerEngine,
    get_approved_seed_answers,
)


@pytest.fixture
def engine(tmp_path):
    db_file = tmp_path / "answer_engine_test.db"
    return SemanticAnswerEngine(storage_dsn=f"sqlite:///{db_file}")


def test_approved_seed_answers_are_present(engine):
    """Seed bank contains approved rules for Yaswanth Reddy Jonnalagadda."""
    seeds = get_approved_seed_answers()
    assert seeds[CanonicalIntent.FIRST_NAME] == "Yaswanth Reddy"
    assert seeds[CanonicalIntent.LAST_NAME] == "Jonnalagadda"
    assert seeds[CanonicalIntent.PHONE_TYPE] == "Mobile"
    assert seeds[CanonicalIntent.COUNTY] == "Fairfax"
    assert seeds[CanonicalIntent.PRIOR_EMPLOYMENT] == "No"
    assert seeds[CanonicalIntent.RELATIVES_EMPLOYED] == "No"
    assert seeds[CanonicalIntent.REFERRAL_SOURCE] == "LinkedIn"
    assert seeds[CanonicalIntent.AGE_18_OR_OLDER] == "Yes"
    assert seeds[CanonicalIntent.WEEKENDS_HOLIDAYS] == "Yes"
    assert seeds[CanonicalIntent.START_DATE] == "Immediately"
    assert seeds[CanonicalIntent.MINIMUM_SALARY] == "125000"


def test_paraphrased_questions_match_same_canonical_intent(engine):
    """Different phrasings of sponsorship and prior employment resolve to the exact same intent."""
    # Sponsorship paraphrases
    q1 = "Will you now or in the future require sponsorship for an employment visa?"
    q2 = "Do you currently or will you ever need immigration sponsorship to work in the US?"
    q3 = "Visa sponsorship requirement:"

    m1 = engine.resolve_question(q1)
    m2 = engine.resolve_question(q2)
    m3 = engine.resolve_question(q3)

    assert m1.intent == CanonicalIntent.SPONSORSHIP
    assert m2.intent == CanonicalIntent.SPONSORSHIP
    assert m3.intent == CanonicalIntent.SPONSORSHIP
    assert m1.answer == "Yes"
    assert m2.answer == "Yes"
    assert m3.answer == "Yes"

    # Prior employment paraphrases
    p1 = "Have you ever worked for Cisco or any of its subsidiaries in the past?"
    p2 = "Are you a former employee of this company?"
    pm1 = engine.resolve_question(p1)
    pm2 = engine.resolve_question(p2)
    assert pm1.intent == CanonicalIntent.PRIOR_EMPLOYMENT
    assert pm2.intent == CanonicalIntent.PRIOR_EMPLOYMENT
    assert pm1.answer == "No"
    assert pm2.answer == "No"


def test_semantic_option_control_mapping(engine):
    """Maps canonical answer to the closest semantic option widget."""
    # Canonical answer is "LinkedIn", options do not include "LinkedIn" directly but have "Social Media"
    options = ["Company Careers Website", "Employee Referral", "Social Media / Job Board", "Other"]
    resolved = engine.resolve_question_for_control(
        question="Where did you hear about this job opening?",
        control_type="select",
        options=options,
    )
    assert resolved.selected_option == "Social Media / Job Board"
    assert resolved.source == AnswerSource.BANK


def test_missing_facts_queue_in_review_never_guessed(engine):
    """
    Uncovered questions requiring facts not in profile (e.g. supervisor phone, secret references)
    must NEVER be guessed or fabricated. They are placed in the review queue and the application is paused.
    """
    sensitive_question = "Please provide your previous supervisor's direct cell phone number and relationship:"
    resolved = engine.resolve_question(
        question=sensitive_question,
        application_id=42,
        company_name="DefenseNet",
        job_title="Senior Network Engineer",
    )
    assert resolved.needs_user_review is True
    assert resolved.answer is None  # Never fabricated!

    # Verify placed in review queue
    queued = engine.get_pending_review_items()
    assert len(queued) == 1
    assert queued[0]["application_id"] == 42
    assert queued[0]["company"] == "DefenseNet"
    assert "supervisor" in queued[0]["raw_question"].lower()


def test_resolving_review_item_learns_and_resumes(engine):
    """When user answers a review queue question, it saves to the bank and marks the review item approved."""
    engine.resolve_question(
        question="What is your preferred data center shift?",
        application_id=99,
        company_name="Equinix",
        job_title="Data Center Network Engineer",
    )
    queued = engine.get_pending_review_items()
    review_id = queued[0]["id"]

    # User submits approved answer
    engine.submit_user_answer(
        review_id=review_id,
        user_answer="Day Shift (First Shift)",
        save_as_reusable=True,
    )

    # Queue should now be empty
    assert len(engine.get_pending_review_items()) == 0

    # Asking the question again should now answer from user-approved bank
    follow_up = engine.resolve_question("Which data center shift schedule do you prefer?")
    assert follow_up.answer == "Day Shift (First Shift)"
    assert follow_up.source == AnswerSource.USER_PROVIDED
