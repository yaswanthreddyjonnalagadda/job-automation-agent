"""Phase 0-B4: a Next/Continue/Save press is classified by what the page shows afterward,
not assumed to have "moved" merely because the rendered text changed at all.

`PageAgent._press_next_locked()`'s only post-press check was a blunt ref-stripped whole-page
text-equality diff; a validation error was only inspected once that diff had already decided
"retry," never on the path where it decided "moved" (docs/security/phase0-b4-discovery.md
section 5/7). These tests cover the fix: a visible validation error after a press that DID
change the page's text must still be read as "retry," and the existing "nothing changed at
all" and "genuinely advanced, no error" cases must be unchanged.
"""
import re
from types import SimpleNamespace

import pytest

import config
import page_agent
import safety
from browser_automation import JobApplicationAssistant


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
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    job = SimpleNamespace(title="Engineer", company="Acme", url="https://jobs.example.com/apply")
    return page_agent.PageAgent(
        assistant, SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email=""),
        config.get_user_profile(), SimpleNamespace(raw_text="x"), job, resume_file=None)


def press(page, a):
    controls = page_agent.parse_snapshot(a.snapshot(page))
    plan = page_agent.PagePlan(
        page_kind="application_form", next_ref=ref_of(a.snapshot(page), "button", "Next"),
        next_label="Next", next_kind="next_step")
    return a.press_next(page, plan, controls)


def test_a_press_that_genuinely_advances_with_no_error_is_moved(page):
    page.set_content(
        '<h2>Step 1</h2><button onclick="document.body.innerHTML='
        '\'<h2>Step 2</h2><p>Welcome to step 2</p>\'">Next</button>')
    a = make_agent()
    kind, _, _ = press(page, a)
    assert kind == "moved"


def test_a_press_that_changes_nothing_at_all_is_retry(page):
    # this.blur() keeps the stripped before/after snapshots byte-identical -- otherwise even a
    # no-op button shifts which element is marked [active] (focus), which the existing
    # ref/active-stripping regex does not fully normalize away (a pre-existing, unrelated
    # quirk of the before/after text diff, not something Phase 0-B4 changes or needs to fix).
    page.set_content('<h2>Step 1</h2><button onclick="this.blur()">Next</button>')
    a = make_agent()
    kind, _, why = press(page, a)
    assert kind == "retry" and "did not move on" in why


def test_a_press_that_changes_text_but_still_shows_a_validation_error_is_retry(page):
    """The confirmed gap: the rendered text changes (new content appears) but a validation
    error is also visibly present -- this must not be read as a successful advance merely
    because *something* changed."""
    page.set_content(
        '<h2>Step 1</h2><button onclick="document.body.innerHTML='
        '\'<h2>Step 1</h2><div role=&quot;alert&quot;>Please enter your last name</div>'
        '<p>extra content appeared</p>\'">Next</button>')
    a = make_agent()
    kind, _, why = press(page, a)
    assert kind == "retry" and "error" in why.lower() and "last name" in why.lower()


def test_a_press_that_changes_text_with_no_error_text_at_all_is_still_moved(page):
    """Guards against over-triggering: ordinary new page content that happens to contain
    none of page_errors()'s error-shaped phrasing must still read as moved."""
    page.set_content(
        '<h2>Step 1</h2><button onclick="document.body.innerHTML='
        '\'<h2>Step 2</h2><p>Tell us about your education</p>\'">Next</button>')
    a = make_agent()
    kind, _, _ = press(page, a)
    assert kind == "moved"
