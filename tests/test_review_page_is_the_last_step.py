"""A page headed "Review your application" is the last step, whatever its forward button says (f277).

Walmart, 28 October 2026: the review page (93%) offers Edit, Continue and Back, and Continue is the final submission. The
agent recognised a review step only by a visible Submit button or a wizard marker reading "Review", so it pressed Continue
as an ordinary step and the application was sent without the owner's pause. The next page said "Application Submitted!".
"""
from types import SimpleNamespace

import pytest

import config
import page_agent
import safety
from browser_automation import JobApplicationAssistant

REVIEW = """<header><input type="text" aria-label="Search for jobs"></header>
<main><h1>Review your application</h1><h2>Contact</h2><button>Edit</button><h2>Employment</h2><button>Edit</button>
<button id="go" onclick="window.pressed = true">Continue</button><button>Back</button></main>"""


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


@pytest.fixture
def assistant():
    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a.values = safety.AgentValues()
    return a


@pytest.mark.parametrize("heading", ["Review your application", "Review", "Review and submit", "Review & Submit",
                                     "Application review", "Review your information"])
def test_a_review_heading_with_nothing_left_to_fill_is_the_review_step(page, assistant, heading):
    page.set_content(f"<main><h1>{heading}</h1><button>Edit</button><button>Continue</button><button>Back</button></main>")
    assert assistant.is_review_step(page) is True


def test_the_search_box_in_the_site_header_does_not_hide_it(page, assistant):
    page.set_content(REVIEW)
    assert assistant.is_review_step(page) is True


def test_a_review_inside_an_iframe_is_found_too(page, assistant):
    inner = "<main><h1>Review your application</h1><button>Continue</button></main>"
    page.set_content(f'<h1>Walmart Careers</h1><iframe srcdoc="{inner}" width="600" height="300"></iframe>')
    assert assistant.is_review_step(page) is True


@pytest.mark.parametrize("markup", [
    "<main><h1>Review your resume</h1><button>Continue</button></main>",                 # not the application
    "<main><h1>Terms and Conditions</h1><button>Agree</button><button>Back</button></main>",
    "<main><h1>Review your application</h1><label>Notes <input id=n></label><button>Continue</button></main>",   # still to fill
    "<main><h1>Tell us about yourself</h1><label>Name <input id=n></label><button>Continue</button></main>",
    "<main><h2>We will review your application shortly</h2><button>Continue</button></main>",   # prose, not the page's heading
])
def test_anything_else_is_not_taken_for_the_review_step(page, assistant, markup):
    page.set_content(markup)
    assert assistant.is_review_step(page) is False


def _agent(assistant):
    job = SimpleNamespace(title="Senior Network Engineer", company="Walmart", url="https://careers.example.test/jobs/R-1")
    return page_agent.PageAgent(assistant, SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email=""),
                                config.UserProfile(email="o@example.com"), SimpleNamespace(raw_text="Network engineer"),
                                job, resume_file=None)


def _press_continue(agent, page):
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    plan = page_agent.PagePlan(page_kind="application_form", next_kind="next_step", next_label="Continue",
                               next_ref=next(c.ref for c in controls if c.name == "Continue"))
    return agent.press_next(page, plan, controls)


def test_continue_on_the_review_page_is_not_pressed_it_is_left_for_the_owner(page, assistant):
    """The real press path: the plan calls it an ordinary next step, and the agent still stops."""
    page.set_content(REVIEW)
    kind, _page, why = _press_continue(_agent(assistant), page)
    assert kind == "stop" and "ready for you to submit" in why
    assert not page.evaluate("window.pressed")


def test_continue_on_an_ordinary_step_is_still_pressed(page, assistant):
    page.set_content('<main><h1>Tell us about yourself</h1><label>Name <input id="n" value="x"></label>'
                     '<button id="go" onclick="window.pressed = true">Continue</button></main>')
    kind, _page, _why = _press_continue(_agent(assistant), page)
    assert kind != "stop" and page.evaluate("window.pressed") is True
