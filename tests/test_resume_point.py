"""A reopened page must still show the application.

KBI Biopharma (Workday), 28 September: "Continuing where the last run left it" reopened the careers home
(/en-US/KBI_Biopharma/), which has no form and no posting; with no AI to read it, the run stopped at once.
"""
from types import SimpleNamespace

import pytest

from apply_flow import shows_the_application


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


def assistant(posting=False):
    return SimpleNamespace(on_job_description=lambda page: posting)


CAREERS_HOME = """<header><button>Sign In</button><nav><button>Search for Jobs</button>
<button>Introduce Yourself</button></nav></header><footer><a href="#">Privacy Policy</a></footer>"""


def test_a_careers_home_is_not_the_application(page):
    page.set_content(CAREERS_HOME)
    assert shows_the_application(assistant(), page) is False


def test_a_search_box_alone_is_not_the_application(page):
    page.set_content(CAREERS_HOME + '<input type="search" placeholder="Search jobs">')
    assert shows_the_application(assistant(), page) is False


@pytest.mark.parametrize("form", [
    '<label>Email Address* <input type="email"></label>',
    '<label>Why us? <textarea></textarea></label>',
    '<div role="combobox" aria-label="Country">Select One</div>',
    '<label><input type="checkbox"> I agree</label>',
])
def test_a_page_with_a_form_is_the_application(page, form):
    page.set_content(CAREERS_HOME + form)
    assert shows_the_application(assistant(), page) is True


def test_the_job_posting_counts(page):
    page.set_content(CAREERS_HOME)
    assert shows_the_application(assistant(posting=True), page) is True


def test_a_form_inside_a_frame_counts(page):
    page.set_content(CAREERS_HOME + '<iframe srcdoc="<input type=text name=first>"></iframe>')
    page.wait_for_timeout(300)
    assert shows_the_application(assistant(), page) is True


def test_a_hidden_form_does_not(page):
    page.set_content(CAREERS_HOME + '<div style="display:none"><input type="text"></div>')
    assert shows_the_application(assistant(), page) is False
