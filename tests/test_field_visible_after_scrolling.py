"""A field is visible when the page shows it, wherever the window is scrolled to (f295).

Fastly (Greenhouse), 1 November 2026: the agent works down an 11,000-pixel form. By the time it came to ask what the
choices of its "agreements that may restrict you" dropdown were, the dropdown had scrolled above the top of the
window, and the field inventory called it invisible because it measured the field against the window, not the page.
The list was never opened, the model was asked without its choices, and the question was left blank.
"""
import pytest

import form_fields as ff


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 900, "height": 600})
    pg = context.new_page()
    yield pg
    context.close()


FORM = """
<html><body style="margin:0">
  <label for="early">Are you subject to any agreements that may restrict your ability to be employed?*</label>
  <input id="early" role="combobox" aria-autocomplete="list" type="text">
  <div style="height:3000px"></div>
  <label for="late">Veteran status*</label>
  <input id="late" role="combobox" aria-autocomplete="list" type="text">
  <input type="text" aria-label="decoy" style="position:absolute;left:-9999px;top:10px">
  <input type="text" aria-label="decoy above" style="position:absolute;left:10px;top:-9999px">
  <input type="text" aria-label="hidden" style="display:none">
</body></html>
"""


def visible_of(fields):
    return {(f.label or f.question): f.visible for f in fields}


def shown_for(shown, start):
    found = [v for k, v in shown.items() if k.startswith(start)]
    assert len(found) == 1, f"expected one field starting {start!r}, found {list(shown)}"
    return found[0]


@pytest.mark.parametrize("scroll_to", [0, 500, 1500, 3000])
def test_a_field_is_as_visible_scrolled_past_as_in_view(page, scroll_to):
    page.set_content(FORM)
    page.evaluate(f"window.scrollTo(0, {scroll_to})")
    shown = visible_of(ff.inventory(page))
    assert shown_for(shown, "Are you subject") is True, f"scrolled to {scroll_to}: a field above the window was called invisible"
    assert shown_for(shown, "Veteran status") is True


def test_a_field_hidden_off_the_page_or_by_style_is_still_invisible(page):
    page.set_content(FORM)
    page.evaluate("window.scrollTo(0, 3000)")
    shown = visible_of(ff.inventory(page))
    for name in ("decoy", "decoy above", "hidden"):
        assert shown[name] is False, f"{name!r} is not on the page and must not count as visible"
