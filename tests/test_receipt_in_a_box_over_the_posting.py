"""A receipt shown in a box over the posting page is a receipt, whatever button the page underneath still has (f307).

Rackspace's Workday, 2 November 2026: after Submit the site goes back to the job posting -- whose own Apply button is
still there -- and shows "Congratulations! Your application has been successfully submitted." in a dialog over it. The
agent saw the Apply button, took the page for the form it had handed over ("still on the form"), never read the receipt,
and went on waiting: the browser did not close and the status stayed "ready to submit".
"""
import pytest

from browser_automation import JobApplicationAssistant

POSTING = '<h1>Network Engineer IV - US</h1><button>Apply</button><h3>Similar Jobs (2)</h3>'
CONGRATULATIONS = ('<div role="dialog" aria-modal="true"><h2>Congratulations!</h2>'
                   '<p>Your application has been successfully submitted. Thank you for applying\u2014our team will review '
                   'your details and contact you if your profile matches our requirements.</p>'
                   '<p>By signing in with a third-party account, you consent to sharing basic profile information to '
                   'create or access your candidate profile.</p></div>')


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


def assistant():
    return JobApplicationAssistant.__new__(JobApplicationAssistant)


def test_a_receipt_in_a_dialog_over_the_posting_is_a_receipt(page):
    page.set_content(POSTING + CONGRATULATIONS)
    assert assistant().submission_confirmed(page, "Network Engineer IV - US") is True


def test_the_posting_alone_is_not_a_receipt(page):
    page.set_content(POSTING)
    assert assistant().submission_confirmed(page, "Network Engineer IV - US") is False


@pytest.mark.parametrize("words", [
    "Are you sure you want to submit your application?",
    "Please verify your email address to finish your application.",
    "We use cookies to improve your experience.",
])
def test_a_dialog_that_is_not_a_receipt_is_not_one(page, words):
    page.set_content(POSTING + f'<div role="dialog" aria-modal="true"><p>{words}</p></div>')
    assert assistant().submission_confirmed(page, "Network Engineer IV - US") is False


def test_a_dialog_holding_a_form_is_not_a_receipt(page):
    form = "".join(f'<label>Field {i} <input></label>' for i in range(4))
    page.set_content(POSTING + '<div role="dialog" aria-modal="true"><p>Your application has been successfully '
                     f'submitted.</p>{form}</div>')
    assert assistant().submission_confirmed(page, "Network Engineer IV - US") is False


def test_a_hidden_dialog_is_not_a_receipt(page):
    page.set_content(POSTING + CONGRATULATIONS.replace('role="dialog"', 'role="dialog" style="display:none"'))
    assert assistant().submission_confirmed(page, "Network Engineer IV - US") is False


@pytest.mark.parametrize("reply", [True, False, None, "text", [1, 2]])
def test_a_page_that_does_not_answer_with_a_list_of_texts_has_no_receipt_in_a_box(reply):
    class Page:
        def evaluate(self, script, *args):
            return reply
    assert assistant()._a_box_says_it_was_received(Page()) is False
