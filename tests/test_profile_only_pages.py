"""A page the profile answers whole is planned without the AI -- and must still be planned right.

The shortcut (PAGE_FULLY_KNOWN) saves an AI call on every page the profile answers. When it was
added, the AI plan had been quietly doing four other jobs, and each one was lost:
  * naming a plain <input type=file> "Resume *" for the upload -- the resume was never attached;
  * reading the step counter -- a "Submit" on step 1 of 2 was taken for the last and the run stopped;
  * pressing "Add Experience" on a page with no Next -- the run gave up;
  * seeing a dropdown the agent does not take for a question ("Choose an option") -- Next was pressed
    with a required answer blank.
And its relocation shortcut took any choice with "relocate" in it, "not willing to relocate" too.
"""
import dataclasses
from types import SimpleNamespace

import pytest

import config
import page_agent
import safety
from browser_automation import JobApplicationAssistant
from page_agent import Control


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    pg = context.new_page()
    yield pg
    context.close()


def make_agent(tmp_path, profile=None):
    resume = tmp_path / "My_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    job = SimpleNamespace(title="t", company="c", url="https://jobs.example.com/apply")
    return page_agent.PageAgent(assistant, SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email=""),
                                profile or config.UserProfile(email="o@example.com"),
                                SimpleNamespace(raw_text="x"), job, resume_file=resume)


# --- the step counter decides whether "Submit" is the last ------------------------

SUBMIT = [Control(ref="e1", role="button", name="Submit")]


@pytest.mark.parametrize("snapshot, kind", [
    ("- text: Step 1 of 2", "next_step"),
    ("- text: Step 4 of 5", "next_step"),
    ("- text: step 2/5", "next_step"),
    ("- text: Page 3 of 3", "final_submit"),
    ("- text: Step 2 of 2", "final_submit"),
    ("- heading: Review your application", "final_submit"),
])
def test_a_submit_is_the_last_only_when_no_more_steps_are_shown(tmp_path, snapshot, kind):
    plan = make_agent(tmp_path).profile_plan(SUBMIT, set(), [], snapshot)
    assert plan.next_kind == kind


def test_the_counter_is_reported_as_the_step(tmp_path):
    assert make_agent(tmp_path).profile_plan(SUBMIT, set(), [], "- text: Step 1 of 2").step == "1 of 2"


# --- a dropdown still showing its placeholder keeps the page for the plan ------------

@pytest.mark.parametrize("shown", ["Choose an option", "Select One", "Select...", "-- Make a Selection --",
                                   "Please select", "Choose one"])
@pytest.mark.parametrize("role", ["button", "combobox", "listbox"])
def test_a_placeholder_still_showing_is_a_blank_answer(shown, role):
    assert page_agent._blank_choice_on_page([Control(ref="e1", role=role, name=shown)])
    assert page_agent._blank_choice_on_page([Control(ref="e1", role=role, name="Q", value=shown)])


@pytest.mark.parametrize("control", [
    Control(ref="e1", role="button", name="Next"),
    Control(ref="e1", role="combobox", name="Country", value="United States"),
    Control(ref="e1", role="button", name="Choose an option", disabled=True),
    Control(ref="e1", role="textbox", name="Select"),
])
def test_an_answered_choice_or_an_ordinary_button_is_not(control):
    assert not page_agent._blank_choice_on_page([control])


# --- a plain file box is found by asking the page what it is ----------------------------

def test_a_plain_file_box_named_by_its_label_is_the_resume_upload(page, tmp_path):
    page.set_content('<form><label for="r">Resume *</label><input type="file" id="r">'
                     '<button type="button">Resume builder</button></form>')
    agent = make_agent(tmp_path)
    snapshot = agent.snapshot(page)
    given = agent.attach_documents(page, snapshot, page_agent.parse_snapshot(snapshot))
    assert [a.action for a, _c in given] == ["upload_resume"]
    assert page.evaluate("document.getElementById('r').files[0].name") == "My_Resume.pdf"


def test_a_button_that_only_says_resume_is_not_taken_for_the_upload(page, tmp_path):
    page.set_content('<form><button type="button" onclick="document.body.dataset.pressed=1">Resume builder'
                     '</button></form>')
    agent = make_agent(tmp_path)
    snapshot = agent.snapshot(page)
    assert agent.attach_documents(page, snapshot, page_agent.parse_snapshot(snapshot)) == []
    assert page.evaluate("document.body.dataset.pressed || ''") == ""


# --- relocation, and the questions the profile answers once and for all ---------------

LOCATION_CHOICES = ["San Francisco (Union Square)", "New York (Manhattan)", "Seattle, Washington", "London, UK",
                    "No, and not willing to relocate", "No, but willing to relocate"]


@pytest.mark.parametrize("choices", [LOCATION_CHOICES, list(reversed(LOCATION_CHOICES))])
def test_willing_to_relocate_never_picks_not_willing(tmp_path, choices):
    agent = make_agent(tmp_path, dataclasses.replace(config.UserProfile(), open_to_relocation=True))
    group = Control(ref="e1", role="combobox", name="If you aren't located in one of these, are you open to relocate?",
                    options=choices)
    assert agent.known_answer(group)[0] == "No, but willing to relocate"


def test_the_relatives_question_is_answered_from_the_profile_again(tmp_path):
    agent = make_agent(tmp_path, dataclasses.replace(config.UserProfile(), relatives_employed_here="No"))
    box = Control(ref="e1", role="combobox", name="Do you have a relative or family member employed here?")
    assert agent.known_answer(box) == ("No", "profile.relatives_employed_here")
