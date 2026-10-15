"""Reading what a Workday multiselect already holds (sites.workday._multiselect_selection).

Real markup (Stryker, Staff Cloud DevSecOps Engineer, 23 September 2026,
recorded accessibility snapshot of the "Country Phone Code" field):

    textbox "Country Phone Code" [placeholder: Search]
    generic [aria-hidden]: 1 item selected, United States of America (+1)
    listbox "items selected":
      option "United States of America (+1), press delete to clear value.":
        paragraph: United States of America (+1)

The search box is permanently empty -- reading it, or reading only the pill
selector used by the field's other multiselects, reported this field as
empty forever. `select_from_searchable_input` only skips a field that
`_multiselect_selection` reports as already filled, so an empty read here
made the agent re-answer this field on every pass and hand over with
"could not set: Country Phone Code".
"""

import pytest

from sites.workday import WorkdayAdapter


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    page = browser.new_page()
    yield page
    page.close()


def _container(inner: str) -> str:
    return f'<div data-automation-id="multiSelectContainer" id="box">{inner}</div>'


def test_a_pill_selection_is_read_unchanged(page):
    """Existing behaviour: a tag-style multiselect's committed value is a pill."""
    page.set_content(_container(
        '<input id="skills--uxi" placeholder="Search" />'
        '<div data-automation-id="selectedItem">Python</div>'
        '<div data-automation-id="selectedItem">Go</div>'
    ))
    value = WorkdayAdapter._multiselect_selection(page, "skills--uxi")
    assert value == "Python, Go"


def test_an_items_selected_listbox_is_read_as_the_value(page):
    """The bug: Country Phone Code's committed value lives in an "items
    selected" listbox, not a pill. Text matches the real Stryker recording."""
    page.set_content(_container(
        '<input id="phoneCode--uxi" placeholder="Search" />'
        '<div aria-hidden="true">1 item selected, United States of America (+1)</div>'
        '<div role="listbox" aria-label="items selected">'
        '  <div role="option" aria-label="United States of America (+1), press delete to clear value.">'
        '    <p>United States of America (+1)</p>'
        '  </div>'
        '</div>'
    ))
    value = WorkdayAdapter._multiselect_selection(page, "phoneCode--uxi")
    assert value == "United States of America (+1)"


def test_the_visually_hidden_summary_is_a_last_resort(page):
    """No pill and no listbox option markup -- only the summary text survives."""
    page.set_content(_container(
        '<input id="phoneCode--uxi" placeholder="Search" />'
        '<div aria-hidden="true">1 item selected, United States of America (+1)</div>'
    ))
    value = WorkdayAdapter._multiselect_selection(page, "phoneCode--uxi")
    assert value == "United States of America (+1)"


def test_a_field_with_nothing_selected_reads_as_empty(page):
    """No pill, no listbox, no summary: correctly empty, not a false positive
    that would make the agent skip a field it actually still needs to fill."""
    page.set_content(_container('<input id="phoneCode--uxi" placeholder="Search" />'))
    value = WorkdayAdapter._multiselect_selection(page, "phoneCode--uxi")
    assert value == ""


def test_never_re_answers_a_field_the_listbox_already_holds(page):
    """The actual loop: select_from_searchable_input must return True without
    touching the widget again once the listbox already shows a value. The
    `assistant` collaborator is never consulted on this path, so None
    stands in for it -- reaching it at all would itself be the bug."""
    page.set_content(_container(
        '<input id="phoneCode--uxi" placeholder="Search" />'
        '<div role="listbox" aria-label="items selected">'
        '  <div role="option" aria-label="United States of America (+1), press delete to clear value.">'
        '    <p>United States of America (+1)</p>'
        '  </div>'
        '</div>'
    ))
    adapter = WorkdayAdapter()
    opened = {"count": 0}
    adapter._open_prompt = lambda *a, **k: opened.__setitem__("count", opened["count"] + 1)

    result = adapter.select_from_searchable_input(None, page, "phoneCode--uxi", ["United States of America (+1)"])
    assert result is True
    assert opened["count"] == 0, "already-filled field must not be re-opened/re-driven"
