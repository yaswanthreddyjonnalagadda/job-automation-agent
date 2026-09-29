"""With the AI unavailable, a page whose required questions are all answered is not a reason to stop.

Blue Cross and Blue Shield of Louisiana (Workday), 29 September: My Experience needed only the resume, which the
agent attaches itself; Skills and Phone Extension were optional. Gemini's daily allowance was spent and the run
stopped anyway. Now optional questions are left blank and the run goes on; a required one still stops it.
"""
from types import SimpleNamespace

import pytest

import config
import page_agent
from page_agent import Control


def agent(planner=None, tmp_path=None):
    job = SimpleNamespace(title="Engineer", company="Acme", url="https://jobs.example.com/apply/1")
    return page_agent.PageAgent(SimpleNamespace(values=None), planner or SimpleNamespace(),
                                SimpleNamespace(auto_submit=False, ats_email=""),
                                config.UserProfile(email="jane@example.com"), SimpleNamespace(raw_text="x"), job,
                                resume_file=None)


NEXT = Control(ref="e9", role="button", name="Save and Continue")


def test_only_optional_questions_left_means_carry_on():
    a = agent()
    plan = a.carry_on_without_the_ai([Control(ref="e1", role="textbox", name="Phone Extension"), NEXT], set(),
                                     ["Phone Extension", "items selected"], "", "Gemini (429): daily allowance")
    assert plan is not None and plan.next_label == "Save and Continue"
    assert any("left blank without the AI" in n for n in a.notes)


@pytest.mark.parametrize("still_open, required", [
    (["Are you willing to relocate? *"], set()),
    (["Desired salary"], {"Desired salary"}),
])
def test_a_required_question_still_open_stops_the_run(still_open, required):
    assert agent().carry_on_without_the_ai([NEXT], required, still_open, "", "no AI") is None


def test_no_plain_way_forward_stops_the_run():
    assert agent().carry_on_without_the_ai([Control(ref="e1", role="button", name="Add")], set(), ["Skills"], "",
                                           "no AI") is None


# --- a whole run, with the AI failing on every page ------------------------------------------------

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


class NoAI:
    def plan_page(self, snapshot, facts, feedback=""):
        raise RuntimeError("Gemini (429): the key's daily allowance is used up")


def serve(page, optional_only=True):
    question = ('<label>Phone Extension <input id="ext"></label>' if optional_only else
                '<label>Are you willing to travel? * <input id="travel"></label>')
    pages = {"/apply/1": f"""<html><body><h1>My Experience</h1><p>current step 2 of 3</p>{question}
                <button onclick="location.href='/apply/2'">Save and Continue</button></body></html>""",
             "/apply/2": "<html><body><h1>Review</h1><p>current step 3 of 3</p><button>Submit</button></body></html>"}
    page.route("https://jobs.example.com/**", lambda route: route.fulfill(
        status=200, content_type="text/html", body=pages.get("/" + route.request.url.split("/", 3)[3], "x")))
    page.goto("https://jobs.example.com/apply/1")


def test_the_run_goes_on_past_optional_questions_with_no_ai(page):
    serve(page, optional_only=True)
    a = agent(NoAI())
    a.run(page)
    assert page.url.endswith("/apply/2")


def test_the_run_stops_at_a_required_question_with_no_ai(page):
    serve(page, optional_only=False)
    a = agent(NoAI())
    outcome = a.run(page)
    assert outcome.kind == "owner_needed" and page.url.endswith("/apply/1")
