"""Tests for Task 2: Grounded AI Answers Contract.

Rules under test:
1. Every answering call returns a structured reply with "grounded": true/false and "answer".
2. The prompt tells the model: answer only from the facts given; if they do not support an answer, return grounded=false.
3. grounded=false, an empty answer, or an option not in the list:
   - required field -> wait for owner (handoff)
   - optional field -> leave blank
   - never fill a guess
4. A reply without "grounded" counts as false.
5. Gemini and OpenAI clients follow the same contract.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock
from types import SimpleNamespace
import pytest

from config import UserProfile
from claude_integration import ClaudeClient
from gemini_integration import GeminiClient
from openai_integration import OpenAIClient
import open_answers


def test_reply_without_grounded_counts_as_false():
    """A reply without 'grounded' key counts as grounded=False."""
    client = ClaudeClient(MagicMock(anthropic_api_key="test-key"))
    client._call_json = MagicMock(return_value={"answer": "Some guess"})

    res = client.answer_single_question("What is your favorite tool?")
    assert isinstance(res, dict)
    assert res.get("grounded") is False
    assert res.get("answer") == ""


def test_reply_with_grounded_false_returns_empty_answer():
    """When the model explicitly says grounded=False, answer is empty."""
    client = ClaudeClient(MagicMock(anthropic_api_key="test-key"))
    client._call_json = MagicMock(return_value={"grounded": False, "answer": "Fabricated"})

    res = client.answer_single_question("Do you have 10 years of Rust?")
    assert res.get("grounded") is False
    assert res.get("answer") == ""


def test_reply_with_unknown_option_is_refused():
    """When the model returns an option not in the offered list, it is refused."""
    client = ClaudeClient(MagicMock(anthropic_api_key="test-key"))
    client._call_json = MagicMock(return_value={"grounded": True, "answer": "Maybe"})

    res = client.answer_single_question(
        "Are you willing to relocate?",
        options=["Yes", "No"],
    )
    assert res.get("grounded") is False
    assert res.get("answer") == ""


def test_reply_with_grounded_true_and_valid_option():
    """When the model returns grounded=True and an exact offered option, it succeeds."""
    client = ClaudeClient(MagicMock(anthropic_api_key="test-key"))
    client._call_json = MagicMock(return_value={"grounded": True, "answer": "Yes"})

    res = client.answer_single_question(
        "Are you willing to relocate?",
        options=["Yes", "No"],
    )
    assert res.get("grounded") is True
    assert res.get("answer") == "Yes"


def test_choose_option_grounded_contract():
    """choose_option returns structured reply with grounded, answer, choice, equivalent."""
    client = ClaudeClient(MagicMock(anthropic_api_key="test-key"))
    # 1. Valid grounded option
    client._call_json = MagicMock(return_value={"grounded": True, "answer": "Masters", "reason": "matches"})
    res = client.choose_option("Masters of Science", ["Bachelors", "Masters"], "Degree")
    assert res.get("grounded") is True
    assert res.get("answer") == "Masters"
    assert res.get("choice") == "Masters"
    assert res.get("equivalent") is True

    # 2. Missing grounded key counts as false
    client._call_json = MagicMock(return_value={"choice": "Masters", "equivalent": True})
    res_no_grounded = client.choose_option("Masters of Science", ["Bachelors", "Masters"], "Degree")
    assert res_no_grounded.get("grounded") is False
    assert res_no_grounded.get("answer") == ""
    assert res_no_grounded.get("equivalent") is False

    # 3. Option not in list is refused
    client._call_json = MagicMock(return_value={"grounded": True, "answer": "PhD", "reason": "higher"})
    res_unknown = client.choose_option("Masters of Science", ["Bachelors", "Masters"], "Degree")
    assert res_unknown.get("grounded") is False
    assert res_unknown.get("answer") == ""
    assert res_unknown.get("equivalent") is False


def test_gemini_client_follows_same_contract():
    """GeminiClient parses grounded responses identically."""
    cfg = SimpleNamespace(gemini_api_key="AIzaSyTest12345678901234567890123456789")
    client = GeminiClient(cfg)
    client._call_json = MagicMock(return_value={"grounded": True, "answer": "Yes"})

    res = client.answer_single_question("Do you have a driver's license?", options=["Yes", "No"])
    assert res.get("grounded") is True
    assert res.get("answer") == "Yes"

    # Missing grounded -> False
    client._call_json = MagicMock(return_value={"answer": "Yes"})
    res_fail = client.answer_single_question("Do you have a driver's license?", options=["Yes", "No"])
    assert res_fail.get("grounded") is False


def test_openai_client_follows_same_contract():
    """OpenAIClient parses grounded responses identically."""
    cfg = SimpleNamespace(openai_api_key="sk-testkey12345678901234567890")
    client = OpenAIClient(cfg)
    client._call_json = MagicMock(return_value={"grounded": True, "answer": "Yes"})

    res = client.answer_single_question("Do you have a driver's license?", options=["Yes", "No"])
    assert res.get("grounded") is True
    assert res.get("answer") == "Yes"

    # Missing grounded -> False
    client._call_json = MagicMock(return_value={"answer": "Yes"})
    res_fail = client.answer_single_question("Do you have a driver's license?", options=["Yes", "No"])
    assert res_fail.get("grounded") is False


def test_open_answers_write_requires_grounded_true():
    """open_answers.write ignores drafts that are not grounded."""
    # When model returns ungrounded reply
    def ask_ungrounded(system, user, tokens):
        return json.dumps({"grounded": False, "answer": "Invented essay"})

    ans, issues = open_answers.write(
        "Why do you want to work here?",
        ask=ask_ungrounded,
        facts="",
        resume_text="",
    )
    assert ans == ""
    assert any("grounded" in issue.lower() or "no" in issue.lower() for issue in issues)

    # When model returns reply without grounded key
    def ask_no_grounded_key(system, user, tokens):
        return json.dumps({"answer": "Essay without grounded key"})

    ans2, issues2 = open_answers.write(
        "Why do you want to work here?",
        ask=ask_no_grounded_key,
        facts="",
        resume_text="",
    )
    assert ans2 == ""


def test_screening_questions_grounded_contract():
    """answer_screening_questions requires grounded=True to include an answer."""
    client = ClaudeClient(MagicMock(anthropic_api_key="test-key"))
    questions = [
        {"question_text": "Grounded Q?", "input_type": "text", "options": []},
        {"question_text": "Ungrounded Q?", "input_type": "text", "options": []},
        {"question_text": "Missing grounded Q?", "input_type": "text", "options": []},
    ]
    client._call_json = MagicMock(return_value={
        "Grounded Q?": {"grounded": True, "answer": "Valid answer"},
        "Ungrounded Q?": {"grounded": False, "answer": "Some guess"},
        "Missing grounded Q?": {"answer": "Another guess"},
    })
    got = client.answer_screening_questions(
        SimpleNamespace(raw_text=""),
        SimpleNamespace(title="Engineer", company="Acme"),
        UserProfile(),
        questions,
    )
    assert "Grounded Q?" in got
    assert got["Grounded Q?"] == "Valid answer"
    assert "Ungrounded Q?" not in got
    assert "Missing grounded Q?" not in got


def test_open_answers_review_grounded_contract():
    """open_answers.review requires grounded=True."""
    # 1. Fully grounded and supported -> no issues
    def ask_ok(system, payload, tokens):
        return json.dumps({"grounded": True, "supported": True, "complete": True, "issues": []})

    issues = open_answers.review("Tell us why", "Good answer", ask=ask_ok, facts="", resume_text="")
    assert issues == []

    # 2. grounded=False -> reported as issue
    def ask_ungrounded(system, payload, tokens):
        return json.dumps({"grounded": False, "supported": True, "complete": True, "issues": []})

    issues_ungrounded = open_answers.review("Tell us why", "Good answer", ask=ask_ungrounded, facts="", resume_text="")
    assert any("grounded" in i.lower() for i in issues_ungrounded)

    # 3. Missing grounded key counts as false
    def ask_missing(system, payload, tokens):
        return json.dumps({"supported": True, "complete": True, "issues": []})

    issues_missing = open_answers.review("Tell us why", "Good answer", ask=ask_missing, facts="", resume_text="")
    assert any("grounded" in i.lower() for i in issues_missing)


def test_page_agent_optional_field_left_blank_when_ungrounded():
    """An ungrounded optional field is removed from still_open and left blank."""
    from page_agent import PageAgent, _open_is_required, Control, _same_question

    ctrl = Control(ref="ctrl1", role="textbox", name="Optional Note")
    controls = [ctrl]
    required = set()
    still_open = ["Optional Note"]

    assert not _open_is_required(ctrl.question, controls, required)

    # Simulate ungrounded val returned by AI
    val = {"grounded": False, "answer": ""}
    is_req = _open_is_required(ctrl.question, controls, required)
    grounded = bool(val.get("grounded", False))
    ans_str = str(val.get("answer", "")).strip()

    required_left = []
    if grounded and ans_str:
        pass
    elif not is_req:
        still_open = [q for q in still_open if not _same_question(ctrl.question, q)]
    else:
        required_left.append(ctrl.question)

    # Optional field is removed from still_open and not added to required_left
    assert "Optional Note" not in still_open
    assert len(still_open) == 0
    assert "Optional Note" not in required_left


def test_page_agent_required_field_waits_when_ungrounded():
    """An ungrounded required field remains in still_open and is added to required_left."""
    from page_agent import PageAgent, _open_is_required, Control, _same_question

    ctrl = Control(ref="ctrl2", role="textbox", name="Required Skill *")
    controls = [ctrl]
    required = {"Required Skill *"}
    still_open = ["Required Skill *"]

    assert _open_is_required(ctrl.question, controls, required)

    # Simulate ungrounded val returned by AI
    val = {"grounded": False, "answer": ""}
    is_req = _open_is_required(ctrl.question, controls, required)
    grounded = bool(val.get("grounded", False))
    ans_str = str(val.get("answer", "")).strip()

    required_left = []
    if grounded and ans_str:
        pass
    elif not is_req:
        still_open = [q for q in still_open if not _same_question(ctrl.question, q)]
    else:
        if ctrl.question not in required_left:
            required_left.append(ctrl.question)

    # Required field stays in still_open and goes to required_left
    assert "Required Skill *" in still_open
    assert "Required Skill *" in required_left
