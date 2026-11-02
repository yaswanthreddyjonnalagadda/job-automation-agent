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
