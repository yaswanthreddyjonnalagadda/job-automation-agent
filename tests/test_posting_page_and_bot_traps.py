"""Two ways the agent spent AI requests on nothing.

Embry-Riddle (Workday), 28 September: the job posting page, with a job search box beside its Apply button, went
to the AI three times, and the AI said "click Apply". Writer, 28 September: a field hidden from people to catch
programs ("This input is for robots only") was sent to the AI as a question.
"""
from types import SimpleNamespace

import pytest

import config
import page_agent
from page_agent import Control, PageAgent, parse_snapshot


def agent(planner=None):
    job = SimpleNamespace(title="Engineer", company="Acme", url="https://jobs.example.com/apply/1")
    return PageAgent(SimpleNamespace(values=None), planner or SimpleNamespace(),
                     SimpleNamespace(auto_submit=False, ats_email=""), config.UserProfile(email="jane@example.com"),
                     SimpleNamespace(raw_text="x"), job, resume_file=None)


APPLY = Control(ref="e9", role="button", name="Apply")


@pytest.mark.parametrize("other", [
    Control(ref="e1", role="searchbox", name="Search for Jobs"),
    Control(ref="e1", role="combobox", name="Language", value="Select"),
])
def test_a_posting_with_other_boxes_is_opened_without_the_ai(other):
    plan = agent().posting_plan([other, APPLY], set(), [other.name], "")
    assert plan is not None and plan.next_kind == "open_application" and plan.next_label == "Apply"


def test_a_form_with_a_required_question_and_an_apply_button_is_not_treated_as_a_posting():
    question = Control(ref="e1", role="textbox", name="Years of experience *")
    assert agent().posting_plan([question, APPLY], set(), ["Years of experience *"], "") is None


def test_a_page_whose_way_on_is_not_apply_is_left_to_the_usual_path():
    nxt = Control(ref="e9", role="button", name="Next")
    assert agent().posting_plan([Control(ref="e1", role="searchbox", name="Search"), nxt], set(), ["Search"], "") is None


# --- a field for robots ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("label", [
    "Enter website. This input is for robots only, do not enter if you're human.",
    "Do not fill this in if you are a human",
    "If you are human, leave this field blank",
])
def test_a_field_for_robots_is_not_read_as_a_question(label):
    snapshot = f'- textbox "{label}" [ref=e1]\n- textbox "Email" [ref=e2]'
    assert [c.name for c in parse_snapshot(snapshot)] == ["Email"]


@pytest.mark.parametrize("label", ["Website", "Leave blank if you have no middle name", "Personal website or portfolio"])
def test_an_ordinary_field_is_not_taken_for_a_trap(label):
    assert not page_agent.is_bot_trap(Control(ref="e1", role="textbox", name=label))


# --- a whole run: the posting goes to Apply with no AI call ------------------------------------------------------

@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


class CountingAI:
    def __init__(self):
        self.pages = 0

    def plan_page(self, snapshot, facts, feedback=""):
        self.pages += 1
        raise AssertionError("the AI was asked about a page")


def test_the_run_presses_apply_on_a_posting_without_asking_the_ai(browser):
    context = browser.new_context()
    page = context.new_page()
    pages = {"/apply/1": """<html><body><h1>Network Engineer</h1><p>About the job ...</p>
                 <label>Search for Jobs <input type="search"></label>
                 <button onclick="location.href='/apply/2'">Apply</button></body></html>""",
             "/apply/2": "<html><body><h1>Review</h1><p>current step 3 of 3</p><button>Submit</button></body></html>"}
    page.route("https://jobs.example.com/**", lambda route: route.fulfill(
        status=200, content_type="text/html", body=pages.get("/" + route.request.url.split("/", 3)[3], "x")))
    page.goto("https://jobs.example.com/apply/1")
    ai = CountingAI()
    agent(ai).run(page)
    assert page.url.endswith("/apply/2") and ai.pages == 0
    context.close()
