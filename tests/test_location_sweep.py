"""
Tests for Wipe-and-Enforce Location Sweep (Cascading Field Dependency Resolution).

Verifies the 4 strict rules:
1. Topological Execution Order: Never fill location fields simultaneously. Force sequence: Country -> State/Province -> City.
2. Clear and Reset: For Country, explicitly clear incorrect default selection (Afghanistan). Use fill_and_dispatch to force canonical country.
3. Network Quiescence: Right after updating Country, block execution and wait for networkidle state before touching State.
4. Dependent Repopulation: Once network settles, clear corrupted State/Province (Badakhshān), verify options in dropdown portal,
   and use fill_and_dispatch to select true profile state (Virginia). Then populate City (Fairfax).
5. Sub-Card Coverage: Pre-navigation commit_draft_cards sweeps and enforces location on education and experience cards before commit.
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
# Rule 1 to 4: Wipe-and-Enforce Sweep with Cascading Dynamic Dependencies
# ==============================================================================

def test_wipe_and_enforce_sweep_topological_execution_and_dependent_repopulation(page):
    """Verifies that Country is updated first, network quiescence allows regional state
    options to load, and State is then cleared of Badakhshān and updated to Virginia."""
    page.set_content("""
    <html>
    <body>
      <form id="contact_form">
        <label for="country_field">Country / Region</label>
        <select id="country_field" name="country">
          <option value="AF" selected>Afghanistan</option>
          <option value="US">United States</option>
          <option value="CA">Canada</option>
        </select>

        <label for="state_field">State / Province</label>
        <select id="state_field" name="state">
          <option value="BAD" selected>Badakhshān</option>
          <option value="KBL">Kabul</option>
        </select>

        <label for="city_field">City</label>
        <input id="city_field" name="city" type="text" value="Fairfax" />
      </form>

      <script>
        window.eventLog = [];
        const cSelect = document.getElementById('country_field');
        const sSelect = document.getElementById('state_field');
        const cityInput = document.getElementById('city_field');

        cSelect.addEventListener('change', (e) => {
            window.eventLog.push({ field: 'country', value: e.target.options[e.target.selectedIndex].text, time: performance.now() });
            // Simulate AJAX re-render of dependent state options when Country becomes United States
            if (e.target.value === 'US' || e.target.options[e.target.selectedIndex].text.includes('United States')) {
                setTimeout(() => {
                    sSelect.innerHTML = `
                        <option value="">Select State</option>
                        <option value="VA">Virginia</option>
                        <option value="MD">Maryland</option>
                        <option value="DC">District of Columbia</option>
                    `;
                    window.statesRepopulated = true;
                }, 100);
            }
        });

        sSelect.addEventListener('change', (e) => {
            window.eventLog.push({ field: 'state', value: e.target.options[e.target.selectedIndex].text, time: performance.now() });
        });

        cityInput.addEventListener('input', (e) => {
            window.eventLog.push({ field: 'city', value: e.target.value, time: performance.now() });
        });
      </script>
    </body>
    </html>
    """)

    # Initial state assertions (simulating resume parser hallucination)
    assert page.locator("#country_field").input_value() == "AF"
    assert page.locator("#state_field").input_value() == "BAD"
    assert page.locator("#city_field").input_value() == "Fairfax"

    # Execute Wipe-and-Enforce Sweep
    success = interaction.wipe_and_enforce_location_sweep(
        page,
        country="United States",
        state="Virginia",
        city="Fairfax",
    )
    assert success is True

    # Assert Rule 2: Country is reset to canonical profile country
    country_val = page.locator("#country_field").evaluate("el => el.options[el.selectedIndex].text")
    assert country_val == "United States"

    # Assert Rule 4: Corrupted Badakhshān was cleared, options were populated, and Virginia was selected
    state_val = page.locator("#state_field").evaluate("el => el.options[el.selectedIndex].text")
    assert state_val == "Virginia"

    # Assert City is set/verified
    assert page.locator("#city_field").input_value() == "Fairfax"

    # Assert Rule 1: Topological order (Country change event preceded State change event)
    event_log = page.evaluate("() => window.eventLog")
    assert len(event_log) >= 2
    country_events = [e for e in event_log if e["field"] == "country"]
    state_events = [e for e in event_log if e["field"] == "state"]

    assert len(country_events) > 0
    assert len(state_events) > 0
    assert country_events[0]["time"] < state_events[0]["time"]


# ==============================================================================
# Combobox / Searchable Inputs Test
# ==============================================================================

def test_wipe_and_enforce_sweep_combobox_inputs(page):
    """Verifies that Wipe-and-Enforce Sweep clears and populates combobox inputs."""
    page.set_content("""
    <html>
    <body>
      <div class="form-section">
        <label>Country</label>
        <input id="cb_country" role="combobox" type="text" value="Afghanistan" />

        <label>State / Province</label>
        <input id="cb_state" role="combobox" type="text" value="Badakhshān" />

        <label>City</label>
        <input id="cb_city" type="text" value="Fairfax" />
      </div>
    </body>
    </html>
    """)

    success = interaction.wipe_and_enforce_location_sweep(
        page,
        country="United States",
        state="Virginia",
        city="Fairfax",
    )
    assert success is True

    assert page.locator("#cb_country").input_value() == "United States"
    assert page.locator("#cb_state").input_value() == "Virginia"
    assert page.locator("#cb_city").input_value() == "Fairfax"


# ==============================================================================
# Sub-Card & Draft Card Sweep Test
# ==============================================================================

def test_commit_draft_cards_sweeps_location_before_commit(page):
    """Verifies that commit_draft_cards executes the Wipe-and-Enforce sweep
    inside experience/education card containers before clicking internal Save Entry buttons."""
    page.set_content("""
    <html>
    <body>
      <div data-automation-id="workExperience-entry-0" class="card">
        <h3>Senior Engineer</h3>
        <label for="work_country">Country</label>
        <select id="work_country">
          <option value="AF" selected>Afghanistan</option>
          <option value="US">United States</option>
        </select>

        <label for="work_state">State / Province</label>
        <select id="work_state">
          <option value="BAD" selected>Badakhshān</option>
          <option value="VA">Virginia</option>
        </select>

        <label for="work_city">City</label>
        <input id="work_city" type="text" value="Fairfax" />

        <button id="save_card_btn" type="button">Save Entry</button>
      </div>

      <script>
        window.committedData = null;
        document.getElementById('save_card_btn').addEventListener('click', () => {
            const c = document.getElementById('work_country');
            const s = document.getElementById('work_state');
            const city = document.getElementById('work_city');
            window.committedData = {
                country: c.options[c.selectedIndex].text,
                state: s.options[s.selectedIndex].text,
                city: city.value
            };
        });
      </script>
    </body>
    </html>
    """)

    committed_count = interaction.commit_draft_cards(page)
    assert committed_count == 1

    committed_data = page.evaluate("() => window.committedData")
    assert committed_data is not None
    # Verify location was wiped and enforced to United States and Virginia BEFORE save was clicked
    assert committed_data["country"] == "United States"
    assert committed_data["state"] == "Virginia"
    assert committed_data["city"] == "Fairfax"


# ==============================================================================
# Module & Class Export Verification
# ==============================================================================

def test_wipe_and_enforce_location_sweep_exported_and_accessible():
    """Verifies wipe_and_enforce_location_sweep is accessible across modules and classes."""
    assert hasattr(interaction, "wipe_and_enforce_location_sweep")
    assert callable(interaction.wipe_and_enforce_location_sweep)

    assert hasattr(browser_automation, "wipe_and_enforce_location_sweep")
    assert hasattr(browser_automation.JobApplicationAssistant, "wipe_and_enforce_location_sweep")

    assert hasattr(page_agent, "wipe_and_enforce_location_sweep")
    assert hasattr(page_agent.PageAgent, "wipe_and_enforce_location_sweep")
