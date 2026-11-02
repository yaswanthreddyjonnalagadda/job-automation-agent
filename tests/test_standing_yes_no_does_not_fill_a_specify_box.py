"""A settled Yes/No answer is not typed into a free-text box that asks to specify something (f285).

Color Health (CareerPuck), 1 November 2026: 'If you will require the company's assistance or sponsorship for work
authorization, please specify the type (e.g., H-1B transfer, TN, ...)' is a text box. The bare word 'sponsor' chose the
owner's settled sponsorship answer, so the box held 'Yes', and the correct answer ('H-1B') then could not replace it
("could not set"). A keyword chose the wrong concept: the question asks for a type, not whether.
"""
from types import SimpleNamespace

import pytest

import config
from browser_automation import JobApplicationAssistant
from page_agent import Control, PageAgent

SPECIFY = ("If you will require the company’s assistance or sponsorship for work authorization, please specify the "
           "type (e.g., H-1B transfer, TN, F-1 to H-1B, etc.).")
YES_NO = "Will you require the company’s assistance or sponsorship with work authorization now or in the future?*"


def known(question, role="textbox", options=()):
    agent = PageAgent(JobApplicationAssistant.__new__(JobApplicationAssistant), None, SimpleNamespace(),
                      config.UserProfile(requires_visa_sponsorship=True, open_to_relocation=True), None,
                      SimpleNamespace(title="Engineer", company="Example", url="https://example.com"))
    agent.library_answer = lambda *a, **kw: ""
    return agent.known_answer(Control("field", role, question, options=list(options)))[0]


def test_a_specify_the_type_text_box_is_not_given_yes():
    assert known(SPECIFY) == ""


@pytest.mark.parametrize("question", [
    "If you will relocate, please describe the timeline.",
    "If you will need sponsorship, please describe your current status.",
])
def test_a_box_asking_to_describe_or_explain_is_not_given_yes(question):
    assert known(question) == ""


def test_the_yes_no_question_is_still_answered_from_the_profile():
    assert known(YES_NO, role="radio", options=["Yes", "No"]) == "Yes"
    assert known(YES_NO) == "Yes"
