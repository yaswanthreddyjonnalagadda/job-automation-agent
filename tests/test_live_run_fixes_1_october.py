"""What the Premier Health and Waystar runs of 1 October showed. Synthetic data only; no browser, no network."""
from types import SimpleNamespace

import pytest

import config
import confirmation
import page_agent
from page_agent import Answer, Control


def agent(profile=None):
    return page_agent.PageAgent(SimpleNamespace(), SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email=""),
                                profile or config.get_user_profile(), SimpleNamespace(raw_text="x"),
                                SimpleNamespace(title="t", company="c", url="https://jobs.example.com/a"),
                                resume_file=None)


# --- a question gets the owner's state only when it asks for a state -------------------------------

@pytest.mark.parametrize("question", [
    "If applicable, has your professional license/certification (in any state) ever been revoked?",
    "Do you currently, or have you in the last 5 years, worked for the US Government (e.g., Congress, State Department)?",
    "Have you lived in any other state in the last 7 years?",
])
def test_a_question_that_only_mentions_a_state_is_not_given_the_owners_state(question):
    assert agent().known_answer(Control(ref="e1", role="combobox", name=question))[1] != "profile.state"


@pytest.mark.parametrize("question", ["State", "State*", "State/Province", "Home state", "Mailing State"])
def test_a_question_that_asks_for_the_state_still_gets_it(question):
    assert agent().known_answer(Control(ref="e1", role="combobox", name=question)) == ("Virginia", "profile.state")


# --- a "none" pick never stands beside real ones ----------------------------------------------------

AREAS = "I have experience in the following areas of Information Technology"


def picks(*values):
    by_ref = {f"e{i}": Control(ref=f"e{i}", role="checkbox", name=v, group=AREAS) for i, v in enumerate(values)}
    answers = [Answer(f"e{i}", AREAS, "check", v, "profile") for i, v in enumerate(values)]
    return [a.value for a in page_agent._without_contradicting_none(answers, by_ref)]


def test_none_is_dropped_beside_real_choices():
    assert picks("I have no experience in the following areas", "System Analysis/Installing/Design", "Security") == \
        ["System Analysis/Installing/Design", "Security"]


@pytest.mark.parametrize("none", ["None of the above", "Not applicable", "N/A", "I do not have any of these"])
def test_none_alone_stands(none):
    assert picks(none) == [none]


def test_real_choices_that_merely_start_with_no_are_kept():
    assert picks("Network Engineer", "North America region") == ["Network Engineer", "North America region"]


# --- one confirmation decision, with the site's own words ------------------------------------------

@pytest.mark.parametrize("text, site, received", [
    ("Application received. The hiring team will review your profile.", ("application received",), True),
    ("Thank you for applying to Premier Health!", (), True),
    ("You have successfully applied for Network Engineer", (), True),
    ("Your application was submitted.", (), True),
    ("Thank you for your interest. Sign in to continue.", (), False),
    ("To complete your application, upload your resume.", ("application received",), False),
    ("Jobs you have applied to will appear here", (), False),
])
def test_a_confirmation_is_read_the_same_way_everywhere(text, site, received):
    assert confirmation.says_received(text, site) is received


def test_the_reading_agent_and_the_wait_use_the_same_words():
    assert page_agent.CONFIRMATION_TEXT is confirmation.CONFIRMATION_TEXT
    import browser_automation
    assert not hasattr(browser_automation.JobApplicationAssistant, "_CONFIRMATION_PHRASES")
