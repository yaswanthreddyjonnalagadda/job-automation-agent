"""Ant Design lists (Dayforce and many others), on the real widget.

23 September, Dayforce: the agent put "Afghanistan" into Country and
"Badakhshan" into State/Province, and reported both as answered from the
profile. It was the agent's own doing. Ant Design's list is virtual -- only
the first rows exist until it is scrolled, Afghanistan to Argentina in a
country list -- and every row carries an empty state marker. When the
answer was not among the rows in view, the picker matched that empty text
("" is inside every string), clicked the first row, and called it done.
The correction added later went through the same picker, so it "corrected"
Afghanistan to Afghanistan.

These tests drive Ant Design's own Select (antd 5.29.3, bundled in
tests/fixtures/ant_select), not an imitation: the hand-made imitation in
test_phase2_primitives.py has every option in view and no state marker,
which is why it passed while the real form failed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st
from playwright.sync_api import sync_playwright

import geo_reference
import interaction
import page_agent
import provenance
from test_page_agent import Planner, _somewhere_profile, make_agent

BUNDLE = ROOT / "tests" / "fixtures" / "ant_select" / "ant-select.bundle.js"

# A country list as portals draw it: every ISO country, alphabetical, so the
# first row is Afghanistan and the answer is far below the rows in view.
COUNTRIES = sorted((entry["names"][0] for entry in geo_reference._data()["countries"].values()), key=str.casefold)
STATES = {
    "United States": sorted(entry["names"][0] for entry in geo_reference._data()["us_states"].values()),
    "Afghanistan": ["Badakhshān", "Badghīs", "Baghlān", "Balkh", "Bāmyān", "Dāykundī", "Farāh", "Fāryāb",
                    "Ghaznī", "Ghōr", "Helmand", "Herāt", "Jowzjān", "Kābul", "Kandahār"],
}

FORM_JS = """
(() => {
  const {React, createRoot, Select} = window.AntSelectFixture;
  const e = React.createElement;
  const COUNTRIES = %(countries)s, STATES = %(states)s, DEFAULT = %(default)s, SEARCH_BY = %(search_by)s;
  window.eventLog = [];
  const country = (v) => COUNTRIES[Number(v) - 1000];
  function Form() {
    const [c, setC] = React.useState(DEFAULT ? String(1000 + COUNTRIES.indexOf(DEFAULT)) : undefined);
    const [s, setS] = React.useState(undefined);
    const states = STATES[c === undefined ? '' : country(c)] || [];
    const field = (id, label, props) => e('div', {style: {marginBottom: 12}},
      e('label', {htmlFor: id}, label),
      e(Select, Object.assign({id, style: {width: 320}, showSearch: Boolean(SEARCH_BY), optionFilterProp: SEARCH_BY || 'label'}, props)));
    return e('form', null,
      e('h2', null, 'Contact Information'),
      field('country', 'Country *', {value: c,
        options: COUNTRIES.map((name, i) => ({value: String(1000 + i), label: name})),
        onChange: (v) => { window.eventLog.push(['country', country(v)]); setC(v); setS(undefined); }}),
      field('state', 'State/Province *', {value: s, disabled: c === undefined,
        options: states.map((name, i) => ({value: String(5000 + i), label: name})),
        onChange: (v) => { window.eventLog.push(['state', states[Number(v) - 5000]]); setS(v); }}));
  }
  createRoot(document.getElementById('root')).render(e(Form));
})();
"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    provenance.install(context)
    pg = context.new_page()
    yield pg
    context.close()


@pytest.fixture
def resume_file(tmp_path):
    path = tmp_path / "resume.pdf"
    path.write_bytes(b"%PDF-1.4 test resume")
    return path


def open_form(page, default_country: str = "", search_by: str = "label") -> None:
    """The form; `search_by` is what the list's search box matches: "label",
    "value" (antd's default -- typing a country's name finds nothing), or ""
    for a list with no search box."""
    page.set_content('<html><body><div id="root" style="padding:20px"></div></body></html>')
    page.add_script_tag(path=str(BUNDLE))
    page.add_script_tag(content=FORM_JS % {"countries": json.dumps(COUNTRIES), "states": json.dumps(STATES),
                                          "default": json.dumps(default_country),
                                          "search_by": json.dumps(search_by)})
    page.wait_for_selector("#country")
    provenance.install_on_page(page)


def shown(page, field_id: str) -> str:
    return page.locator(f"#{field_id}").evaluate(
        "el => { const i = el.closest('.ant-select').querySelector('.ant-select-selection-item');"
        " return i ? (i.getAttribute('title') || i.innerText) : ''; }")


def agent_for(resume_file, **place):
    return make_agent(Planner(), resume_file, profile=_somewhere_profile(**place))


def control(agent, page, prefix: str):
    return next(c for c in page_agent.parse_snapshot(agent.snapshot(page)) if c.question.startswith(prefix))


def test_the_country_far_down_the_list_is_the_one_chosen(page, resume_file):
    open_form(page)
    agent = agent_for(resume_file, country="United States", state="Virginia")
    assert agent.choose(page, control(agent, page, "Country"), "United States") is True
    assert shown(page, "country") == "United States"
    assert page.evaluate("() => window.eventLog") == [["country", "United States"]]


def test_the_state_comes_from_the_list_the_country_loaded(page, resume_file):
    open_form(page)
    agent = agent_for(resume_file, country="United States", state="Virginia")
    agent.choose(page, control(agent, page, "Country"), "United States")
    assert agent.choose(page, control(agent, page, "State"), "Virginia") is True
    assert shown(page, "state") == "Virginia"
    assert page.evaluate("() => window.eventLog") == [["country", "United States"], ["state", "Virginia"]]


@pytest.mark.parametrize("answer", ["Virginia", "Atlantis"])
def test_an_answer_the_list_does_not_offer_is_left_for_the_owner(page, resume_file, answer):
    """Not the first row, not anything: a state offered to a country list
    (the 08:19 failure) and a place that is no country leave it empty."""
    open_form(page)
    agent = agent_for(resume_file, country="United States")
    assert agent.choose(page, control(agent, page, "Country"), answer) is False
    assert shown(page, "country") == ""
    assert page.evaluate("() => window.eventLog") == []


@pytest.mark.parametrize("wanted", [COUNTRIES[0], COUNTRIES[1], COUNTRIES[-2], COUNTRIES[-1]])
def test_the_first_and_last_rows_are_reached_too(page, resume_file, wanted):
    open_form(page)
    agent = agent_for(resume_file, country=wanted)
    assert agent.choose(page, control(agent, page, "Country"), wanted) is True
    assert shown(page, "country") == wanted


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(wanted=st.sampled_from(COUNTRIES))
def test_whichever_country_is_wanted_that_country_is_chosen(page, resume_file, wanted):
    open_form(page)
    agent = agent_for(resume_file, country=wanted)
    assert agent.choose(page, control(agent, page, "Country"), wanted) is True
    assert shown(page, "country") == wanted


def test_the_picker_never_reports_a_choice_the_select_did_not_keep(page):
    """Asked for a country the list lacks, the bare picker says no, and the
    select shows nothing."""
    open_form(page)
    assert interaction.resolve_ant_dropdown(page, page.locator("#country"), "Atlantis", timeout_ms=2_000) is False
    assert shown(page, "country") == ""


def test_a_site_default_is_put_right_on_the_real_widget(page, resume_file):
    open_form(page, default_country="Afghanistan")
    agent = agent_for(resume_file, country="United States", state="Virginia")
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    plan = page_agent.PagePlan.from_json({"page_kind": "application_form", "answers": [], "next": {"kind": "none"}})
    agent.correct_from_profile(page, plan, controls)
    assert shown(page, "country") == "United States"
    assert any("Afghanistan" in note and "United States" in note for note in agent.notes)


def test_the_owners_own_choice_in_the_list_stays(page, resume_file):
    """A person who opens the list and clicks Albania keeps Albania. The list
    sits outside the field, so the click makes no input event on it; the
    observer credits the field the list belongs to."""
    open_form(page)
    provenance.set_agent_busy(page, False)                        # the owner's turn
    page.locator("#country").click()                              # a person's own clicks
    page.locator(".ant-select-item-option[title='Albania']").click()
    provenance.set_agent_busy(page, True)
    assert shown(page, "country") == "Albania"
    assert provenance.owner_edited(page.locator("#country")) is True   # seen as the owner's
    agent = agent_for(resume_file, country="United States")
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    plan = page_agent.PagePlan.from_json({"page_kind": "application_form", "answers": [], "next": {"kind": "none"}})
    agent.correct_from_profile(page, plan, controls)
    assert shown(page, "country") == "Albania"


def test_the_address_sweep_uses_the_same_picker(page):
    open_form(page, default_country="Afghanistan")
    interaction.wipe_and_enforce_location_sweep(page, country="United States", state="Virginia", city="")
    assert shown(page, "country") == "United States"
    assert shown(page, "state") == "Virginia"


@pytest.mark.parametrize("search_by", ["value", ""])
def test_a_list_whose_search_cannot_find_the_name_is_read_whole(page, resume_file, search_by):
    """antd searches option values by default, so typing "United States"
    finds nothing; some lists have no search box at all. Either way the
    list is scrolled through rather than typed into."""
    open_form(page, search_by=search_by)
    agent = agent_for(resume_file, country="United States")
    assert agent.choose(page, control(agent, page, "Country"), "United States") is True
    assert shown(page, "country") == "United States"
