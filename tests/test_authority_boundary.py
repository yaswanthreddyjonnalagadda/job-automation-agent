"""P0-B1 authority-boundary follow-up, 7 October 2026 -- narrowed and then generalized.

The final independent review of the submission-replay-and-containment-hardening PR found
that when PageAgent classifies a submit-labeled control as an intermediate step (not the
final one), the only thing standing between that classification being wrong and a real,
uncontained submit is the browser-side guard: Python's own gate (submit_gate(last_step=False))
runs CAPTCHA/legal/attestation checks, but never re-verifies the "not the last step" premise
itself. That premise rested entirely on plan.step, an AI-self-reported field this code never
reads from the page -- a misread or hallucinated "step N of M" had no independent, Python-side
check at all; the browser guard was the sole remaining protection for that specific click.

A first fix (commit 756b77b) cross-checked the step counter against a Workday-style wizard
marker, but only when that marker was present and read literally as "Review". A second,
independent review of that commit reproduced -- through this exact production path, with the
browser-side guard entirely absent -- a genuinely final submit control on a page with NO such
marker and a wrong step count being clicked anyway: the general case, not just the
Workday-wizard-contradiction case, was still open. This file's tests now cover that general
case: PageAgent._press_next_locked() calls assistant.submission_step_finality(), a tri-state
("FINAL" / "NON_FINAL" / "UNKNOWN") read directly from the page's own DOM structure, and only
"NON_FINAL" may authorize an ordinary submit-labeled click outside the authoritative
final-submit gateway -- an unsupported ATS shape, a missing marker, or ambiguous evidence is
"UNKNOWN", which fails closed exactly like "FINAL".

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

# A submit-labeled control with NO wizard-progress chrome of any kind -- the common shape
# for ATSs that are not Workday-style wizards (Greenhouse, Lever, Ashby, SuccessFactors, and
# similar). This is Schwab's own step 2 of 5's actual DOM shape; it is reused here as the
# general-case reproduction because it carries no independent, DOM-derived evidence either
# way -- exactly what the final independent review of commit 756b77b found still clicked
# through when plan.step is wrong and the control is genuinely final (see
# docs/security/phase0-b1-authority-boundary-final-independent-review.md, Section 3).
NO_INDEPENDENT_EVIDENCE_SUBMIT_STEP = """
  <form onsubmit="window.submitted = true; return false">
    <label>Answer <input id="answer"></label>
    <button type="submit">Submit</button>
  </form>
"""

# The legitimate case this fix must not break: a non-Workday stepper that exposes its
# progress through the standard ARIA progressbar pattern (role="progressbar" with numeric
# aria-valuenow/aria-valuemax and an accessible name that mentions "step") -- read directly
# by submission_step_finality() as positive, independent NON_FINAL evidence, never from
# plan.step.
ARIA_PROGRESSBAR_NON_FINAL_STEP = """
  <div role="progressbar" aria-valuenow="2" aria-valuemax="5" aria-label="Step 2 of 5"></div>
  <form onsubmit="window.submitted = true; return false">
    <label>Answer <input id="answer"></label>
    <button type="submit">Submit</button>
  </form>
"""

# An unsupported/unrecognized ATS shape: a progress-looking element that is neither a
# Workday-style marker nor a standards-based ARIA progressbar (an ordinary styled <div>, the
# kind of ad hoc markup an unrecognized ATS might use for its own step indicator). No
# positive evidence can be read from this, so it must resolve to UNKNOWN, not NON_FINAL.
UNSUPPORTED_ATS_PROGRESS_STEP = """
  <div class="step-indicator">Step 2 of 5</div>
  <form onsubmit="window.submitted = true; return false">
    <label>Answer <input id="answer"></label>
    <button type="submit">Submit</button>
  </form>
"""

# Adversarial variants of ambiguous/garbled progress evidence: none may ever be read as
# positive NON_FINAL evidence.
DUPLICATE_CONFLICTING_MARKERS_STEP = """
  <div data-automation-id="progressBarActiveStep">current step 2 of 5</div>
  <div data-automation-id="progressBarActiveStep" style="display:none">current step 5 of 5</div>
  <form onsubmit="window.submitted = true; return false">
    <label>Answer <input id="answer"></label>
    <button type="submit">Submit</button>
  </form>
"""

MALFORMED_PROGRESSBAR_STEP = """
  <div role="progressbar" aria-valuenow="not-a-number" aria-valuemax="5" aria-label="Step progress"></div>
  <form onsubmit="window.submitted = true; return false">
    <label>Answer <input id="answer"></label>
    <button type="submit">Submit</button>
  </form>
"""

INCONSISTENT_PROGRESSBAR_STEP = """
  <div role="progressbar" aria-valuenow="5" aria-valuemax="3" aria-label="Step progress"></div>
  <form onsubmit="window.submitted = true; return false">
    <label>Answer <input id="answer"></label>
    <button type="submit">Submit</button>
  </form>
"""

HIDDEN_STALE_MARKER_STEP = """
  <div data-automation-id="progressBarActiveStep" style="display:none">current step 2 of 5</div>
  <form onsubmit="window.submitted = true; return false">
    <label>Answer <input id="answer"></label>
    <button type="submit">Submit</button>
  </form>
"""


def press_submit(page, html, *, next_kind="next_step", step="2 of 5"):
    page.set_content(f"<html><body>{html}</body></html>")
    agent = make_agent()
    snapshot = agent.snapshot(page)
    controls = parse_snapshot(snapshot)
    plan = PagePlan(
        page_kind="application_form", step=step,
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


def test_a_genuinely_final_control_with_no_marker_and_a_wrong_step_count_is_refused(page):
    """Section A of the general authority-boundary follow-up, 7 October 2026: the exact
    scenario the final independent review of commit 756b77b reproduced. No wizard marker of
    any kind is present, plan.step falsely claims more steps remain, and the control is
    genuinely final (its onsubmit really fires). Python must refuse this itself -- there is
    no browser-side guard attached at all in this test -- because submission_step_finality()
    can only answer UNKNOWN here, and UNKNOWN must fail closed exactly like FINAL. This test
    fails against commit 756b77b (it returns "moved" there)."""
    kind, _page, reason = press_submit(page, NO_INDEPENDENT_EVIDENCE_SUBMIT_STEP)

    assert kind == "stop", reason
    assert page.evaluate("window.submitted") is None


def test_misleading_step_metadata_with_no_independent_proof_fails_closed(page):
    """Section E: however extreme the AI-reported step count is -- "1 of 99" here, not just
    a plausible-looking "2 of 5" -- it must have zero influence on this decision when there
    is no independent DOM evidence either way."""
    kind, _page, reason = press_submit(page, NO_INDEPENDENT_EVIDENCE_SUBMIT_STEP, step="1 of 99")

    assert kind == "stop", reason
    assert page.evaluate("window.submitted") is None


def test_an_unsupported_ats_progress_shape_resolves_unknown_not_non_final(page):
    """Section B: a progress-looking element that matches neither the Workday marker nor the
    standards-based ARIA progressbar pattern is not positive evidence of anything -- it must
    resolve to UNKNOWN (fail closed), not be creatively parsed into NON_FINAL."""
    kind, _page, reason = press_submit(page, UNSUPPORTED_ATS_PROGRESS_STEP)

    assert kind == "stop", reason
    assert page.evaluate("window.submitted") is None


def test_a_non_workday_stepper_with_real_aria_progress_evidence_still_proceeds(page):
    """The legitimate flow this fix must preserve, generalized beyond Workday: a stepper
    exposing the standard ARIA progressbar pattern, with an accessible name that mentions
    'step' and a current value strictly below its max, is real independent evidence that
    this is not the last step -- the click must still proceed."""
    kind, _page, _reason = press_submit(page, ARIA_PROGRESSBAR_NON_FINAL_STEP)

    assert kind == "moved"
    assert page.evaluate("window.submitted") is True


def test_duplicate_conflicting_markers_resolve_unknown(page):
    """Section F: two progressBarActiveStep elements disagreeing with each other (one live,
    one stale and hidden) is ambiguous evidence, not evidence either way -- it must not be
    read as permission to proceed."""
    kind, _page, reason = press_submit(page, DUPLICATE_CONFLICTING_MARKERS_STEP)

    assert kind == "stop", reason
    assert page.evaluate("window.submitted") is None


def test_a_malformed_progressbar_value_resolves_unknown(page):
    """Section F: aria-valuenow that isn't actually a number must not crash the check or be
    coerced into either a FINAL or NON_FINAL answer."""
    kind, _page, reason = press_submit(page, MALFORMED_PROGRESSBAR_STEP)

    assert kind == "stop", reason
    assert page.evaluate("window.submitted") is None


def test_an_inconsistent_progressbar_value_resolves_unknown_not_final(page):
    """Section F: aria-valuenow exceeding aria-valuemax is internally inconsistent. It must
    not grant NON_FINAL (it plainly isn't "current < total"), and this check deliberately
    never treats a progressbar's own high reading as proof of FINAL either, so this resolves
    to UNKNOWN -- still a refusal, for the same fail-closed reason."""
    kind, _page, reason = press_submit(page, INCONSISTENT_PROGRESSBAR_STEP)

    assert kind == "stop", reason
    assert page.evaluate("window.submitted") is None


def test_a_hidden_stale_marker_claiming_steps_remain_is_not_trusted(page):
    """Section F: a progressBarActiveStep marker that is present in the DOM but not visible
    (display:none, the shape of a leftover node from a prior SPA state) must not be trusted
    as positive NON_FINAL evidence just because its stale text claims more steps remain."""
    kind, _page, reason = press_submit(page, HIDDEN_STALE_MARKER_STEP)

    assert kind == "stop", reason
    assert page.evaluate("window.submitted") is None


def test_browser_containment_is_not_what_refused_the_click(page):
    """Section 7's explicit proof: the fixture's onsubmit handler is real (clicking the
    button directly, with no PageAgent involved at all, really does fire it), and
    PageAgent.press_next() refuses before ever reaching that click -- not because some
    guard intercepted it, but because Python's own decision never presses the button."""
    page.set_content(f"<html><body>{NO_INDEPENDENT_EVIDENCE_SUBMIT_STEP}</body></html>")
    page.locator("button[type=submit]").click()
    assert page.evaluate("window.submitted") is True  # the control is genuinely dangerous

    # A fresh set_content() does not recreate `window` (confirmed during the P0-B1 hardening
    # work, 7 October 2026): a plain property assigned on it, unlike a listener, survives --
    # so the flag above must be cleared explicitly before proving the second half, or a pass
    # here would just be reading the first click's own leftover state.
    page.evaluate("window.submitted = undefined")
    page.set_content(f"<html><body>{NO_INDEPENDENT_EVIDENCE_SUBMIT_STEP}</body></html>")
    agent = make_agent()
    assert not hasattr(agent.assistant, "_submission_guard")
    snapshot = agent.snapshot(page)
    controls = parse_snapshot(snapshot)
    plan = PagePlan(
        page_kind="application_form", step="2 of 5",
        next_ref=ref_of(snapshot, "button", "Submit"), next_label="Submit", next_kind="next_step",
    )
    kind, _page, reason = agent.press_next(page, plan, controls)

    assert kind == "stop", reason
    assert page.evaluate("window.submitted") is None


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

    page.set_content(f"<html><body>{NO_INDEPENDENT_EVIDENCE_SUBMIT_STEP}</body></html>")
    assert assistant.is_review_step_by_wizard_marker(page) is False
    assert assistant.is_review_step(page) is True  # the existing, broader check still fires
