"""A search-and-pick list that holds none of the owner's names is searched for a bounded time, and once (f302).

Verizon's Workday, 2 November 2026: the education step took 4, 1 and 12 minutes, and Field of Study was still blank. The
subject has four names and a school five; each try scrolled a long list and opened category after category, and the
three correction passes ran the whole search again for every entry that was still blank.
"""
from types import SimpleNamespace

import pytest

import sites.workday as workday
from sites.workday import WorkdayAdapter
from test_workday_prompt_textbox import agent, browser, page  # noqa: F401

FIELD = """<div data-automation-id="formField-fieldOfStudy">
  <input id="row--fieldOfStudy" data-uxi-widget-type="selectinput" data-uxi-multiselect-id="f">
</div>
<div data-automation-id="responsiveMonikerPrompt" data-associated-widget="f">
  <div data-automation-id="promptOption" data-automation-label="Astronomy">Astronomy</div>
</div>"""
NAMES = ["Computer Science", "Computer Technology", "Computer and Information Science", "Computer and Information Sciences"]


def test_one_list_is_not_worked_on_past_its_budget(page, monkeypatch):
    page.set_content(FIELD)
    budget = workday.SEARCH_BUDGET_SECONDS
    clock = SimpleNamespace(now=1000.0, opened=0)
    monkeypatch.setattr(workday.time, "monotonic", lambda: clock.now)
    adapter = WorkdayAdapter()

    def open_prompt(*args, **kwargs):
        clock.opened += 1
        clock.now += budget / 2 + 1       # each try costs more than half the budget
    monkeypatch.setattr(adapter, "_open_prompt", open_prompt)
    assert adapter.select_from_searchable_input(agent().assistant, page, "--fieldOfStudy", NAMES, 0, keyboard=False) is False
    assert 1 <= clock.opened < len(NAMES), "every name was tried although the budget was spent"


def test_a_field_of_study_search_that_found_nothing_is_not_repeated_by_the_correction_passes(page, monkeypatch):
    page.set_content(FIELD)
    adapter = WorkdayAdapter()
    asked = []
    monkeypatch.setattr(adapter, "delete_all_entries", lambda *a, **k: 0)
    monkeypatch.setattr(adapter, "_ensure_entry_slot", lambda *a, **k: None)
    monkeypatch.setattr(adapter, "_school_list_is_empty", lambda *a, **k: False)
    monkeypatch.setattr(adapter, "select_from_button_dropdown", lambda *a, **k: True)
    monkeypatch.setattr(adapter, "_multiselect_selection", lambda *a, **k: "")
    monkeypatch.setattr(page, "wait_for_timeout", lambda ms: None)

    def search(assistant, page_, suffix, names, *args, **kwargs):
        asked.append(suffix)
        return False
    monkeypatch.setattr(adapter, "select_from_searchable_input", search)
    adapter.fill_education_section(agent().assistant, page, [
        {"school": "", "degree": "Master of Science", "field": "Computer Science"}])
    assert asked == ["--fieldOfStudy"], "the whole search ran again in the correction passes"


def test_a_field_that_was_chosen_is_still_left_alone_by_the_correction_passes(page, monkeypatch):
    page.set_content(FIELD)
    adapter = WorkdayAdapter()
    asked = []
    monkeypatch.setattr(adapter, "delete_all_entries", lambda *a, **k: 0)
    monkeypatch.setattr(adapter, "_ensure_entry_slot", lambda *a, **k: None)
    monkeypatch.setattr(adapter, "_school_list_is_empty", lambda *a, **k: False)
    monkeypatch.setattr(adapter, "select_from_button_dropdown", lambda *a, **k: True)
    monkeypatch.setattr(adapter, "_multiselect_selection", lambda *a, **k: "Computer Science")
    monkeypatch.setattr(page, "wait_for_timeout", lambda ms: None)
    monkeypatch.setattr(adapter, "select_from_searchable_input", lambda *a, **k: asked.append(1) or True)
    adapter.fill_education_section(agent().assistant, page, [
        {"school": "", "degree": "Master of Science", "field": "Computer Science"}])
    assert asked == [1]


PICKED = """<div data-automation-id="multiSelectContainer">
  <div role="listbox" aria-label="items selected">{pill}</div>
  <input id="row--fieldOfStudy" data-uxi-widget-type="selectinput" data-uxi-multiselect-id="f">
</div>
<script>
  document.getElementById('row--fieldOfStudy').addEventListener('keydown', e => {{
    if (e.key === 'Backspace') document.querySelector('[role=listbox]').innerHTML = '';
  }});
</script>"""
OTHER = '<div role="option">*Other, press delete to clear value.</div>'


def search_that_clicks_other(adapter, monkeypatch, page):
    """A search of a list with categories: it finds none of the names, but its exploration has clicked '*Other'."""
    monkeypatch.setattr(adapter, "_open_prompt", lambda *a, **k: None)
    monkeypatch.setattr(adapter, "_choose_scrolled_prompt", lambda *a, **k: None)

    def explore(*a, **k):
        page.evaluate("h => document.querySelector('[role=listbox]').innerHTML = h", OTHER)
        return None
    monkeypatch.setattr(adapter, "_choose_nested_prompt", explore)


def test_a_placeholder_the_search_clicked_while_looking_is_taken_back(page, monkeypatch):
    page.set_content(PICKED.format(pill=""))
    adapter = WorkdayAdapter()
    search_that_clicks_other(adapter, monkeypatch, page)
    assert adapter.select_from_searchable_input(agent().assistant, page, "--fieldOfStudy", NAMES, 0, keyboard=False) is False
    assert adapter._multiselect_selection(page, "--fieldOfStudy", "f", 0) == "", "the box was left on '*Other'"


def test_a_value_already_in_the_box_is_not_touched(page, monkeypatch):
    page.set_content(PICKED.format(pill='<div role="option">Computer Science, press delete to clear value.</div>'))
    adapter = WorkdayAdapter()
    adapter._clear_what_the_search_chose(page, page.locator("#row--fieldOfStudy"), "--fieldOfStudy", "f", 0, NAMES)
    assert adapter._multiselect_selection(page, "--fieldOfStudy", "f", 0) == "Computer Science"


WRONG = '<div role="option">Electrical Engineering, press delete to clear value.</div>'


def pick_computer_science(adapter, monkeypatch, page):
    monkeypatch.setattr(adapter, "_open_prompt", lambda *a, **k: None)

    def choose(*a, **k):
        page.evaluate("h => document.querySelector('[role=listbox]').innerHTML = h",
                      '<div role="option">Computer Science, press delete to clear value.</div>')
        return "Computer Science"
    monkeypatch.setattr(adapter, "_choose_scrolled_prompt", choose)


def test_a_subject_left_in_the_box_from_another_entry_is_replaced_for_an_education_entry(page, monkeypatch):
    """Verizon, 2 November: the Master's kept the Bachelor's 'Electrical Engineering' because the box was already filled."""
    page.set_content(PICKED.format(pill=WRONG))
    adapter = WorkdayAdapter()
    pick_computer_science(adapter, monkeypatch, page)
    assert adapter.select_from_searchable_input(agent().assistant, page, "--fieldOfStudy", NAMES, 0, keyboard=False,
                                                replace_unsupported=True) is True
    assert adapter._multiselect_selection(page, "--fieldOfStudy", "f", 0) == "Computer Science"


def test_elsewhere_a_value_already_in_a_box_is_still_left_alone(page, monkeypatch):
    page.set_content(PICKED.format(pill=WRONG))
    adapter = WorkdayAdapter()
    pick_computer_science(adapter, monkeypatch, page)
    assert adapter.select_from_searchable_input(agent().assistant, page, "--fieldOfStudy", NAMES, 0, keyboard=False) is False
    assert adapter._multiselect_selection(page, "--fieldOfStudy", "f", 0) == "Electrical Engineering"


def test_the_correction_passes_do_not_accept_another_entrys_subject(page, monkeypatch):
    page.set_content(FIELD)
    adapter = WorkdayAdapter()
    asked = []
    monkeypatch.setattr(adapter, "delete_all_entries", lambda *a, **k: 0)
    monkeypatch.setattr(adapter, "_ensure_entry_slot", lambda *a, **k: None)
    monkeypatch.setattr(adapter, "_school_list_is_empty", lambda *a, **k: False)
    monkeypatch.setattr(adapter, "select_from_button_dropdown", lambda *a, **k: True)
    monkeypatch.setattr(adapter, "_multiselect_selection", lambda *a, **k: "Electrical Engineering")
    monkeypatch.setattr(page, "wait_for_timeout", lambda ms: None)
    monkeypatch.setattr(adapter, "select_from_searchable_input", lambda *a, **k: asked.append(k.get("replace_unsupported")) or True)
    adapter.fill_education_section(agent().assistant, page, [
        {"school": "", "degree": "Master of Science", "field": "Computer Science"}])
    assert asked and all(asked), "a box holding another entry's subject was accepted as filled"
    assert len(asked) == 4, "the first pass and the three correction passes each look again"
