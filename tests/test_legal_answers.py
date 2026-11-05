"""Tests for legal question classification, profile isolation, and EEO decline handling.

Rules under test:
1. Normal unknown questions reach the AI with full profile and resume context.
2. Sponsorship and legal questions with profile facts reach the AI with ONLY those category facts.
3. EEO questions with no profile facts select a decline/prefer-not-to-say option when available.
4. EEO questions with no profile facts and no decline option wait for user handoff.
5. The AI prompt for a legal question holds no other candidate profile data or resume text.
6. Answers record where each answer came from: profile / rule / ai / ai_legal / declined.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from config import UserProfile
import legal_questions
from claude_integration import ClaudeClient


def test_classify_legal_question_categories():
    """All 9 required categories are classified correctly."""
    assert legal_questions.classify_legal_question("Are you legally authorized to work in the US?") == "work_authorization"
    assert legal_questions.classify_legal_question("Will you now or in the future require visa sponsorship?") == "sponsorship"
    assert legal_questions.classify_legal_question("Are you a US citizen?") == "citizenship"
    assert legal_questions.classify_legal_question("What is your current country of residence?") == "residency"
    assert legal_questions.classify_legal_question("Have you ever been convicted of a felony?") == "criminal_history"
    assert legal_questions.classify_legal_question("What is your gender?") == "eeo_gender"
    assert legal_questions.classify_legal_question("What is your race and ethnicity?") == "eeo_race"
    assert legal_questions.classify_legal_question("Please select your protected veteran status") == "eeo_veteran"
    assert legal_questions.classify_legal_question("Do you have a physical or mental disability?") == "eeo_disability"


def test_normal_unknown_question_is_not_legal():
    """Arbitrary domain/experience questions are not classified as legal."""
    assert legal_questions.classify_legal_question("What is your favorite programming language?") is None
    assert legal_questions.classify_legal_question("Why are you interested in this role?") is None


def test_find_decline_option():
    """Detects decline options regardless of casing or surrounding punctuation."""
    options = ["Male", "Female", "Prefer not to say"]
    assert legal_questions.find_decline_option(options) == "Prefer not to say"

    options2 = ["Asian", "White", "Decline to state", "Two or more races"]
    assert legal_questions.find_decline_option(options2) == "Decline to state"

    options_none = ["Yes", "No"]
    assert legal_questions.find_decline_option(options_none) is None


def test_category_facts_extraction():
    """Extracts ONLY fields belonging to that category."""
    profile = UserProfile(
        full_name="Jane Doe",
        email="jane@example.com",
        requires_visa_sponsorship=False,
        gender="",
        veteran_status="Not a veteran",
    )
    # Sponsorship category has requires_visa_sponsorship
    facts = legal_questions.get_category_facts("sponsorship", profile)
    assert facts == {"requires_visa_sponsorship": False}

    # EEO gender has no value set -> empty dict
    gender_facts = legal_questions.get_category_facts("eeo_gender", profile)
    assert gender_facts == {}

    # EEO veteran has veteran_status
    veteran_facts = legal_questions.get_category_facts("eeo_veteran", profile)
    assert veteran_facts == {"veteran_status": "Not a veteran"}


def test_ai_prompt_for_legal_question_holds_no_other_profile_data():
    """The AI prompt for a legal question contains ONLY category facts, no name, no resume, etc."""
    profile = UserProfile(
        full_name="Jane Doe",
        email="jane@example.com",
        current_location="Boston, MA",
        target_titles=("Network Engineer",),
        requires_visa_sponsorship=False,
    )
    client = ClaudeClient(MagicMock(anthropic_api_key="test-key"))
    captured_messages = []

    def mock_call_json(system, user_message, max_tokens=600):
        captured_messages.append({"system": system, "user_message": user_message})
        return {"answer": "No"}

    client._call_json = mock_call_json

    ans = client.answer_single_question(
        question="Will you require visa sponsorship?",
        options=["Yes", "No"],
        resume_text="Extensive Python and BGP experience at Acme Corp...",
        profile=profile,
        job_title="Senior Engineer",
        company="BigCorp",
    )

    assert ans == "No"
    assert len(captured_messages) == 1
    prompt = captured_messages[0]["user_message"]

    # Must contain category fact
    assert "requires_visa_sponsorship: False" in prompt

    # Must NOT contain personal identifying or resume details
    assert "Jane Doe" not in prompt
    assert "jane@example.com" not in prompt
    assert "Boston, MA" not in prompt
    assert "Extensive Python and BGP" not in prompt
    assert "Acme Corp" not in prompt


def test_normal_unknown_question_reaches_ai():
    """A normal question gets full resume and profile context in the prompt."""
    profile = UserProfile(
        full_name="Jane Doe",
        email="jane@example.com",
        current_location="Boston, MA",
    )
    client = ClaudeClient(MagicMock(anthropic_api_key="test-key"))
    captured_messages = []

    def mock_call_json(system, user_message, max_tokens=600):
        captured_messages.append({"system": system, "user_message": user_message})
        return {"answer": "Python"}

    client._call_json = mock_call_json

    ans = client.answer_single_question(
        question="What programming language do you prefer?",
        options=["Python", "Go", "Java"],
        resume_text="10 years writing Python",
        profile=profile,
        job_title="Backend Engineer",
        company="TechCorp",
    )

    assert ans == "Python"
    assert len(captured_messages) == 1
    prompt = captured_messages[0]["user_message"]
    assert "Jane Doe" in prompt
    assert "10 years writing Python" in prompt


def test_eeo_question_with_no_profile_fact_picks_decline():
    """EEO question with empty profile selects decline without calling the model."""
    profile = UserProfile(gender="")
    client = ClaudeClient(MagicMock(anthropic_api_key="test-key"))
    client._call_json = MagicMock(side_effect=AssertionError("AI model should not be called"))

    ans = client.answer_single_question(
        question="What is your gender?",
        options=["Male", "Female", "I prefer not to say"],
        profile=profile,
    )
    assert ans == "I prefer not to say"


def test_eeo_question_with_no_profile_fact_and_no_decline_waits():
    """EEO question with no profile fact and no decline option returns empty string (waits)."""
    profile = UserProfile(gender="")
    client = ClaudeClient(MagicMock(anthropic_api_key="test-key"))
    client._call_json = MagicMock(side_effect=AssertionError("AI model should not be called"))

    ans = client.answer_single_question(
        question="What is your gender?",
        options=["Male", "Female"],
        profile=profile,
    )
    assert ans == ""


def test_sponsorship_question_answers_from_profile_fact():
    """Sponsorship question with profile fact answers accurately."""
    profile = UserProfile(requires_visa_sponsorship=True)
    client = ClaudeClient(MagicMock(anthropic_api_key="test-key"))
    client._call_json = MagicMock(return_value={"answer": "Yes"})

    ans = client.answer_single_question(
        question="Will you now or in the future require visa sponsorship?",
        options=["Yes", "No"],
        profile=profile,
    )
    assert ans == "Yes"
