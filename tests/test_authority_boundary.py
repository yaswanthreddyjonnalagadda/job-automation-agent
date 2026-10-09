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

# The legitimate flow this fix must preserve: a real Workday-shaped application-step
# structure (the active-step marker's own text carrying a numeric "current step N of M",
# confirmed against a real recorded production page -- see submission_step_finality()'s own
# docstring) proving another application step genuinely remains. This is the ONLY positive
# NON_FINAL authority left after the second closure review, 7 October 2026 removed the
# generic ARIA-progressbar signal entirely (two successive attempts at a cross-ATS generic
# heuristic -- a bare "step"-labeled progressbar, then one also requiring aria-posinset/
# aria-setsize -- were each independently reproduced as live defects; neither is a
# deterministic proof that the element belongs to this application's own wizard).
WORKDAY_LEGITIMATE_INTERMEDIATE_STEP = """
  <div data-automation-id="progressBarActiveStep">current step 2 of 5</div>
  <form onsubmit="window.submitted = true; return false">
    <label>Answer <input id="answer"></label>
    <button type="submit">Submit</button>
  </form>
"""

# A third closure review's own reproduction, 7 October 2026: a bare, generic
# aria-current="step" element -- NOT Workday's own vetted data-automation-id marker, but the
# plain, standards-based ARIA state any unrelated nested stepper elsewhere on the page can
# carry -- used to be accepted by the same marker selector as Workday's vetted structure. Its
# presence alone does not tie it to THIS application's own wizard/navigation flow.
UNRELATED_ARIA_CURRENT_STEP_STEP = """
  <div aria-current="step">current step 2 of 5</div>
  <form onsubmit="window.submitted = true; return false">
    <label>Answer <input id="answer"></label>
    <button type="submit">Submit</button>
  </form>
"""

# The second closure-review's own reproduction, 7 October 2026: role="progressbar" PLUS
# aria-posinset/aria-setsize -- the prior fix's own stronger association attempt -- is still
# not authority. aria-posinset/aria-setsize are plain set-position semantics for a set-item
# role; their presence does not establish that this element belongs to the application's own
# wizard rather than an unrelated upload/onboarding/document sub-process (the label here says
# exactly that: "Upload progress").
POSINSET_SETSIZE_UNRELATED_ROLE_STEP = """
  <div role="progressbar" aria-valuenow="2" aria-valuemax="5"
       aria-posinset="1" aria-setsize="3" aria-label="Upload progress"></div>
  <form onsubmit="window.submitted = true; return false">
    <label>Answer <input id="answer"></label>
    <button type="submit">Submit</button>
  </form>
"""

# A deliberately convincing-looking generic progressbar -- application-looking label text,
# plausible posinset/setsize, numerically consistent values -- that is still not inside any
# specifically validated ATS application-wizard structure. Text and arbitrary ARIA attributes
# alone are not authority, however application-like they read.
MISLEADING_APPLICATION_LOOKING_PROGRESSBAR_STEP = """
  <div role="progressbar" aria-valuenow="2" aria-valuemax="5"
       aria-posinset="2" aria-setsize="5" aria-label="Application Step 2 of 5"></div>
  <form onsubmit="window.submitted = true; return false">
    <label>Answer <input id="answer"></label>
    <button type="submit">Submit</button>
  </form>
"""

# A follow-up closure-review defect, 7 October 2026: a bare role="progressbar" whose
# accessible name merely contains the word "step" is NOT application-wizard evidence on its
# own -- it must not be confused with an unrelated progress indicator (a file upload here)
# that happens to be labeled with that word.
UNRELATED_UPLOAD_PROGRESSBAR_STEP = """
  <div role="progressbar" aria-valuenow="2" aria-valuemax="5" aria-label="Upload step 2 of 5"></div>
  <form onsubmit="window.submitted = true; return false">
    <label>Answer <input id="answer"></label>
    <button type="submit">Submit</button>
  </form>
"""

# A follow-up closure-review defect, 7 October 2026: aria-valuenow="" -- Number("") === 0 in
# JavaScript, a real coercion trap that let an empty value pass a bare Number.isFinite()
# check and grant NON_FINAL. (These four fixtures now resolve UNKNOWN purely because the
# generic progressbar path is gone entirely -- but they are kept, unchanged, as a permanent
# regression guard against ever reintroducing that numeric coercion bug if a future,
# individually-vetted ATS-specific signal is added.)
EMPTY_VALUENOW_STEP = """
  <div role="progressbar" aria-valuenow="" aria-valuemax="5"
       aria-posinset="2" aria-setsize="5" aria-label="Step progress"></div>
  <form onsubmit="window.submitted = true; return false">
    <label>Answer <input id="answer"></label>
    <button type="submit">Submit</button>
  </form>
"""

# A follow-up closure-review defect, 7 October 2026: a negative current value must not be
# accepted just because it is numerically less than the max.
NEGATIVE_VALUENOW_STEP = """
  <div role="progressbar" aria-valuenow="-1" aria-valuemax="5"
       aria-posinset="2" aria-setsize="5" aria-label="Step progress"></div>
  <form onsubmit="window.submitted = true; return false">
    <label>Answer <input id="answer"></label>
    <button type="submit">Submit</button>
  </form>
"""

# A current value exceeding its own max is internally inconsistent -- it must not be read as
# either FINAL or NON_FINAL, only UNKNOWN.
VALUENOW_EXCEEDS_MAX_STEP = """
  <div role="progressbar" aria-valuenow="9" aria-valuemax="5"
       aria-posinset="2" aria-setsize="5" aria-label="Step progress"></div>
  <form onsubmit="window.submitted = true; return false">
    <label>Answer <input id="answer"></label>
    <button type="submit">Submit</button>
  </form>
"""

# A non-numeric string must not crash the check or be coerced into a number.
MALFORMED_VALUENOW_STEP = """
  <div role="progressbar" aria-valuenow="abc" aria-valuemax="5"
       aria-posinset="2" aria-setsize="5" aria-label="Step progress"></div>
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


def test_a_real_workday_intermediate_step_still_proceeds(page):
    """The legitimate flow this fix must preserve -- the only one left after the second
    closure review removed the generic ARIA-progressbar signal entirely: a real
    Workday-shaped active-step marker whose own text numerically proves another step
    remains. This is genuine, independently-verified evidence (not plan.step, not the
    control's own label) that the click must still proceed."""
    kind, _page, _reason = press_submit(page, WORKDAY_LEGITIMATE_INTERMEDIATE_STEP)

    assert kind == "moved"
    assert page.evaluate("window.submitted") is True


def test_an_unrelated_aria_current_step_element_is_still_not_authority(page):
    """A third closure review's own reproduction, 7 October 2026: the marker selector used
    to also match the bare, generic aria-current="step" ARIA state -- not Workday's own
    vetted data-automation-id marker -- so an unrelated nested stepper elsewhere on the
    page, with misleading plan.step metadata, could be misread as proof this application's
    own wizard has more steps remaining. Also proves the underlying Submit control is
    genuinely dangerous via a direct click on fresh content, matching the same
    browser-independence proof used throughout this file."""
    page.set_content(f"<html><body>{UNRELATED_ARIA_CURRENT_STEP_STEP}</body></html>")
    page.locator("button[type=submit]").click()
    assert page.evaluate("window.submitted") is True  # the control is genuinely dangerous
    page.evaluate("window.submitted = undefined")

    kind, _page, reason = press_submit(page, UNRELATED_ARIA_CURRENT_STEP_STEP, step="2 of 5")

    assert kind == "stop", reason
    assert page.evaluate("window.submitted") is None


def test_posinset_and_setsize_on_an_unrelated_role_is_still_not_authority(page):
    """Test 1 of the second Finding 6 closure-review follow-up, 7 October 2026: the prior
    fix's own stronger-association attempt (role="progressbar" plus aria-posinset/
    aria-setsize) is itself not sufficient -- that attribute pair is plain set-position
    semantics for a set-item role and does not establish this element describes the
    application's own steps rather than an unrelated upload. Also proves the underlying
    Submit control is genuinely dangerous via a direct click on fresh content."""
    page.set_content(f"<html><body>{POSINSET_SETSIZE_UNRELATED_ROLE_STEP}</body></html>")
    page.locator("button[type=submit]").click()
    assert page.evaluate("window.submitted") is True  # the control is genuinely dangerous
    page.evaluate("window.submitted = undefined")

    kind, _page, reason = press_submit(page, POSINSET_SETSIZE_UNRELATED_ROLE_STEP)

    assert kind == "stop", reason
    assert page.evaluate("window.submitted") is None


def test_a_misleading_application_looking_progressbar_is_still_not_authority(page):
    """Test 2: application-looking label text ("Application Step 2 of 5") combined with
    numerically consistent, plausible posinset/setsize/valuenow/valuemax values is still
    not a deterministic proof of association with the application's own wizard -- text and
    arbitrary ARIA attributes alone are never authority, however convincing they read."""
    kind, _page, reason = press_submit(page, MISLEADING_APPLICATION_LOOKING_PROGRESSBAR_STEP)

    assert kind == "stop", reason
    assert page.evaluate("window.submitted") is None


def test_duplicate_conflicting_markers_resolve_unknown(page):
    """Section F: two progressBarActiveStep elements disagreeing with each other (one live,
    one stale and hidden) is ambiguous evidence, not evidence either way -- it must not be
    read as permission to proceed."""
    kind, _page, reason = press_submit(page, DUPLICATE_CONFLICTING_MARKERS_STEP)

    assert kind == "stop", reason
    assert page.evaluate("window.submitted") is None


def test_a_hidden_stale_marker_claiming_steps_remain_is_not_trusted(page):
    """Section F: a progressBarActiveStep marker that is present in the DOM but not visible
    (display:none, the shape of a leftover node from a prior SPA state) must not be trusted
    as positive NON_FINAL evidence just because its stale text claims more steps remain."""
    kind, _page, reason = press_submit(page, HIDDEN_STALE_MARKER_STEP)

    assert kind == "stop", reason
    assert page.evaluate("window.submitted") is None


def test_an_unrelated_upload_progressbar_labeled_with_the_word_step_is_not_evidence(page):
    """Section A of the Finding 6 closure-review follow-up, 7 October 2026: a progressbar
    whose accessible name merely mentions the word "step" (an unrelated file-upload
    indicator, "Upload step 2 of 5") is not application-wizard evidence -- it carries no
    aria-posinset/aria-setsize, so there is no structural proof it describes the
    application's own steps. Also proves the underlying Submit control is genuinely
    dangerous via a direct click on fresh content, matching Section 7's requirement."""
    page.set_content(f"<html><body>{UNRELATED_UPLOAD_PROGRESSBAR_STEP}</body></html>")
    page.locator("button[type=submit]").click()
    assert page.evaluate("window.submitted") is True  # the control is genuinely dangerous
    page.evaluate("window.submitted = undefined")

    kind, _page, reason = press_submit(page, UNRELATED_UPLOAD_PROGRESSBAR_STEP)

    assert kind == "stop", reason
    assert page.evaluate("window.submitted") is None


def test_an_empty_aria_valuenow_is_not_evidence(page):
    """Section B: aria-valuenow="" must not be coerced by Number("") === 0 into a
    passing, finite value."""
    kind, _page, reason = press_submit(page, EMPTY_VALUENOW_STEP)

    assert kind == "stop", reason
    assert page.evaluate("window.submitted") is None


def test_a_negative_aria_valuenow_is_not_evidence(page):
    """Section C: a negative current value must not be accepted merely because it is
    numerically less than the max."""
    kind, _page, reason = press_submit(page, NEGATIVE_VALUENOW_STEP)

    assert kind == "stop", reason
    assert page.evaluate("window.submitted") is None


def test_an_aria_valuenow_exceeding_its_max_is_not_evidence(page):
    """Section D: now > max is internally inconsistent -- it must resolve to UNKNOWN, not
    be read as FINAL or NON_FINAL."""
    kind, _page, reason = press_submit(page, VALUENOW_EXCEEDS_MAX_STEP)

    assert kind == "stop", reason
    assert page.evaluate("window.submitted") is None


def test_a_malformed_aria_valuenow_string_is_not_evidence(page):
    """Section E: a non-numeric string must not crash the check or be coerced into a
    number."""
    kind, _page, reason = press_submit(page, MALFORMED_VALUENOW_STEP)

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


@pytest.mark.parametrize('context', [
    '<form></form>', '<label>Email<input></label>', '<h2>Review your application</h2>',
])
def test_ai_posting_label_cannot_authorize_apply_on_application(page, context):
    page.set_content('<h1>Engineer</h1><h2>Responsibilities</h2>' + context +
                     '<button onclick="window.submitted=true">Apply</button>')
    agent = make_agent()  # No browser guard: Python must refuse independently.
    snapshot = agent.snapshot(page)
    controls = parse_snapshot(snapshot)
    plan = PagePlan(page_kind='job_description', next_kind='open_application',
                    next_ref=ref_of(snapshot, 'button', 'Apply'), next_label='Apply')
    assert agent.press_next(page, plan, controls)[0] == 'stop'
    assert page.evaluate('window.submitted') is None


def test_dom_proven_posting_apply_is_allowed_by_python(page):
    page.set_content('<h1>Engineer</h1><h2>Responsibilities</h2>'
                     '<button onclick="window.opened=true">Apply</button>')
    agent = make_agent()
    agent.settle = lambda *_args: None
    snapshot = agent.snapshot(page)
    controls = parse_snapshot(snapshot)
    plan = PagePlan(page_kind='job_description', next_kind='open_application',
                    next_ref=ref_of(snapshot, 'button', 'Apply'), next_label='Apply')
    agent.press_next(page, plan, controls)
    assert page.evaluate('window.opened') is True
