"""
Tests for Phase 2: Core Primitives, Seam Separation & Ant Design Resolution.

Verifies:
- Task 2.1: fill_and_dispatch native fill + synthetic bubbling events (input, change, blur)
- Task 2.2: Perception vs. Interaction seam separation
- Task 2.3: Dayforce / Ant Design rc-select dropdown resolution via wrapper mousedown, detached portal matching, and hide verification
- Task 2.4: Automated pre-navigation resume draft card commit sweep
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from playwright.sync_api import sync_playwright

import browser_automation
import interaction
import page_agent
import perception
import safety


@pytest.fixture(scope="module")
def browser():
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


# ==============================================================================
# Task 2.1: Universal Synthetic Event Dispatcher
# ==============================================================================

def test_fill_and_dispatch_dispatches_bubbling_synthetic_events(page):
    """Verifies fill_and_dispatch calls native fill and dispatches bubbling
    'input', 'change', and 'blur' events."""
    page.set_content("""
    <html><body>
      <input id="target_input" type="text" />
      <script>
        window.dispatchedEvents = [];
        const inp = document.getElementById('target_input');
        ['input', 'change', 'blur'].forEach(eventType => {
            inp.addEventListener(eventType, e => {
                window.dispatchedEvents.push({
                    type: e.type,
                    bubbles: e.bubbles,
                    value: inp.value
                });
            });
        });
      </script>
    </body></html>
    """)

    loc = page.locator("#target_input")
    interaction.fill_and_dispatch(loc, "Engineering Director")

    # Verify input received the value
    assert loc.input_value() == "Engineering Director"

    # Verify event types and bubbling flag
    events = page.evaluate("() => window.dispatchedEvents")
    event_types = [e["type"] for e in events]
    assert "input" in event_types
    assert "change" in event_types
    assert "blur" in event_types

    # Ensure each synthetic event bubbled as required for reactive state tracking
    for e in events:
        assert e["bubbles"] is True


def test_fill_and_dispatch_accessible_from_modules_and_classes(page):
    """Verifies fill_and_dispatch is exported and accessible from browser_automation,
    page_agent, JobApplicationAssistant, and PageAgent."""
    page.set_content("<input id='test_box' />")
    loc = page.locator("#test_box")

    # From module imports
    assert hasattr(browser_automation, "fill_and_dispatch")
    assert hasattr(page_agent, "fill_and_dispatch")

    # From classes
    assert hasattr(browser_automation.JobApplicationAssistant, "fill_and_dispatch")
    assert hasattr(page_agent.PageAgent, "fill_and_dispatch")

    # Calling through class methods
    browser_automation.JobApplicationAssistant.fill_and_dispatch(loc, "val1")
    assert loc.input_value() == "val1"

    page_agent.PageAgent.fill_and_dispatch(loc, "val2")
    assert loc.input_value() == "val2"


# ==============================================================================
# Task 2.2: Perception vs. Interaction Seam Separation
# ==============================================================================

def test_perception_functions_are_read_only(page):
    """Verifies that perception functions inspect DOM without triggering click or mutation."""
    page.set_content("""
    <html><body>
      <div class="ant-select">
        <div class="ant-select-selector">
          <input id="picker_input" class="ant-select-selection-search-input" value="Select State" />
        </div>
      </div>
      <script>
        window.mutated = false;
        document.getElementById('picker_input').addEventListener('click', () => { window.mutated = true; });
        document.getElementById('picker_input').addEventListener('change', () => { window.mutated = true; });
      </script>
    </body></html>
    """)

    inp = page.locator("#picker_input")

    # Perception checks
    assert perception.is_ant_dropdown(inp) is True
    assert perception.is_field_active(inp) is True
    attrs = perception.read_control_attributes(inp)
    assert attrs["value"] == "Select State"

    # Confirm perception did not mutate or trigger events
    assert page.evaluate("() => window.mutated") is False


# ==============================================================================
# Task 2.3: Dayforce / Ant Design Dropdown Resolution
# ==============================================================================

ANT_DESIGN_HTML = """
<html><body>
  <div id="wrapper" class="ant-select ant-select-single ant-select-show-arrow">
    <div class="ant-select-selector" id="selector_box">
      <span class="ant-select-selection-search">
        <input id="obscured_input" class="ant-select-selection-search-input"
               role="combobox" aria-haspopup="listbox" style="pointer-events: none; opacity: 0;" />
      </span>
      <span class="ant-select-selection-item" id="selected_text"></span>
    </div>
  </div>

  <!-- Detached portal mounted at root of body -->
  <div id="portal" class="ant-select-dropdown ant-select-dropdown-hidden" style="position: absolute;">
    <div role="listbox">
      <div class="ant-select-item ant-select-item-option" role="option" data-value="United States">
        <div class="ant-select-item-option-content">United States</div>
      </div>
      <div class="ant-select-item ant-select-item-option" role="option" data-value="Canada">
        <div class="ant-select-item-option-content">Canada</div>
      </div>
      <div class="ant-select-item ant-select-item-option" role="option" data-value="United Kingdom">
        <div class="ant-select-item-option-content">United Kingdom</div>
      </div>
    </div>
  </div>

  <script>
    window.mousedownFired = false;
    const selector = document.getElementById('selector_box');
    const portal = document.getElementById('portal');
    const selectedText = document.getElementById('selected_text');
    const input = document.getElementById('obscured_input');

    // Ant Design rc-select opens specifically on mousedown
    selector.addEventListener('mousedown', (e) => {
        window.mousedownFired = true;
        portal.classList.remove('ant-select-dropdown-hidden');
    });

    // Clicking an option selects it and hides the portal
    portal.querySelectorAll('.ant-select-item-option').forEach(opt => {
        opt.addEventListener('click', () => {
            const val = opt.getAttribute('data-value');
            selectedText.textContent = val;
            input.value = val;
            portal.classList.add('ant-select-dropdown-hidden');
        });
    });
  </script>
</body></html>
"""

def test_resolve_ant_dropdown_success(page):
    """Verifies resolve_ant_dropdown dispatches mousedown on wrapper, matches
    detached option case-insensitively, clicks option, and confirms portal hides."""
    page.set_content(ANT_DESIGN_HTML)

    input_loc = page.locator("#obscured_input")
    assert perception.is_ant_dropdown(input_loc) is True

    # Call resolver with case-insensitive target ("united states")
    success = interaction.resolve_ant_dropdown(page, input_loc, "united states")
    assert success is True

    # 1. mousedown was dispatched on the selector wrapper
    assert page.evaluate("() => window.mousedownFired") is True

    # 2. Correct option was selected
    assert page.locator("#selected_text").inner_text() == "United States"

    # 3. Portal dropdown hidden class was restored
    portal_classes = page.locator("#portal").get_attribute("class")
    assert "ant-select-dropdown-hidden" in portal_classes


def test_resolve_ant_dropdown_returns_false_for_missing_option(page):
    """Verifies resolve_ant_dropdown fails gracefully if target option is absent."""
    page.set_content(ANT_DESIGN_HTML)
    input_loc = page.locator("#obscured_input")

    success = interaction.resolve_ant_dropdown(page, input_loc, "Nonexistent Country", timeout_ms=1_000)
    assert success is False


# ==============================================================================
# Task 2.4: Automate Resume Draft Card Commits
# ==============================================================================

DRAFT_CARDS_HTML = """
<html><body>
  <div class="resume-experience-card" data-automation-id="workExperience-0">
    <h3>Senior Network Engineer</h3>
    <input id="role_desc" value="Lead cloud networking architecture" />
    <button id="save_card_1" onclick="window.card1_committed = true; this.remove();">Save Entry</button>
  </div>

  <div class="resume-education-card" data-automation-id="education-0">
    <h3>B.Tech Computer Science</h3>
    <input id="school_name" value="JNTU" />
    <button id="save_card_2" onclick="window.card2_committed = true; this.remove();">Update</button>
  </div>

  <div class="unrelated-section">
    <button id="final_submit_btn" onclick="window.submit_clicked = true;">Submit application</button>
    <button id="next_btn" onclick="window.next_clicked = true;">Next</button>
  </div>

  <script>
    window.card1_committed = false;
    window.card2_committed = false;
    window.submit_clicked = false;
    window.next_clicked = false;
  </script>
</body></html>
"""

def test_find_active_draft_cards_perception(page):
    """Verifies perception layer identifies active draft cards without clicking."""
    page.set_content(DRAFT_CARDS_HTML)

    cards = perception.find_active_draft_cards(page)
    assert len(cards) == 2
    buttons = [b for card in cards for b in card["commit_buttons"]]
    assert "Save Entry" in buttons
    assert "Update" in buttons

    # Ensure no buttons were clicked
    assert page.evaluate("() => window.card1_committed") is False
    assert page.evaluate("() => window.card2_committed") is False


def test_commit_draft_cards_interaction(page):
    """Verifies commit_draft_cards sweeps DOM, clicks internal card commit buttons,
    and never clicks page-level submit buttons."""
    page.set_content(DRAFT_CARDS_HTML)

    committed = interaction.commit_draft_cards(page)
    assert committed == 2

    # Both internal card buttons were clicked
    assert page.evaluate("() => window.card1_committed") is True
    assert page.evaluate("() => window.card2_committed") is True

    # The submit button was NEVER touched
    assert page.evaluate("() => window.submit_clicked") is False
    assert page.evaluate("() => window.next_clicked") is False


def test_commit_open_sections_preserves_safety_against_submit_label(page):
    """Verifies commit_open_sections never clicks buttons with submit labels."""
    page.set_content("""
    <html><body>
      <div class="card"><input value="x"/><button onclick="window.sub=true;">Submit</button></div>
      <div class="card"><input value="y"/><button onclick="window.done=true;">Done</button></div>
      <script>window.sub = false; window.done = false;</script>
    </body></html>
    """)

    committed = interaction.commit_draft_cards(page)
    assert committed == 1
    assert page.evaluate("() => window.done") is True
    assert page.evaluate("() => window.sub") is False
