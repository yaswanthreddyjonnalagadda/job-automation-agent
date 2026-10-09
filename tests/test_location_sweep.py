"""
The owner's address sweep (interaction.wipe_and_enforce_location_sweep).

What it must do, and what the 23 September version got wrong:

1. Country first, then -- once the page has loaded that country's states --
   state, then city. (Kept.)
2. Correct any value the site put there that contradicts the profile, not
   only two place names written into the code as "wrong". (Was: literals.)
3. Never change a value the owner entered, and leave the site's values
   alone when SITE_PREFILL_POLICY=leave or no observer ran. (Was: it
   overwrote whatever it found.)
4. Assume nothing: a place the profile leaves empty is left empty. (Was: it
   fell back to one owner's country, state and city for everyone.)
5. Never touch a work or education entry: its location belongs to the
   entry. (Was: it wrote the owner's home into every entry.)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st
from playwright.sync_api import sync_playwright

import browser_automation
import geo_reference
import interaction
import page_agent
import provenance


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    """A page watched by the provenance observer, as every agent page is."""
    context = browser.new_context()
    provenance.install(context)
    pg = context.new_page()
    yield pg
    context.close()


def show(page, html):
    page.set_content(html)
    provenance.install_on_page(page)


def cascading_form(country_default: str, province_default: str) -> str:
    return f"""
    <form id="contact_form">
      <label for="country_field">Country / Region</label>
      <select id="country_field" name="country">
        <option value="">- Select -</option>
        <option value="XX" selected>{country_default}</option>
        <option value="US">United States</option>
        <option value="CA">Canada</option>
      </select>
      <label for="state_field">State / Province</label>
      <select id="state_field" name="state">
        <option value="P1" selected>{province_default}</option>
        <option value="P2">Another Province</option>
      </select>
      <label for="city_field">City</label>
      <input id="city_field" name="city" type="text" value="" />
    </form>
    <script>
      window.eventLog = [];
      const cSelect = document.getElementById('country_field');
      const sSelect = document.getElementById('state_field');
      cSelect.addEventListener('change', (e) => {{
        window.eventLog.push({{ field: 'country', time: performance.now() }});
        if (e.target.value === 'US') {{
          setTimeout(() => {{
            sSelect.innerHTML = '<option value="">Select State</option><option value="VA">Virginia</option>'
                              + '<option value="MD">Maryland</option><option value="DC">District of Columbia</option>';
          }}, 100);
        }}
      }});
      sSelect.addEventListener('change', () => window.eventLog.push({{ field: 'state', time: performance.now() }}));
    </script>"""


def selected(page, selector):
    return page.locator(selector).evaluate("el => el.options[el.selectedIndex].text")


def test_disabled_state_is_deferred_without_clearing_or_recording(page):
    show(page, '<label for="state">State</label><input id="state" disabled value="No Selection">')
    recorded = []
    interaction.wipe_and_enforce_location_sweep(
        page, state='Virginia', record_callback=lambda *args: recorded.append(args))
    assert page.locator('#state').input_value() == 'No Selection'
    assert page.locator('#state').is_disabled()
    assert recorded == []


def test_sap_country_row_enables_state_before_selection(page):
    show(page, '''
    <label for="country_input">Country</label>
    <input id="country_input" class="rcmpaginatedselectinput" aria-owns="countries">
    <button id="country_selectButton" onclick="document.querySelector('#countries').hidden=false">Open country</button>
    <ul id="countries" hidden><li onclick="document.querySelector('#country_input').value=this.textContent;document.querySelector('#state_input').disabled=false;this.parentNode.hidden=true">United States</li></ul>
    <label for="state_input">State</label>
    <input id="state_input" disabled class="rcmpaginatedselectinput" aria-owns="states">
    <button id="state_selectButton" onclick="document.querySelector('#states').hidden=false">Open state</button>
    <ul id="states" hidden><li onclick="document.querySelector('#state_input').value=this.textContent;this.parentNode.hidden=true">Virginia</li></ul>
    ''')
    recorded = []
    interaction.wipe_and_enforce_location_sweep(
        page, country='United States', state='Virginia',
        record_callback=lambda *args: recorded.append(args))
    assert page.locator('#country_input').input_value() == 'United States'
    assert page.locator('#state_input').input_value() == 'Virginia'
    assert [answer for _, answer in recorded] == ['United States', 'Virginia']


# The site's first entries on real forms: Afghanistan on some, "Aaland Islands"
# on Chobani's, Albania on others. None of them may matter.
@pytest.mark.parametrize("country_default,province_default", [
    ("Afghanistan", "Badakhshān"),
    ("Aaland Islands", "Brändö"),
    ("Albania", "Berat"),
])
def test_any_site_default_is_corrected_country_first(page, country_default, province_default):
    show(page, cascading_form(country_default, province_default))
    assert interaction.wipe_and_enforce_location_sweep(
        page, country="United States", state="Virginia", city="Fairfax") is True
    assert selected(page, "#country_field") == "United States"
    assert selected(page, "#state_field") == "Virginia"
    assert page.locator("#city_field").input_value() == "Fairfax"
    log = page.evaluate("() => window.eventLog")
    first_country = min(e["time"] for e in log if e["field"] == "country")
    first_state = min(e["time"] for e in log if e["field"] == "state")
    assert first_country < first_state          # the state list depends on the country


@pytest.mark.parametrize("country_default,province_default", [
    ("Afghanistan", "Badakhshān"), ("Aaland Islands", "Brändö"),
])
def test_combobox_inputs_are_corrected_too(page, country_default, province_default):
    show(page, f"""<div class="form-section">
        <label for="cb_country">Country</label><input id="cb_country" role="combobox" value="{country_default}">
        <label for="cb_state">State / Province</label><input id="cb_state" role="combobox" value="{province_default}">
        <label for="cb_city">City</label><input id="cb_city" value="">
      </div>""")
    interaction.wipe_and_enforce_location_sweep(page, country="United States", state="Virginia", city="Fairfax")
    assert page.locator("#cb_country").input_value() == "United States"
    assert page.locator("#cb_state").input_value() == "Virginia"
    assert page.locator("#cb_city").input_value() == "Fairfax"


def test_the_owners_own_choice_is_never_overwritten(page):
    """A person who really types Afghanistan keeps it."""
    show(page, """<label for="cb_country">Country</label><input id="cb_country" role="combobox" value="">
                  <label for="cb_city">City</label><input id="cb_city" value="">""")
    provenance.set_agent_busy(page, False)                 # the owner's turn
    page.fill("#cb_country", "Afghanistan")                # a person's own, trusted typing
    page.fill("#cb_city", "Kabul")
    provenance.set_agent_busy(page, True)
    interaction.wipe_and_enforce_location_sweep(page, country="United States", state="Virginia", city="Fairfax")
    assert page.locator("#cb_country").input_value() == "Afghanistan"
    assert page.locator("#cb_city").input_value() == "Kabul"


def test_leave_policy_keeps_the_sites_values_and_fills_only_empty_ones(page, monkeypatch):
    monkeypatch.setenv("SITE_PREFILL_POLICY", "leave")
    show(page, cascading_form("Afghanistan", "Badakhshān"))
    interaction.wipe_and_enforce_location_sweep(page, country="United States", state="Virginia", city="Fairfax")
    assert selected(page, "#country_field") == "Afghanistan"
    assert page.locator("#city_field").input_value() == "Fairfax"


def test_a_select_prompt_counts_as_empty_even_under_leave(page, monkeypatch):
    """"- Select -" is nobody's answer: it is filled whatever the policy."""
    monkeypatch.setenv("SITE_PREFILL_POLICY", "leave")
    show(page, '<label for="c">Country</label><select id="c"><option selected>- Select -</option>'
               '<option>Canada</option><option>United States</option></select>')
    interaction.wipe_and_enforce_location_sweep(page, country="United States", state="", city="")
    assert selected(page, "#c") == "United States"


def test_without_the_observer_a_present_value_is_left_alone(browser):
    context = browser.new_context()
    try:
        bare = context.new_page()
        bare.set_content(cascading_form("Afghanistan", "Badakhshān"))
        interaction.wipe_and_enforce_location_sweep(bare, country="United States", state="Virginia", city="Fairfax")
        assert selected(bare, "#country_field") == "Afghanistan"      # cannot tell the site from the owner
        assert bare.locator("#city_field").input_value() == "Fairfax"  # an empty field is still filled
    finally:
        context.close()


def test_nothing_is_assumed_when_the_profile_names_no_place(page):
    from types import SimpleNamespace

    show(page, cascading_form("Afghanistan", "Badakhshān"))
    empty = SimpleNamespace(country="", state="", city="")
    assert interaction.wipe_and_enforce_location_sweep(page, profile=empty) is False
    assert selected(page, "#country_field") == "Afghanistan"
    assert page.locator("#city_field").input_value() == ""


def test_a_work_entrys_location_is_never_rewritten_to_the_owners_home(page):
    """The card keeps its own place; it is still committed."""
    show(page, """
      <div data-automation-id="workExperience-entry-0" class="card">
        <h3>Network Engineer</h3>
        <label for="work_country">Country</label>
        <select id="work_country"><option>United States</option><option selected>India</option></select>
        <label for="work_state">State / Province</label>
        <select id="work_state"><option>Virginia</option><option selected>Telangana</option></select>
        <label for="work_city">City</label><input id="work_city" type="text" value="Hyderabad" />
        <button id="save_card_btn" type="button">Save Entry</button>
      </div>
      <script>
        window.committedData = null;
        document.getElementById('save_card_btn').addEventListener('click', () => {
          const c = document.getElementById('work_country'), s = document.getElementById('work_state');
          window.committedData = { country: c.options[c.selectedIndex].text,
                                   state: s.options[s.selectedIndex].text,
                                   city: document.getElementById('work_city').value };
        });
      </script>""")
    assert interaction.commit_draft_cards(page) == 1
    assert page.evaluate("() => window.committedData") == {"country": "India", "state": "Telangana",
                                                           "city": "Hyderabad"}
    # And the owner's sweep, run on the whole page, leaves the entry alone too.
    interaction.wipe_and_enforce_location_sweep(page, country="United States", state="Virginia", city="Fairfax")
    assert selected(page, "#work_country") == "India"
    assert page.locator("#work_city").input_value() == "Hyderabad"


_COUNTRIES = sorted({geo_reference.country_names(code)[0] for code in geo_reference._data()["countries"]}
                    - {"United States"})


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(default=st.sampled_from(_COUNTRIES), position=st.integers(min_value=0, max_value=3))
def test_whichever_country_the_site_defaults_to_it_is_corrected(page, default, position):
    """The property the literals could never give: every country, not two."""
    others = ["Canada", "Mexico", "India", "Japan"]
    others = [o for o in others if o != default]
    options = others[:position] + [default] + others[position:] + ["United States"]
    html = "".join(f"<option{' selected' if o == default else ''}>{o}</option>" for o in options)
    show(page, f'<label for="c">Country</label><select id="c"><option value="">- Select -</option>{html}</select>')
    interaction.wipe_and_enforce_location_sweep(page, country="United States", state="", city="")
    assert selected(page, "#c") == "United States"


def test_wipe_and_enforce_location_sweep_exported_and_accessible():
    assert callable(interaction.wipe_and_enforce_location_sweep)
    assert hasattr(browser_automation, "wipe_and_enforce_location_sweep")
    assert hasattr(browser_automation.JobApplicationAssistant, "wipe_and_enforce_location_sweep")
    assert hasattr(page_agent, "wipe_and_enforce_location_sweep")
    assert hasattr(page_agent.PageAgent, "wipe_and_enforce_location_sweep")
