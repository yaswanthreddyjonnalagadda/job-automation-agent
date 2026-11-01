"""Workday pick-one lists drawn as `button "Select One Required"` (f282).

Orion Advisor Solutions, 31 October 2026: three required dropdowns on Application Questions -- "What Orion value
resonates the most with you?", "Would you like to include a phonetic pronunciation for your name?" and "Which statement
best reflects your experience using AI tools?" -- were left for the owner. Their rows exist only once the button is
opened, so the per-question AI was asked with no options and declined each as "a personal preference"; the
full-page fallback never opened them either, because its prompt pattern did not allow Workday's trailing "Required".
"""
import json

import answer_engine as ae
import config
import page_agent
from claude_integration import ClaudeClient

PHONETIC = ("Would you like to include a phonetic pronunciation for your name? "
            "(Example: Enter A-ver-ree Shi-vawn for Avery Siobhan.)*")


def _said(question, options, profile):
    return ae.answer(ae.Ask(question=question, options=list(options), role="button", profile=profile))[0]


def test_phonetic_pronunciation_is_no_unless_the_profile_holds_one():
    assert _said(PHONETIC, [], config.UserProfile(first_name="Pat")) == "No"
    assert _said(PHONETIC, ["Yes", "No"], config.UserProfile(first_name="Pat")) == "No"
    with_one = config.UserProfile(first_name="Pat")
    object.__setattr__(with_one, "name_pronunciation", "PAT")
    assert _said(PHONETIC, ["Yes", "No"], with_one) == "Yes"


def test_select_one_required_is_a_prompt_not_a_question():
    assert page_agent._PROMPT_NAME.match("Select One Required")
    assert page_agent._PROMPT_NAME.match("Select One")
    assert not page_agent._PROMPT_NAME.match("Select One Orion value")


def test_workday_button_choices_are_read_without_the_placeholder():
    agent = object.__new__(page_agent.PageAgent)
    agent._peeked, agent._current_host = {}, "orionadvisor.wd1.myworkdayjobs.com"
    agent._open_and_read = lambda _page, _control, _label: [
        "Select One", "Create Raving Fans", "Innovate Always", "See It Through", "Support One Another"]
    control = page_agent.Control(ref="f5e1064", role="button", name="Select One Required", value="Select One",
                                 context="What Orion value resonates the most with you?*")
    assert page_agent.is_workday_choice_button(control)
    assert agent._read_button_choices(None, control) == [
        "Create Raving Fans", "Innovate Always", "See It Through", "Support One Another"]


def test_the_ai_is_told_to_pick_an_offered_preference_rather_than_decline():
    client = object.__new__(ClaudeClient)
    seen = {}

    def fake_call(system, user_message, max_tokens):
        seen["system"], seen["user"] = system, user_message
        return json.dumps({"answer": "See It Through"})

    client._call = fake_call
    options = ["Create Raving Fans", "Innovate Always", "See It Through", "Support One Another"]
    got = client.answer_single_question("What Orion value resonates the most with you?", options=options,
                                        resume_text="Network engineer.", profile=config.UserProfile(first_name="Pat"))
    assert got == "See It Through"
    assert "preference" in seen["system"] and "never one that overstates" in seen["system"]
    assert "AVAILABLE OPTIONS" in seen["user"]

