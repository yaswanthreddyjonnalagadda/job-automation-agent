"""P0-B1 authority-boundary follow-up, 7 October 2026.

The final independent review of the submission-replay-and-containment-hardening PR found
that when PageAgent classifies a submit-labeled control as an intermediate step (not the
final one), the only thing standing between that classification being wrong and a real,
uncontained submit is the browser-side guard: Python's own gate (submit_gate(last_step=False))
runs CAPTCHA/legal/attestation checks, but never re-verifies the "not the last step" premise
itself. That premise rests entirely on plan.step, an AI-self-reported field this code never
reads from the page -- a misread or hallucinated "step N of M" had no independent, Python-side
check at all; the browser guard was the sole remaining protection for that specific click.

These tests call PageAgent.press_next() directly -- the real, production decision path, not a
reimplementation of it -- with no submission guard attached at all (the assistant deliberately
has no working protect_submission()), so a pass here proves Python itself refuses the click;
it is not propped up by browser-side containment.
"""
import re
from types import SimpleNamespace

import pytest

import browser_automation
import config
import safety
from page_agent import PageAgent, PagePlan, parse_snapshot


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


def ref_of(snapshot: str, role: str, name: str) -> str:
    m = re.search(rf'- {role} "{re.escape(name)}"[^\n]*\[ref=([\w-]+)\]', snapshot)
    return m.group(1) if m else ""


def make_agent():
    """A real JobApplicationAssistant instance (so is_review_step() and
    is_review_step_by_wizard_marker() are the real, unmodified production methods), with
    protect_submission() shadowed to a no-op so no browser-side SubmissionGuardV0 is ever
    constructed or attached -- the test proves Python's own refusal, with no browser-side
    backstop in play at all."""
    assistant = browser_automation.JobApplicationAssistant.__new__(browser_automation.JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    assistant.protect_submission = lambda tab: None
    cfg = SimpleNamespace(auto_submit=False, ats_email="")
    job = SimpleNamespace(title="Engineer", company="Example", url="https://jobs.example.com/apply/2")
    agent = PageAgent(assistant, SimpleNamespace(), cfg, config.UserProfile(email="o@example.com"),
                      SimpleNamespace(raw_text="x"), job, resume_file=None)
    agent._ensure_state()
    return agent


# A step whose own wizard chrome contradicts the step counter: it says "Review" is the active
# step, while the (AI-reported) step counter claims "2 of 5" -- more steps to come.
CONTRADICTORY_REVIEW_STEP = """
  <div data-automation-id="progressBarActiveStep">Review</div>
  <form onsubmit="window.submitted = true; return false">
    <label>Answer <input id="answer"></label>
    <button type="submit">Submit</button>
  </form>
"""

# The legitimate case this fix must not break: Schwab's own step 2 of 5, a plain form with no
# wizard-progress chrome of any kind -- its "Submit" button only saves that step.
ORDINARY_INTERMEDIATE_SUBMIT_STEP = """
  <form onsubmit="window.submitted = true; return false">
    <label>Answer <input id="answer"></label>
    <button type="submit">Submit</button>
  </form>
"""


def press_submit(page, html, *, next_kind="next_step"):
    page.set_content(f"<html><body>{html}</body></html>")
    agent = make_agent()
    snapshot = agent.snapshot(page)
    controls = parse_snapshot(snapshot)
    plan = PagePlan(
        page_kind="application_form", step="2 of 5",
        next_ref=ref_of(snapshot, "button", "Submit"), next_label="Submit", next_kind=next_kind,
    )
    outcome = agent.press_next(page, plan, controls)
    return outcome


def test_a_step_counter_contradicted_by_the_pages_own_wizard_chrome_is_refused(page):
    """The exact authority-boundary finding: the AI's self-reported step counter says more
    steps follow, but the page's own DOM-structural wizard marker says this IS the review
    step. Python must refuse the click itself -- not rely on the browser guard, which this
    test never attaches at all."""
    kind, _page, reason = press_submit(page, CONTRADICTORY_REVIEW_STEP)

    assert kind == "stop", reason
    assert page.evaluate("window.submitted") is None


def test_an_ordinary_intermediate_submit_with_no_contradiction_still_proceeds(page):
    """The complementary case: Schwab's own step 2 of 5 carries no wizard-progress chrome at
    all, so there is no contradiction to detect -- the control must still be pressed exactly
    as before this fix."""
    kind, _page, _reason = press_submit(page, ORDINARY_INTERMEDIATE_SUBMIT_STEP)

    assert kind == "moved"
    assert page.evaluate("window.submitted") is True


def test_the_wizard_marker_check_itself_is_independent_of_a_submit_labeled_button(page):
    """is_review_step_by_wizard_marker() must not be a disguised restatement of
    is_review_step()'s own "a button reads Submit" shortcut (which already fires on Schwab's
    legitimate step 2 of 5): it is checked directly, on a page with a wizard marker but no
    submit-labeled button at all, and separately confirmed False on a page with a
    submit-labeled button but no wizard marker (Schwab's actual shape)."""
    page.set_content(
        '<html><body><div data-automation-id="progressBarActiveStep">Review</div>'
        "<p>No submit button here.</p></body></html>"
    )
    assistant = browser_automation.JobApplicationAssistant.__new__(browser_automation.JobApplicationAssistant)
    assert assistant.is_review_step_by_wizard_marker(page) is True

    page.set_content(f"<html><body>{ORDINARY_INTERMEDIATE_SUBMIT_STEP}</body></html>")
    assert assistant.is_review_step_by_wizard_marker(page) is False
    assert assistant.is_review_step(page) is True  # the existing, broader check still fires
