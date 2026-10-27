"""An education entry is not made when the employer's school list is empty (f183).

Invesco's Workday: the School box is a search-and-pick list, and the tenant's list holds nothing -- the schools call
answers 200 and every search, even an empty one, shows 'No Items.'. The agent added an Education entry (which makes School,
Degree and Field of Study required), could not set the school, and stopped for the owner on every run. Education is
optional on Workday, and an entry whose required School can never be chosen only blocks the form, so the section is left
empty and the reason is logged.
"""
import logging
from types import SimpleNamespace

import pytest

from sites.workday import WorkdayAdapter
from test_workday_prompt_textbox import agent, browser, page  # noqa: F401

SCHOOL_LIST = """<div data-automation-id="formField-school">
  <input id="row--school" placeholder="Search" data-uxi-widget-type="selectinput" data-uxi-multiselect-id="s"
         onclick="document.getElementById('prompt').hidden = false">
</div>
<div id="prompt" hidden data-automation-id="responsiveMonikerPrompt" data-associated-widget="s">{rows}</div>"""
EMPTY = '<div data-automation-id="promptOption">No Items.</div>'
OFFERS = ('<div data-automation-id="promptOption" data-automation-label="Eastern Illinois University">'
          'Eastern Illinois University</div>')
EDUCATION = [{"school": "Eastern Illinois University", "degree": "Master of Science", "field": "Computer Science"}]


def adapter_for(monkeypatch):
    adapter = WorkdayAdapter()
    calls = SimpleNamespace(deleted=0, schools=[], degrees=0)

    def delete_all_entries(*args, **kwargs):
        calls.deleted += 1
        return 0

    def select_from_searchable_input(assistant, page, suffix, names, *args, **kwargs):
        calls.schools.append(suffix)
        return True

    def select_from_button_dropdown(*args, **kwargs):
        calls.degrees += 1
        return True

    monkeypatch.setattr(adapter, "delete_all_entries", delete_all_entries)
    monkeypatch.setattr(adapter, "_ensure_entry_slot", lambda *args, **kwargs: None)
    monkeypatch.setattr(adapter, "select_from_searchable_input", select_from_searchable_input)
    monkeypatch.setattr(adapter, "select_from_button_dropdown", select_from_button_dropdown)
    return adapter, calls


def test_an_empty_school_list_leaves_education_out_and_says_why(page, monkeypatch, caplog):
    page.set_content(SCHOOL_LIST.format(rows=EMPTY))
    adapter, calls = adapter_for(monkeypatch)
    with caplog.at_level(logging.INFO):
        adapter.fill_education_section(agent().assistant, page, EDUCATION)
    assert calls.deleted == 2                      # the usual clean slate, then the entry that cannot be finished
    assert calls.schools == [] and calls.degrees == 0
    assert "EDUCATION_SKIPPED" in caplog.text


def test_a_list_that_offers_schools_is_filled_as_before(page, monkeypatch, caplog):
    page.set_content(SCHOOL_LIST.format(rows=OFFERS))
    adapter, calls = adapter_for(monkeypatch)
    with caplog.at_level(logging.INFO):
        adapter.fill_education_section(agent().assistant, page, EDUCATION)
    assert calls.deleted == 1
    assert calls.schools and calls.degrees == 1
    assert "EDUCATION_SKIPPED" not in caplog.text


def test_a_list_that_is_slow_to_load_is_not_taken_for_an_empty_one(page, monkeypatch):
    """'No Items.' shows while the server answers; the schools arrive a moment later."""
    page.set_content(SCHOOL_LIST.format(rows=EMPTY) + """<script>
      document.getElementById('row--school').addEventListener('click', () => setTimeout(() => {
        document.getElementById('prompt').innerHTML =
          '<div data-automation-id="promptOption" data-automation-label="Eastern Illinois University">Eastern Illinois University</div>';
      }, 1200));</script>""")
    adapter, calls = adapter_for(monkeypatch)
    adapter.fill_education_section(agent().assistant, page, EDUCATION)
    assert calls.schools, "the list was read as empty before it had loaded"
