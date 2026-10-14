"""A click that works must not be reported as denied because the clicked element is gone afterwards.

Copart's Workday 'How Did You Hear About Us?' is a nested list: clicking a row either commits the
choice and closes the list, or opens a sub-menu -- either way the row that was clicked leaves the
page. The guard read its denial counter *through that row's locator*; with the row gone the locator
waited its full 30 seconds, raised, and the guard took 'cannot verify' to mean 'denied'. The click
that had worked was reported as refused, the agent believed it was still on the top list, and the
field was left typed but never chosen (f149).

A real denial still stops the fallback: it is read from the frame, which outlives the element.
"""
import pytest

from browser_automation import JobApplicationAssistant
from interaction import click_resiliently
from submission_guard import SubmissionGuardV0

from test_submission_firewall import browser, guarded, install_submit_form, page  # noqa: F401


LIST_THAT_CLOSES = """
  <ul id="list"><li><button id="row" onclick="document.getElementById('list').remove(); window.chose = 'LinkedIn'">LinkedIn</button></li></ul>
"""


@pytest.fixture(params=[JobApplicationAssistant._click_resiliently, click_resiliently],
                ids=["assistant", "interaction"])
def click(request):
    return request.param


def test_a_click_that_removes_its_own_element_is_reported_as_done(page, click):
    page.set_content(LIST_THAT_CLOSES)
    guarded(page)
    page.set_default_timeout(1_500)            # a missing element must not be waited for 30 s

    assert click(page.locator("#row")) is True
    assert page.evaluate("window.chose") == "LinkedIn"


def test_a_click_that_opens_a_sub_menu_in_place_of_its_row_is_reported_as_done(page, click):
    page.set_content("""
      <div id="menu"><button id="row" onclick="document.getElementById('menu').innerHTML='<button>College</button>'">
        Campus Campaign</button></div>""")
    guarded(page)
    page.set_default_timeout(1_500)

    assert click(page.locator("#row")) is True
    assert page.locator("#menu button").inner_text() == "College"


def test_a_click_that_navigates_the_document_is_reported_as_done(page, click):
    page.set_content("""<button id="go" onclick="document.open(); document.write('<p>next page</p>'); document.close()">
      Save and Continue</button>""")
    guarded(page)
    page.set_default_timeout(1_500)

    assert click(page.locator("#go")) is True


def test_a_denied_click_is_still_refused_when_the_element_stays(page, click):
    install_submit_form(page, "Submit Application")
    guarded(page)

    assert click(page.get_by_role("button", name="Submit Application")) is False
    assert page.evaluate("window.submitted") is None


def test_the_frame_is_found_before_the_click_so_a_vanished_element_can_still_be_checked(page):
    page.set_content(LIST_THAT_CLOSES)
    guarded(page)
    locator = page.locator("#row")

    frame = SubmissionGuardV0.click_frame(locator)
    locator.click()

    assert frame is not None and frame == page.main_frame
    assert SubmissionGuardV0.click_was_denied(locator, 0, frame) is False
