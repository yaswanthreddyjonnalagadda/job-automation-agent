"""'Have you worked at <company>?' is answered from the person's own work history: "No" unless it is one of theirs.

Blue Cross and Blue Shield of Louisiana, 29 September: "Have you worked at Louisiana Blue as an employee or a
contracted employee?" was left blank -- the wording was not one the agent knew, known wordings got one fixed
answer whatever the company, and a rule for one employer (OCC) was written into the code.
"""
from types import SimpleNamespace

import pytest

import employment_history as E

RESUME = """JANE DOE
EXPERIENCE
Acme Corp, Springfield, IL | February 2025 - Present | Senior Network Engineer
Globex Inc., Shelbyville, IL | March 2021 - January 2025 | Network Engineer
Initech Ltd., Remote | 2018 - 2021 | Engineer
EDUCATION
Master of Science (MS), Computer Science | State University, Springfield, IL | December 2022
"""
EMPLOYERS = E.past_employers(SimpleNamespace(current_employer="Acme Corp", reasons_for_leaving=()), RESUME)


def test_the_employers_come_from_the_profile_and_the_resume_not_the_schools():
    assert EMPLOYERS == ["Acme Corp", "Globex Inc.", "Initech Ltd."]


@pytest.mark.parametrize("question", [
    "Have you worked at Louisiana Blue as an employee or a contracted employee?*",
    "Have you ever worked for Umbrella Corporation?",
    "Have you previously been employed by Umbrella?",
    "Did you ever work for Umbrella Corp as an intern or employee?",
    "Have you ever provided services to Umbrella as a consultant or contractor?",
    "Are you currently employed by Umbrella or any of its affiliates?",
    "Have you been a contractor at Umbrella in the past?",
])
def test_a_company_the_person_never_worked_for_is_no(question):
    assert E.answer(question, "Umbrella Corporation", EMPLOYERS) == "No"


@pytest.mark.parametrize("question, company", [
    ("Have you ever worked for Acme or any of its subsidiaries?", "Acme Corp"),
    ("Have you worked at Globex as an employee or contractor?", "Globex Corporation"),
    ("Have you worked for us before?", "Globex Inc."),
    ("Are you a former employee of Initech?", "Initech"),
    ("Have you worked here before?", "Acme Corporation"),
])
def test_a_company_on_the_resume_is_yes(question, company):
    assert E.answer(question, company, EMPLOYERS) == "Yes"


@pytest.mark.parametrize("question", [
    "Have you ever worked for a government agency?",
    "Have you worked for a competitor in the last year?",
    "Have you worked with any of the following technologies?",
    "Have you worked for another staffing agency?",
    "Why do you want to work for us?",
    "How many years have you worked in networking?",
    "Are you willing to work weekends?",
])
def test_a_question_that_names_no_one_in_particular_is_not_answered_here(question):
    assert E.answer(question, "Acme Corp", EMPLOYERS) == ""


@pytest.mark.parametrize("a, b, same", [
    ("Louisiana Blue", "Blue Cross and Blue Shield of Louisiana", True),
    ("Capital One", "Capital One Financial Corporation", True),
    ("OCC", "The Options Clearing Corporation", True),
    ("Globex", "Globex Inc.", True),
    ("One Medical", "Capital One", False),
    ("Acme Corp", "Globex Inc.", False),
    ("", "Acme", False),
])
def test_two_names_for_one_organisation(a, b, same):
    assert E.same_organisation(a, b) is same


def test_the_agent_answers_it_before_the_fixed_profile_default(tmp_path):
    import config
    import page_agent
    from page_agent import Control
    profile = config.UserProfile(email="jane@example.com", current_employer="Acme Corp",
                                 previously_employed_here="No")
    job = SimpleNamespace(title="Engineer", company="Acme Corporation", url="u")
    agent = page_agent.PageAgent(SimpleNamespace(values=None), SimpleNamespace(),
                                 SimpleNamespace(auto_submit=False, ats_email=""), profile,
                                 SimpleNamespace(raw_text=RESUME), job, resume_file=None)
    agent._profile_answer_library = {}
    asked = Control(ref="e1", role="radiogroup", name="Have you worked at Acme as an employee or contractor?*",
                    options=["Yes", "No"])
    assert agent.known_answer(asked) == ("Yes", "profile.work_history")
    other = Control(ref="e2", role="radiogroup", name="Have you worked at Louisiana Blue as an employee?*",
                    options=["Yes", "No"])
    assert agent.known_answer(other) == ("No", "profile.work_history")
