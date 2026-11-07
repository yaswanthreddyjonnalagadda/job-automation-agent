"""Phase 5: Replay 30+ varied form questions across ATS portals to verify Answer Engine accuracy."""
from __future__ import annotations

import pytest

from services.matcher.answer_engine import CanonicalIntent, SemanticAnswerEngine


def test_replay_35_varied_questions(tmp_path):
    engine = SemanticAnswerEngine(storage_dsn=f"sqlite:///{tmp_path}/replay.db")

    # (Question, Expected Intent, Expected Answer or Review)
    test_cases = [
        # Sponsorship variations
        ("Are you legally authorized to work in the United States?", CanonicalIntent.WORK_AUTHORIZATION, "Yes"),
        ("Do you currently have the legal right to work in the US?", CanonicalIntent.WORK_AUTHORIZATION, "Yes"),
        ("Are you eligible to work in the country of this posting?", CanonicalIntent.WORK_AUTHORIZATION, "Yes"),
        ("Will you now or in the future require visa sponsorship?", CanonicalIntent.SPONSORSHIP, "Yes"),
        ("Do you require sponsorship for an employment visa (e.g. H-1B)?", CanonicalIntent.SPONSORSHIP, "Yes"),
        ("Will you at any point require immigration sponsorship?", CanonicalIntent.SPONSORSHIP, "Yes"),
        ("Are you seeking visa sponsorship now or in the future?", CanonicalIntent.SPONSORSHIP, "Yes"),
        
        # Prior employment variations
        ("Have you ever been employed by this company?", CanonicalIntent.PRIOR_EMPLOYMENT, "No"),
        ("Are you a former employee of Cisco or its affiliates?", CanonicalIntent.PRIOR_EMPLOYMENT, "No"),
        ("Have you previously worked for our organization in any capacity?", CanonicalIntent.PRIOR_EMPLOYMENT, "No"),
        ("Have you ever worked for us in the past?", CanonicalIntent.PRIOR_EMPLOYMENT, "No"),

        # Relatives employed variations
        ("Do you have any relatives or family members currently working here?", CanonicalIntent.RELATIVES_EMPLOYED, "No"),
        ("Are any of your immediate family members employed by this company?", CanonicalIntent.RELATIVES_EMPLOYED, "No"),
        ("Do you have any personal relationship with any current employee?", CanonicalIntent.RELATIVES_EMPLOYED, "No"),

        # Referral source variations
        ("How did you hear about this career opportunity?", CanonicalIntent.REFERRAL_SOURCE, "LinkedIn"),
        ("Where did you first learn of this position?", CanonicalIntent.REFERRAL_SOURCE, "LinkedIn"),
        ("What source referred you to this job posting?", CanonicalIntent.REFERRAL_SOURCE, "LinkedIn"),
        ("Referral source:", CanonicalIntent.REFERRAL_SOURCE, "LinkedIn"),

        # Age & Eligibility variations
        ("Are you at least 18 years of age?", CanonicalIntent.AGE_18_OR_OLDER, "Yes"),
        ("Are you 18 years of age or older?", CanonicalIntent.AGE_18_OR_OLDER, "Yes"),
        ("Are you of legal working age (18+)?", CanonicalIntent.AGE_18_OR_OLDER, "Yes"),

        # Work preferences & availability
        ("Are you seeking full-time employment?", CanonicalIntent.FULL_TIME, "Full Time"),
        ("Desired employment type: Full-time or Part-time?", CanonicalIntent.FULL_TIME, "Full Time"),
        ("When are you available to start working?", CanonicalIntent.START_DATE, "Immediately"),
        ("What is your earliest possible start date?", CanonicalIntent.START_DATE, "Immediately"),
        ("Are you willing to work overtime, weekends, or holidays if needed?", CanonicalIntent.WEEKENDS_HOLIDAYS, "Yes"),
        ("Are you willing to work weekends and holidays?", CanonicalIntent.WEEKENDS_HOLIDAYS, "Yes"),
        ("Are you willing to relocate for this position?", CanonicalIntent.RELOCATION, "Yes"),
        ("Open to relocation?", CanonicalIntent.RELOCATION, "Yes"),
        ("What are your annual salary expectations?", CanonicalIntent.MINIMUM_SALARY, "125000"),
        ("Minimum desired compensation:", CanonicalIntent.MINIMUM_SALARY, "125000"),

        # Sensitive questions that MUST route to Review Queue (needs_user_review=True, answer=None)
        ("Please list your previous direct supervisor's mobile phone number:", None, None),
        ("Provide names and phone numbers for three professional references:", None, None),
        ("What is your preference between shift work, rotating shifts, or fixed day shift?", None, None),
        ("Do you hold an active Top Secret / SCI government security clearance?", None, None),
    ]

    assert len(test_cases) == 35

    review_queue_count = 0
    resolved_count = 0

    for q, expected_intent, expected_ans in test_cases:
        res = engine.resolve_question(q)
        if expected_intent is not None:
            assert res.intent == expected_intent, f"Failed on '{q}': got {res.intent}"
            assert res.answer == expected_ans, f"Failed answer on '{q}': got {res.answer}"
            assert res.confidence >= 0.85
            resolved_count += 1
        else:
            assert res.needs_user_review is True, f"Failed review queue on '{q}'"
            assert res.answer is None  # Never guessed!
            review_queue_count += 1

    assert resolved_count == 31
    assert review_queue_count == 4

