"""
A REQUIRED preferred-name box takes the first name; an optional one stays blank (f192).

Datadog (Greenhouse), 18 October: "Preferred First Name*" is required. The profile has no preferred name, which
BLANK_MEANS_NONE reads as "there is none": the agent answered "" and _clear_none_boxes emptied the first name the
session had typed, so the form could not be sent. Two parts of one class -- what a field's "none" means when the form
requires it:
  * a required box whose profile field is blank on purpose takes the stand-in config.REQUIRED_STAND_IN names
    (a preferred name is the first name), and is never emptied;
  * "Label*" with no space before the star is a required label (required_questions read only "Label *").
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from browser_automation import JobApplicationAssistant  # noqa: E402
from page_agent import Control, PageAgent, required_questions  # noqa: E402


def agent(required=()):
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a = PageAgent(assistant, None, SimpleNamespace(), config.UserProfile(first_name="Yaswanth", preferred_name=""), None,
                  SimpleNamespace(title="Engineer", company="Example", url="https://example.com"))
    a.library_answer = lambda *x, **k: ""
    a._required_now = set(required)
    return a


@pytest.mark.parametrize("question", ["Preferred First Name*", "Preferred Name *", "* Preferred name"])
def test_a_starred_preferred_name_takes_the_first_name(question):
    assert agent().known_answer(Control("f1", "textbox", question)) == ("Yaswanth", "profile.first_name")


def test_a_preferred_name_the_page_marks_required_elsewhere_takes_the_first_name():
    a = agent(required={"Preferred First Name"})
    assert a.known_answer(Control("f1", "textbox", "Preferred First Name"))[0] == "Yaswanth"


@pytest.mark.parametrize("question", ["Preferred First Name", "Preferred name (optional)"])
def test_an_optional_preferred_name_stays_blank(question):
    value, source = agent().known_answer(Control("f1", "textbox", question))
    assert value == "" and source == "profile.preferred_name"


def test_a_preferred_name_in_the_profile_is_used_as_it_is():
    import dataclasses
    a = agent()
    a.profile = dataclasses.replace(a.profile, preferred_name="Yash")
    assert a.known_answer(Control("f1", "textbox", "Preferred First Name*"))[0] == "Yash"


@pytest.mark.parametrize("line,question", [
    ("- generic [ref=f3e29]: Preferred First Name*", "Preferred First Name"),
    ("- generic [ref=f3e29]: Preferred First Name *", "Preferred First Name"),
    ("- generic [ref=e9]: Email*", "Email"),
])
def test_a_star_without_a_space_marks_the_question_required(line, question):
    assert question in required_questions(line + "\n")


def test_the_stand_ins_are_data():
    assert config.REQUIRED_STAND_IN["preferred_name"] == "first_name"
    assert set(config.REQUIRED_STAND_IN) <= config.BLANK_MEANS_NONE
