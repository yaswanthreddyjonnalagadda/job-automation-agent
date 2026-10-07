"""Containment tests for browser paths that could submit an application."""

from types import SimpleNamespace
from threading import Event, Thread

import page_agent
import pytest
import safety
from browser_automation import JobApplicationAssistant
from job_tracker import JobTracker
from submission_guard import SubmissionGuardV0


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    instance = context.new_page()
    yield instance
    context.close()


def install_submit_form(page, label="Submit Application"):
    page.set_content(f"""
      <form onsubmit="window.submitted = (window.submitted || 0) + 1; return false">
        <label>Answer <input id="answer"></label>
        <button type="submit">{label}</button>
      </form>
    """)


def guarded(page):
    guard = SubmissionGuardV0(page.context)
    guard.activate(page)
    return guard


@pytest.mark.parametrize("label", [
    "Submit Application",
    "Save and Submit",
    "Finish",
    "Send Application",
])
def test_normal_click_is_blocked_for_final_submit_labels(page, label):
    install_submit_form(page, label)
    guard = guarded(page)

    page.get_by_role("button", name=label).click()

    assert page.evaluate("window.submitted") is None
    assert guard.denials(page)[0]["kind"] == "click"


@pytest.mark.parametrize(("element", "label"), [
    ('<button type="submit"></button>', ""),
    ('<input type="submit">', ""),
    ('<button type="submit" aria-label="Finish"></button>', "Finish"),
])
def test_structural_submitters_are_blocked_without_text_inference(page, element, label):
    page.set_content(
        f'<form onsubmit="window.submitted = true; return false">{element}</form>'
    )
    guard = guarded(page)
    page.locator("button[type=submit], input[type=submit]").click()
    assert page.evaluate("window.submitted") is None
    assert guard.denials(page)[0]["kind"] == "click"


def test_form_associated_submitter_outside_form_is_guarded(page):
    page.set_content("""
      <form id="application" onsubmit="window.submitted = true; return false"></form>
      <input type="submit" form="application" value="Send">
    """)
    guard = guarded(page)
    page.locator("input[type=submit]").click()
    assert page.evaluate("window.submitted") is None
    assert guard.denials(page)[0]["kind"] == "click"


def test_form_submit_without_submitter_blocks_if_any_final_submitter_exists(page):
    page.set_content("""
      <form onsubmit="window.submitted = true; return false">
        <button type="submit">Continue</button>
        <button type="submit">Submit Application</button>
      </form>
    """)
    guard = guarded(page)
    page.evaluate("document.querySelector('form').submit()")
    assert page.evaluate("window.submitted") is None
    assert guard.denials(page)[0]["kind"] == "javascript_form_submit"


def test_enter_with_neutral_structural_submitter_is_blocked(page):
    page.set_content("""
      <form onsubmit="window.submitted = true; return false">
        <input id="answer">
        <button type="submit"></button>
      </form>
    """)
    guard = guarded(page)
    page.locator("#answer").press("Enter")
    assert page.evaluate("window.submitted") is None
    assert guard.denials(page)[0]["kind"] == "enter"


def test_force_click_fallback_stops_after_guard_denial(page):
    install_submit_form(page)
    guard = guarded(page)

    class FirstClickFails:
        def __init__(self, locator):
            self.locator = locator
            self.forced = False

        def evaluate(self, *args):
            return self.locator.evaluate(*args)

        def click(self, force=False, **kwargs):
            if not force:
                raise RuntimeError("simulated actionability failure")
            self.forced = True
            return self.locator.click(force=True, **kwargs)

        def dispatch_event(self, *args, **kwargs):
            return self.locator.dispatch_event(*args, **kwargs)

    button = FirstClickFails(page.get_by_role("button", name="Submit Application"))
    assert not JobApplicationAssistant._click_resiliently(button)
    assert button.forced
    assert page.evaluate("window.submitted") is None
    assert guard.denials(page)[0]["kind"] == "click"


def test_direct_force_click_is_blocked(page):
    install_submit_form(page)
    guard = guarded(page)

    page.get_by_role("button", name="Submit Application").click(force=True)

    assert page.evaluate("window.submitted") is None
    assert guard.denials(page)[0]["kind"] == "click"


def test_interaction_resilient_click_stops_before_force_fallback(page):
    from interaction import click_resiliently

    install_submit_form(page)
    guarded(page)

    assert not click_resiliently(page.get_by_role("button", name="Submit Application"))
    assert page.evaluate("window.submitted") is None


def test_dispatch_event_is_blocked(page):
    install_submit_form(page)
    guard = guarded(page)
    button = page.get_by_role("button", name="Submit Application")

    button.dispatch_event("click")

    assert page.evaluate("window.submitted") is None
    assert guard.denials(page)[0]["kind"] == "click"


def test_javascript_element_click_is_blocked(page):
    install_submit_form(page)
    guard = guarded(page)
    button = page.get_by_role("button", name="Submit Application")

    button.evaluate("element => element.click()")

    assert page.evaluate("window.submitted") is None
    assert guard.denials(page)[0]["kind"] == "click"


def test_enter_inside_form_with_final_submitter_is_blocked(page):
    install_submit_form(page)
    guard = guarded(page)

    page.locator("#answer").press("Enter")

    assert page.evaluate("window.submitted") is None
    assert guard.denials(page)[0]["kind"] == "enter"


@pytest.mark.parametrize(("next_kind", "expected_denial"), [
    # "next_step" with no DOM evidence either way used to reach the browser guard -- Python's
    # own gate (submit_gate(last_step=False)) trusted the AI-reported step counter alone and
    # let the click through, and only the structural default-deny rule below caught it,
    # logging a "click" denial. The P0-B1 general authority-boundary fix (7 October 2026)
    # closed that gap in Python itself: submission_step_finality() can only answer UNKNOWN on
    # this plain fixture (no wizard marker, no ARIA progressbar), and UNKNOWN now fails closed
    # exactly like a recognized final control -- so Python refuses before ever attempting the
    # click, and the browser guard (still armed here, unlike test_authority_boundary.py's
    # no-op) never sees an attempt to deny.
    ("next_step", None),
    ("final_submit", None),
])
def test_page_agent_plan_does_not_authorize_submit(page, tmp_path, next_kind, expected_denial):
    page.set_content("""
      <form onsubmit="window.submitted = true; return false">
        <button type="submit">Submit Application</button>
      </form>
    """)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.pending_attestations = lambda _page: []
    profile = SimpleNamespace(accept_application_privacy_prompts=False)
    agent = page_agent.PageAgent(
        assistant,
        SimpleNamespace(),
        SimpleNamespace(),
        profile,
        SimpleNamespace(raw_text=""),
        SimpleNamespace(url="https://jobs.example.com/apply"),
        resume_file=None,
        job_dir=tmp_path,
    )
    snapshot = agent.snapshot(page)
    controls = page_agent.parse_snapshot(snapshot)
    submit = next(control for control in controls if control.name == "Submit Application")
    plan = page_agent.PagePlan(
        page_kind="application_form",
        step="1 of 2",
        next_kind=next_kind,
        next_label="Submit Application",
        next_ref=submit.ref,
    )

    outcome, _page, _reason = agent.press_next(page, plan, controls)

    assert outcome == "stop"
    assert page.evaluate("window.submitted") is None
    denials = assistant._submission_guard.denials(page)
    assert (denials[0]["kind"] if denials else None) == expected_denial


@pytest.mark.parametrize("label", ["Next", "Continue"])
def test_ordinary_navigation_buttons_still_work(page, label):
    page.set_content("""
      <form onsubmit="window.moved = true; return false">
        <label>Answer <input id="answer"></label>
        <button type="submit">""" + label + """</button>
      </form>
    """)
    guard = guarded(page)

    page.get_by_role("button", name=label).click()

    assert page.evaluate("window.moved") is True
    assert guard.denials(page) == []


def test_enter_for_an_intermediate_next_button_still_works(page):
    page.set_content("""
      <form onsubmit="window.moved = true; return false">
        <label>Answer <input id="answer"></label>
        <button type="submit">Next</button>
      </form>
    """)
    guard = guarded(page)

    page.locator("#answer").press("Enter")

    assert page.evaluate("window.moved") is True
    assert guard.denials(page) == []


@pytest.mark.parametrize("label", [
    "Confirm & Send", "Confirm and Send", "Review & Send", "Finalize",
    "Complete", "Done", "Save & Finish", "Proceed", "Postuler", "Bewerben",
])
def test_unrecognized_label_on_a_type_button_control_is_still_blocked(page, label):
    """Pass 4, Finding 1 (adversarial review, 7 October 2026): a <button type="button"> with a
    custom onclick handler has no structural marker SUBMIT_LABEL_RE or structuralSubmitter ever
    recognized -- "Confirm & Send" on a non-Workday ATS went uncontained. The fix does not depend
    on recognizing the label at all: a labelled, form-associated, non-ordinary-navigation,
    non-safe-utility control that is the SOLE such candidate in its form is denied by default."""
    page.set_content(f"""
      <form>
        <label>Answer <input id="answer"></label>
        <button type="button" onclick="window.submitted = true">{label}</button>
      </form>
    """)
    guard = guarded(page)

    page.get_by_role("button", name=label).click()

    assert page.evaluate("window.submitted") is None
    assert guard.denials(page)[0]["kind"] == "click"


def test_unrecognized_label_alongside_a_real_next_button_still_moves_on(page):
    """The default-deny rule must not fire when an ordinary Next/Continue sits beside the
    unrecognized-label control -- only when it is the sole remaining action."""
    page.set_content("""
      <form>
        <label>Answer <input id="answer"></label>
        <button type="button" onclick="window.unrelated = true">Some Custom Label</button>
        <button type="submit">Continue</button>
      </form>
    """)
    guard = guarded(page)

    page.get_by_role("button", name="Continue").click()

    assert guard.denials(page) == []


def test_finish_later_beside_a_real_next_button_is_not_swept_up(page):
    """"Finish Later" (a real label in this project's own PageAgent step-1 fixture) is a
    type="button" pause/defer action sitting beside a type="submit" Next -- exactly the shape
    the default-deny rule must not misfire on."""
    page.set_content("""
      <form onsubmit="window.moved = true; return false">
        <input id="first" required>
        <button type="button" onclick="window.finishedLater = true">Finish Later</button>
        <button type="submit">Next</button>
      </form>
    """)
    guard = guarded(page)

    page.get_by_role("button", name="Finish Later").click()

    assert page.evaluate("window.finishedLater") is True
    assert guard.denials(page) == []


def test_a_dropdown_opener_beside_a_file_chooser_is_not_swept_up(page):
    """A BambooHR-style detached-dropdown opener (aria-haspopup, CLAUDE.md's
    interaction.resolve_ant_dropdown() pattern) sitting beside a "Choose File" button -- a safe
    utility label -- must not become the "sole remaining action" just because the file-chooser
    button is excluded from the sibling count."""
    page.set_content("""
      <form>
        <div><label id="stl">State *</label>
          <button type="button" aria-haspopup="true" id="stb" aria-labelledby="stl"
                  onclick="window.dropdownOpened = true">State Select</button>
        </div>
        <div><button type="button" onclick="window.fileChooserOpened = true">Choose File*</button></div>
      </form>
    """)
    guard = guarded(page)

    page.locator("#stb").click()

    assert page.evaluate("window.dropdownOpened") is True
    assert guard.denials(page) == []


@pytest.mark.parametrize("label", ["Add Experience", "Remove", "Upload", "Cancel", "Back", "Skip"])
def test_known_safe_utility_labels_on_type_button_controls_still_work(page, label):
    """Utility controls (add/remove/upload/cancel/back/skip) must not be swept up by the
    default-deny rule meant for unrecognized FINAL labels."""
    page.set_content(f"""
      <form>
        <button type="button" onclick="window.acted = true">{label}</button>
      </form>
    """)
    guard = guarded(page)

    page.get_by_role("button", name=label).click()

    assert page.evaluate("window.acted") is True
    assert guard.denials(page) == []


def test_unlabeled_icon_only_control_is_not_swept_up_by_default_deny(page):
    """An icon-only control with no accessible label is left as a pre-existing, documented
    limitation -- not newly treated as a final submit just because it is alone in its form."""
    page.set_content("""
      <form>
        <button type="button" onclick="window.acted = true"><svg></svg></button>
      </form>
    """)
    guard = guarded(page)

    page.locator("button").click()

    assert page.evaluate("window.acted") is True
    assert guard.denials(page) == []


def test_popup_opened_after_the_guard_is_constructed_is_still_guarded(page):
    """Pass 4, Finding 3 (adversarial review, 7 October 2026): a new tab/popup (window.open(),
    target=_blank -- common on Taleo/BrassRing/Workday external links) used to start at the
    guard script's own default state.active=false and stay that way until something explicitly
    called activate() on it. The guard now wires a context-wide "page" hook at construction time,
    so a brand-new page is covered with no separate per-page activation call anywhere else."""
    page.set_content("""
      <a id="opener" href="#" onclick="window.open('about:blank','_blank'); return false;">Open</a>
    """)
    guard = SubmissionGuardV0(page.context)   # constructed before the popup exists

    with page.context.expect_page() as info:
        page.click("#opener")
    popup = info.value
    popup.wait_for_load_state()
    popup.route("https://ats.example.test/**", lambda route: route.fulfill(
        status=200, content_type="text/html",
        body='<form><button type="submit">Submit Application</button></form>',
    ))
    popup.goto("https://ats.example.test/apply")

    popup.get_by_role("button", name="Submit Application").click()

    assert popup.evaluate("document.querySelector('form').dataset.submitted") is None
    assert guard.denials(popup)[0]["kind"] == "click"


def test_page_that_already_existed_before_guard_construction_is_guarded(page):
    """The other half of Finding 3: a page open BEFORE the guard is constructed must also end up
    protected, without a separate explicit activate() call from application code."""
    page.route("https://ats.example.test/**", lambda route: route.fulfill(
        status=200, content_type="text/html",
        body='<form><button type="submit">Submit Application</button></form>',
    ))
    page.goto("https://ats.example.test/apply")

    guard = SubmissionGuardV0(page.context)   # constructed AFTER this page already navigated

    page.get_by_role("button", name="Submit Application").click()

    assert guard.denials(page)[0]["kind"] == "click"


# --- P0-B1 hardening follow-up, 7 October 2026: listener liveness after document replacement --
# page.set_content() (and, per the independent review, any document.open()-class content
# replacement) was found to strip every addEventListener-based listener this guard installs
# while leaving window.__jaaSubmissionGuardV0 and its API intact -- object presence was not
# proof of active containment. activate() now re-attaches listeners through stable function
# references (idempotent: addEventListener discards a duplicate registration of the same
# function+capture-phase combination) on every call, which is already how often as every
# PageAgent loop iteration in production.

def test_document_replacement_after_activation_strips_the_listener(page):
    """Confirms the vulnerability this hardening targets is real and reproducible, matching the
    independent review's finding exactly: activation BEFORE a document.open()-class replacement
    (what set_content() uses), with no further activation call afterward, leaves the click
    unguarded. This is the baseline the next test's repair is measured against."""
    guard = SubmissionGuardV0(page.context)
    guard.activate(page)
    page.set_content("""
      <form onsubmit="window.submitted = true; return false">
        <button type="submit" id="sb">Submit Application</button>
      </form>
    """)

    page.locator("#sb").click()

    assert page.evaluate("window.submitted") is True
    assert guard.denials(page) == []


def test_reactivation_after_document_replacement_repairs_the_listener(page):
    """The actual hardening: calling activate() again -- the same call PageAgent's
    protect_submission() already makes on every loop iteration -- repairs the stripped
    listener before the next interaction, with no new Python-side call pattern required."""
    guard = SubmissionGuardV0(page.context)
    guard.activate(page)
    page.set_content("""
      <form onsubmit="window.submitted = true; return false">
        <button type="submit" id="sb">Submit Application</button>
      </form>
    """)

    guard.activate(page)   # the repair
    page.locator("#sb").click()

    assert page.evaluate("window.submitted") is None
    assert guard.denials(page)[0]["kind"] == "click"


def test_repeated_activation_creates_no_duplicate_listeners_or_denials(page):
    """Idempotency: calling activate() many times (as normal operation already does) must not
    multiply listeners or denial events for a single click."""
    guard = SubmissionGuardV0(page.context)
    page.set_content("""
      <form onsubmit="window.submitted = true; return false">
        <button type="submit" id="sb">Submit Application</button>
      </form>
    """)
    for _ in range(5):
        guard.activate(page)

    page.locator("#sb").click()

    assert len(guard.denials(page)) == 1


def test_a_genuine_final_control_is_still_blocked_after_listener_repair(page):
    """The structural default-deny rule (Finding 1) must still work correctly after a repair
    cycle, not just the label-matched/structural-type paths exercised by the simpler tests
    above."""
    guard = SubmissionGuardV0(page.context)
    guard.activate(page)
    page.set_content("""
      <form><button type="button" id="sb" onclick="window.submitted = true">Confirm &amp; Send</button></form>
    """)
    guard.activate(page)   # repair

    page.locator("#sb").click()

    assert page.evaluate("window.submitted") is None
    assert guard.denials(page)[0]["kind"] == "click"


def test_normal_real_navigation_is_unaffected_by_the_liveness_repair(page):
    """A real navigation (not a document.open()-class replacement) never loses its listeners in
    the first place; the liveness repair added by this hardening pass must not change that
    already-correct behavior."""
    guard = SubmissionGuardV0(page.context)
    page.route("https://liveness.test/**", lambda route: route.fulfill(
        status=200, content_type="text/html",
        body='<form><button type="submit" id="sb">Submit Application</button></form>',
    ))
    page.goto("https://liveness.test/")
    guard.activate(page)

    page.locator("#sb").click()

    assert guard.denials(page)[0]["kind"] == "click"


@pytest.mark.parametrize("method", ["submit", "requestSubmit"])
def test_direct_javascript_form_submission_is_blocked(page, method):
    install_submit_form(page)
    guard = guarded(page)

    page.evaluate(f"document.querySelector('form').{method}()")

    assert page.evaluate("window.submitted") is None
    assert guard.denials(page)[0]["kind"] == f"javascript_{'form_submit' if method == 'submit' else 'request_submit'}"


def test_vision_selected_control_cannot_bypass_the_guard(page):
    """The real, armed SubmissionGuardV0 remains defense in depth -- but with the P0-B1
    vision-fallback authority fix (7 October 2026), Python's own vision_click_is_safe()
    now refuses this exact control before the click is ever attempted, so no denial is
    recorded by the guard at all. This mirrors the same, now-familiar shift seen earlier
    in test_page_agent_plan_does_not_authorize_submit: refusal moved from the browser
    layer to Python, which is strictly safer, not weaker. See
    test_vision_authority_refuses_with_no_browser_guard_at_all below for the proof that
    Python itself -- not this guard -- is what refuses it."""
    page.set_content("""
      <form onsubmit="window.submitted = true; return false">
        <button type="submit" aria-label="Continue">Submit Application</button>
      </form>
    """)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant._submission_guard = SubmissionGuardV0(page.context)
    fake_vision = SimpleNamespace(read_page=lambda *_args: {
        "page": "application_form", "click": "Continue", "why": "the next step"
    })
    assistant.safe_to_click_for_claude = lambda _page, _label: True

    kind, clicked = assistant.look_and_act(page, fake_vision, "apply")

    assert kind == "application_form" and not clicked
    assert page.evaluate("window.submitted") is None
    assert assistant._submission_guard.denials(page) == []


def test_vision_authority_refuses_with_no_browser_guard_at_all(page):
    """The P0-B1 vision-fallback authority finding, 7 October 2026 (comprehensive final
    closure review): look_and_act() used to click a model-selected control with nothing
    but safe_to_click_for_claude()'s label-text filter in front of it, relying on the
    browser-side SubmissionGuardV0's post-click denial check as the only real protection.
    This proves Python's own, independent, pre-click structural check (vision_click_is_safe())
    refuses the exact same dangerous control with NO guard present at all -- no
    SubmissionGuardV0 instance, no init script, no listeners, no denial counter."""
    page.set_content("""
      <form onsubmit="window.submitted = true; return false">
        <button type="submit" aria-label="Continue">Submit Application</button>
      </form>
    """)
    page.locator("button[type=submit]").click()
    assert page.evaluate("window.submitted") is True  # the control is genuinely dangerous

    # set_content() does not recreate `window` -- a plain property assigned on it, unlike
    # a listener, survives -- so the flag above must be cleared explicitly, or a pass here
    # would just be reading the first click's own leftover state.
    page.evaluate("window.submitted = undefined")
    page.set_content("""
      <form onsubmit="window.submitted = true; return false">
        <button type="submit" aria-label="Continue">Submit Application</button>
      </form>
    """)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant._submission_guard = None
    assistant.protect_submission = lambda _page: None
    fake_vision = SimpleNamespace(read_page=lambda *_args: {
        "page": "application_form", "click": "Continue", "why": "the next step"
    })
    assistant.safe_to_click_for_claude = lambda _page, _label: True

    kind, clicked = assistant.look_and_act(page, fake_vision, "apply")

    assert kind == "application_form" and clicked is False
    assert page.evaluate("window.submitted") is None


# Section 9's full adversarial matrix for the vision-fallback authority fix: every shape
# proven dangerous by a direct click on fresh content, then refused by look_and_act() with
# NO browser guard present at all (_submission_guard = None, protect_submission a no-op).
_VISION_NO_GUARD_ADVERSARIAL_CASES = [
    ("A_native_submit_misleading_label",
     '<form onsubmit="window.submitted=true;return false">'
     '<button type="submit" aria-label="Continue">Continue</button></form>'),
    ("B_default_button_inside_form",
     '<form onsubmit="window.submitted=true;return false"><button>Continue</button></form>'),
    ("C_input_type_submit",
     '<form onsubmit="window.submitted=true;return false">'
     '<input type="submit" value="Continue"></form>'),
    ("D_input_type_image",
     '<form onsubmit="window.submitted=true;return false">'
     '<input type="image" src="data:image/gif;base64,R0lGODlhAQABAAAAACw="></form>'),
    ("E_external_form_attr_submit",
     '<form id="application" onsubmit="window.submitted=true;return false"></form>'
     '<button form="application" type="submit">Continue</button>'),
    ("F_child_span_inside_submit_button",
     '<form onsubmit="window.submitted=true;return false">'
     '<button type="submit"><span>Continue</span></button></form>'),
    ("G_type_button_custom_requestSubmit",
     '<form id="f1" onsubmit="window.submitted=true;return false"></form>'
     '<button type="button" onclick="document.getElementById(\'f1\').requestSubmit()">Continue</button>'),
    ("H_role_button_custom_requestSubmit",
     '<form id="f1" onsubmit="window.submitted=true;return false"></form>'
     '<div role="button" onclick="document.getElementById(\'f1\').requestSubmit()">Continue</div>'),
    ("I_detached_outside_form_custom_trigger",
     '<form id="app" onsubmit="window.submitted=true;return false"></form>'
     '<button type="button" onclick="document.getElementById(\'app\').requestSubmit()">Continue</button>'),
    ("K_icon_leaf_inside_submit_button",
     '<form onsubmit="window.submitted=true;return false">'
     '<button type="submit"><svg viewBox="0 0 1 1"><circle r="1"/></svg></button></form>'),
]


@pytest.mark.parametrize(("case_id", "html"), _VISION_NO_GUARD_ADVERSARIAL_CASES)
def test_vision_fallback_blocks_every_submission_capable_shape_with_no_guard(page, case_id, html):
    page.set_content(f"<html><body>{html}</body></html>")
    page.locator("button, input, [role=button]").first.click(force=True)
    assert page.evaluate("window.submitted") is True, f"{case_id}: fixture is not actually dangerous"

    # set_content() does not recreate `window`, so the flag above must be cleared explicitly
    # before reloading the same fixture, or a pass below would just read leftover state.
    page.evaluate("window.submitted = undefined")
    page.set_content(f"<html><body>{html}</body></html>")
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant._submission_guard = None
    assistant.protect_submission = lambda _page: None
    fake_vision = SimpleNamespace(read_page=lambda *_args: {
        "page": "application_form", "click": "Continue", "why": "the next step"
    })
    assistant.safe_to_click_for_claude = lambda _page, _label: True

    kind, clicked = assistant.look_and_act(page, fake_vision, "apply")

    assert clicked is False, case_id
    assert page.evaluate("window.submitted") is None, case_id


@pytest.mark.parametrize("label", [
    "Finish", "Done", "Complete", "Proceed", "Confirm", "Continue", "Send", "Finalize",
])
def test_vision_fallback_structure_not_vocabulary_blocks_every_unusual_final_label(page, label):
    """Section 9.J: safety must come from structure, not vocabulary -- every one of these
    labels sits on the same genuinely final, type=submit control, with the browser guard
    entirely absent; Python's own structural check must refuse all of them regardless of
    wording."""
    page.set_content(f"""
      <form onsubmit="window.submitted = true; return false">
        <button type="submit">{label}</button>
      </form>
    """)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant._submission_guard = None
    assistant.protect_submission = lambda _page: None
    fake_vision = SimpleNamespace(read_page=lambda *_args: {
        "page": "application_form", "click": label, "why": "the next step"
    })
    assistant.safe_to_click_for_claude = lambda _page, _label: True

    kind, clicked = assistant.look_and_act(page, fake_vision, "apply")

    assert clicked is False, label
    assert page.evaluate("window.submitted") is None, label


def test_vision_fallback_may_still_click_a_safe_dropdown_opener(page):
    """Section 10: the vision fallback is not completely disabled -- a control that
    positively carries opener semantics (aria-haspopup) may still be clicked."""
    page.set_content('<button aria-haspopup="listbox" aria-expanded="false">Country</button>')
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant._submission_guard = None
    assistant.protect_submission = lambda _page: None
    fake_vision = SimpleNamespace(read_page=lambda *_args: {
        "page": "application_form", "click": "Country", "why": "open the list"
    })
    assistant.safe_to_click_for_claude = lambda _page, _label: True

    _kind, clicked = assistant.look_and_act(page, fake_vision, "apply")

    assert clicked is True


def test_vision_fallback_may_still_click_a_safe_tab(page):
    """Section 10: a real role="tab" control, outside any form-submission authority, may
    still be clicked."""
    page.set_content('<button role="tab" aria-selected="false">Experience</button>')
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant._submission_guard = None
    assistant.protect_submission = lambda _page: None
    fake_vision = SimpleNamespace(read_page=lambda *_args: {
        "page": "application_form", "click": "Experience", "why": "switch tab"
    })
    assistant.safe_to_click_for_claude = lambda _page, _label: True

    _kind, clicked = assistant.look_and_act(page, fake_vision, "apply")

    assert clicked is True


def test_vision_fallback_may_still_click_a_safe_navigational_link(page):
    """Section 10: a normal anchor with a real, non-JavaScript href may still be clicked."""
    page.set_content('<a href="#jobs">View Jobs</a>')
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant._submission_guard = None
    assistant.protect_submission = lambda _page: None
    fake_vision = SimpleNamespace(read_page=lambda *_args: {
        "page": "application_form", "click": "View Jobs", "why": "see other jobs"
    })
    assistant.safe_to_click_for_claude = lambda _page, _label: True

    _kind, clicked = assistant.look_and_act(page, fake_vision, "apply")

    assert clicked is True


def test_plain_boolean_cannot_authorize_gateway_submission(page):
    install_submit_form(page)
    guard = guarded(page)
    button = page.get_by_role("button", name="Submit Application")

    assert not guard.submit_verified(page, button, True)
    assert page.evaluate("window.submitted") is None
    assert guard.denials(page) == []


def test_verified_gateway_permits_one_structured_authorized_submit(page, tmp_path):
    install_submit_form(page)
    guard = guarded(page)
    screenshot = tmp_path / "evidence.png"
    html = tmp_path / "evidence.html"
    screenshot.write_bytes(b"synthetic evidence")
    html.write_text("<html></html>", encoding="utf-8")
    decision = safety.AutoSubmitDecision(
        eligible=True,
        field_comparisons=[
            safety.FieldComparison("Name", "Owner", "Owner", "profile.full_name", True)
        ],
        evidence_paths={"screenshot": str(screenshot), "html": str(html)},
    )
    tracker = JobTracker(tmp_path / "applications.db")
    tracker.create(dedup_key="job-1", title="Engineer", company="Example", url="https://jobs.example/1")

    class ClickChecksCommittedState:
        def __init__(self, locator):
            self.locator = locator

        def evaluate(self, *args):
            return self.locator.evaluate(*args)

        def click(self, **kwargs):
            assert tracker.get_submission_effect_state("job-1") == "DISPATCHED"
            assert [event["kind"] for event in tracker.submission_safety_events("job-1")][-1] == \
                "SUBMISSION_DISPATCHED"
            self.locator.click(**kwargs)

    assert guard.submit_verified(
        page, ClickChecksCommittedState(page.get_by_role("button", name="Submit Application")),
        decision, tracker=tracker, dedup_key="job-1",
    )
    assert page.evaluate("window.submitted") == 1
    assert guard.denials(page) == []
    assert tracker.get_submission_effect_state("job-1") == "DISPATCHED"


def test_human_handoff_disarms_automation_containment(page, tmp_path):
    install_submit_form(page)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant._submission_guard = guarded(page)
    assistant.tracker = JobTracker(tmp_path / "applications.db")
    assistant.tracker.create(dedup_key="job-1", title="Engineer", company="Example")
    assistant.application_key = "job-1"

    assistant.human_handoff(page)
    page.get_by_role("button", name="Submit Application").click()

    assert page.evaluate("window.submitted") == 1
    assert assistant._submission_guard.denials(page) == []
    assert assistant._submission_execution_state == "parked"
    assert assistant.tracker.submission_safety_events("job-1")[-1]["kind"] == "MANUAL_HANDOFF_STARTED"


def test_parked_handoff_blocks_pending_page_agent_and_gateway_work(page, tmp_path):
    install_submit_form(page)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant._submission_guard = guarded(page)
    assistant.tracker = JobTracker(tmp_path / "applications.db")
    assistant.tracker.create(dedup_key="job-1", title="Engineer", company="Example")
    assistant.application_key = "job-1"
    assistant.human_handoff(page)

    agent = page_agent.PageAgent.__new__(page_agent.PageAgent)
    agent.assistant = assistant
    outcome = agent.press_next(page, None, [])

    assert outcome[0] == "stop"
    assert assistant.click_verified_submit(page, None) is False
    assert assistant.tracker.get_submission_effect_state("job-1") is None
    assert page.evaluate("window.submitted") is None
    assert assistant._submission_guard.denials(page) == []


def test_handoff_waits_for_inflight_page_agent_progress_to_drain(page, tmp_path):
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    guard = SimpleNamespace(deactivated=False)
    guard.bind_application = lambda _tracker, _key: None
    guard.deactivate = lambda _page: setattr(guard, "deactivated", True)
    assistant._submission_guard = guard
    assistant.tracker = JobTracker(tmp_path / "applications.db")
    assistant.tracker.create(dedup_key="job-1", title="Engineer", company="Example")
    assistant.application_key = "job-1"
    agent = page_agent.PageAgent.__new__(page_agent.PageAgent)
    agent.assistant = assistant

    progress_entered = Event()
    finish_progress = Event()
    handoff_started = Event()
    handoff_finished = Event()
    completed_progress = []

    def pending_progress(_page, _plan, _controls):
        progress_entered.set()
        assert finish_progress.wait(5)
        completed_progress.append(True)
        return "moved", page, "navigation completed"

    agent._press_next_locked = pending_progress
    progression = Thread(target=lambda: agent.press_next(page, None, []))

    def perform_handoff():
        handoff_started.set()
        assistant.human_handoff(page)
        handoff_finished.set()

    handoff = Thread(target=perform_handoff)
    progression.start()
    assert progress_entered.wait(5)
    handoff.start()
    try:
        assert handoff_started.wait(5)
        assert not handoff_finished.wait(0.1)
    finally:
        finish_progress.set()
        progression.join(5)
        handoff.join(5)

    assert not progression.is_alive()
    assert not handoff.is_alive()
    assert completed_progress == [True]
    assert handoff_finished.is_set()
    assert assistant._submission_execution_state == "parked"
    assert guard.deactivated
    assert assistant.tracker.submission_safety_events("job-1")[-1]["kind"] == \
        "MANUAL_HANDOFF_STARTED"
    assert agent.press_next(page, None, [])[0] == "stop"


def test_handoff_storage_failure_keeps_guard_armed(page, tmp_path, monkeypatch):
    install_submit_form(page)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant._submission_guard = guarded(page)
    assistant.tracker = JobTracker(tmp_path / "applications.db")
    assistant.tracker.create(dedup_key="job-1", title="Engineer", company="Example")
    assistant.application_key = "job-1"

    def fail_event(*_args, **_kwargs):
        raise OSError("local state store unavailable")

    monkeypatch.setattr(assistant.tracker, "record_submission_safety_event", fail_event)
    with pytest.raises(OSError):
        assistant.human_handoff(page)
    page.get_by_role("button", name="Submit Application").click()

    assert assistant._submission_execution_state == "parked"
    assert page.evaluate("window.submitted") is None
    assert assistant._submission_guard.denials(page)[0]["kind"] == "click"


def test_resuming_automation_rearms_guard_before_navigation(page, tmp_path):
    install_submit_form(page)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant._submission_guard = guarded(page)
    assistant.tracker = JobTracker(tmp_path / "applications.db")
    assistant.tracker.create(dedup_key="job-1", title="Engineer", company="Example")
    assistant.application_key = "job-1"

    assistant.human_handoff(page)
    assistant.resume_automation(page)
    page.get_by_role("button", name="Submit Application").click()

    assert assistant._submission_execution_state == "active"
    assert page.evaluate("window.submitted") is None
    assert assistant._submission_guard.denials(page)[0]["kind"] == "click"
