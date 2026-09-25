"""Alloy (Greenhouse), 24 September: the whole application was filled except "Location (City)".

The profile says "Fairfax, VA"; the form's own search offers "Fairfax, Virginia, United States",
and offers nothing at all for "Fairfax, VA". The agent could not tell that the two name one
town, fell back to typing and pressing Enter, took the typed text for an answer, and handed
over an application whose required Location box was empty. Beside it, the phone's dial-code
picker -- labelled "Country" and showing "+1" -- was "corrected" to "United States" on every
pass.

The classes:
- a town written one way in the profile and another way by the form (a state's code against
  its name, a country left out) is the same town;
- an answer counts as given only when it survives leaving the box (typed text that was never
  committed is dropped on blur);
- a dial code is the answer to a phone-country question, never to the owner's country.
"""
from types import SimpleNamespace

import pytest

import config
import geo_reference
import page_agent
import provenance
import safety
from browser_automation import JobApplicationAssistant

LIST = ["Fairfax, Virginia, United States", "Fairfax Station, Virginia, United States",
        "Fairfax, California, United States", "Town of Fairfax, Vermont, United States",
        "Fairfax, Iowa, United States"]


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


# --- a town in two spellings is one town -------------------------------------------------

@pytest.mark.parametrize("a, b", [
    ("Fairfax, VA", "Fairfax, Virginia, United States"),
    ("Fairfax, Virginia", "Fairfax, VA, USA"),
    ("fairfax , va", "Fairfax, Virginia, United States"),
    ("Springfield, IL", "Springfield, Illinois, United States"),
    ("Fairfax, VA, United States", "Fairfax, Virginia"),
])
def test_one_town_in_two_spellings_is_the_same_town(a, b):
    assert geo_reference.same_locality(a, b) and geo_reference.same_locality(b, a)


@pytest.mark.parametrize("a, b", [
    ("Fairfax, VA", "Fairfax Station, Virginia, United States"),       # another town
    ("Fairfax, VA", "Town of Fairfax, Vermont, United States"),
    ("Fairfax, VA", "Fairfax, California, United States"),             # the same name in another state
    ("Fairfax, VA", "Fairfax, Iowa, United States"),
    ("Fairfax, VA", "Fairfax, United States"),                         # a state named on one side only
    ("Fairfax", "Fairfax, Virginia, United States"),                   # no region to compare: not a locality
    ("Fairfax VA", "Fairfax, Virginia, United States"),
    ("", "Fairfax, Virginia, United States"),
])
def test_a_different_town_state_or_country_is_not_the_same_town(a, b):
    assert not geo_reference.same_locality(a, b)


def test_every_state_is_the_same_town_by_its_code_or_its_name_and_no_other_state_is():
    """The class, over the reference data: no state is special."""
    states = list(geo_reference._data()["us_states"].items())
    covered = 0
    for i, (code, entry) in enumerate(states):
        # A name with a comma in it ("Virgin Islands, U.S.") cannot be told from a town's parts.
        name = next((n for n in entry["names"] if "," not in n), None)
        if name is None:
            continue
        covered += 1
        other_code, other = states[(i + 1) % len(states)]
        assert geo_reference.same_locality(f"Midtown, {code}", f"Midtown, {name}, United States"), code
        assert not geo_reference.same_locality(f"Midtown, {other_code}", f"Midtown, {name}, United States"), code
        assert not geo_reference.same_locality(f"Midtown, {code}", f"Uptown, {name}, United States"), code
    assert covered >= 50


def test_the_agents_own_comparisons_know_a_town_in_two_spellings():
    assert page_agent._same_answer("Fairfax, VA", "Fairfax, Virginia, United States")
    assert not page_agent._same_answer("Fairfax, VA", "Fairfax, California, United States")
    assert page_agent.closest_choice(LIST, "Fairfax, VA") == 0
    assert page_agent.closest_choice(LIST[1:], "Fairfax, VA") is None        # the town is not on the list


# --- the location box, as Greenhouse draws it ------------------------------------------------

def location_widget(options=LIST, commit_on_enter=True):
    return f"""<html><body>
<div class="select__container">
  <label id="candidate-location-label" for="candidate-location">Location (City)<span aria-hidden="true">*</span></label>
  <div class="select-shell">
    <div id="chosen" class="select__single-value"></div>
    <input id="candidate-location" role="combobox" aria-autocomplete="list" aria-expanded="false"
           aria-labelledby="candidate-location-label" type="text" autocomplete="off">
    <div id="menu" hidden><div id="list" role="listbox"></div></div>
  </div>
</div>
<label for="next">Next box</label><input id="next">
<script>
  const DATA = {list(options)!r};
  const input = document.getElementById('candidate-location'), list = document.getElementById('list'),
        menu = document.getElementById('menu'), chosen = document.getElementById('chosen');
  function pick(x) {{ chosen.textContent = x; input.value = ''; menu.hidden = true; window.picked = x; }}
  function render(items) {{
    list.innerHTML = ''; menu.hidden = !items.length;
    items.forEach(x => {{ const o = document.createElement('div'); o.setAttribute('role', 'option'); o.textContent = x;
      o.addEventListener('mousedown', e => e.preventDefault()); o.addEventListener('click', () => pick(x)); list.appendChild(o); }});
  }}
  // the search knows only names as it spells them: "Fairfax, VA" finds nothing
  input.addEventListener('input', () => {{ const t = input.value.trim().toLowerCase();
    render(t ? DATA.filter(d => d.toLowerCase().startsWith(t)) : []); }});
  input.addEventListener('keydown', e => {{
    if (e.key === 'Enter' && {str(commit_on_enter).lower()} && !menu.hidden) pick(list.firstChild.textContent);
    // as react-select does: Backspace in an empty box removes the chosen value
    if (e.key === 'Backspace' && input.value === '' && chosen.textContent) chosen.textContent = '';
  }});
  // text that was never chosen does not survive leaving the box
  input.addEventListener('blur', () => {{ if (!chosen.textContent) input.value = ''; menu.hidden = true; }});
</script></body></html>"""


def make_agent(profile=None):
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    cfg = SimpleNamespace(auto_submit=False, ats_email="")
    job = SimpleNamespace(title="Cloud Security Engineer", company="Example", url="https://jobs.example.com/apply")
    profile = profile or config.UserProfile(email="o@example.com", country="United States")
    return page_agent.PageAgent(assistant, SimpleNamespace(), cfg, profile, SimpleNamespace(raw_text="x"), job,
                                resume_file=None)


def location_control(agent, page):
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    return controls, next(c for c in controls if c.role == "combobox")


def test_the_town_is_chosen_from_the_list_in_the_forms_own_spelling(page):
    page.set_content(location_widget())
    agent = make_agent()
    controls, box = location_control(agent, page)
    assert agent.choose(page, box, "Fairfax, VA", controls) is True
    assert page.evaluate("window.picked") == "Fairfax, Virginia, United States"     # not the row Enter would take


def test_a_different_town_is_never_left_in_the_box_when_the_list_does_not_have_the_right_one(page):
    """The last resort types and presses Enter, which takes whichever row is showing. A row
    that is not the town asked for is not kept: the box is emptied and the answer is not given."""
    page.set_content(location_widget(options=LIST[1:]))
    agent = make_agent()
    controls, box = location_control(agent, page)
    assert agent.choose(page, box, "Fairfax, VA", controls) is False
    assert page.locator("#chosen").inner_text() == ""


def test_typed_text_that_was_never_chosen_is_not_an_answer(page):
    """Nothing to choose from: the text stays in the box until it is left, so a check made
    at once read it as the answer."""
    page.set_content(location_widget(options=[]))
    agent = make_agent()
    controls, box = location_control(agent, page)
    assert agent.type_and_commit(page, box, "Fairfax, VA") is False


def test_a_choice_made_by_typing_and_pressing_enter_still_counts_when_it_is_kept(page):
    page.set_content(location_widget(options=["Fairfax, VA"]))
    agent = make_agent()
    controls, box = location_control(agent, page)
    assert agent.type_and_commit(page, box, "Fairfax, VA") is True
    assert page.evaluate("window.picked") == "Fairfax, VA"


# --- a dial code is not the owner's country --------------------------------------------------

PHONE = """<html><body>
<div><label id="cl">Country<span>*</span></label>
  <div><span id="dial">+1</span><input role="combobox" aria-labelledby="cl" id="cc"></div></div>
<label for="ph">Phone*</label><input id="ph" value="(571) 354-5212">
</body></html>"""


@pytest.mark.parametrize("code", ["+1", "+91", "+44"])
def test_a_phone_countrys_dial_code_is_not_corrected_to_the_owners_country(browser, code):
    # With the page observer on, as in a real run, the "+1" is known to be the site's own value
    # -- the case in which the agent puts a contradicting value right from the profile.
    context = browser.new_context()
    try:
        provenance.install(context)
        page = context.new_page()
        page.set_content(PHONE.replace("+1", code))
        provenance.install_on_page(page)
        agent = make_agent()
        controls = page_agent.parse_snapshot(agent.snapshot(page))
        assert agent.correct_from_profile(page, page_agent.PagePlan(), controls) == []
        assert page.locator("#cc").input_value() == ""              # nothing was typed into the picker
        assert not any("Country" in note for note in agent.notes)
    finally:
        context.close()


def test_the_dial_code_test_is_only_for_a_dial_code():
    country = page_agent.parse_snapshot('- combobox "Country" [ref=e1]: Canada')[0]
    dial = page_agent.parse_snapshot('- combobox "Country" [ref=e1]: "+1"')[0]
    assert not page_agent._holds_dial_code(country) and page_agent._holds_dial_code(dial)
