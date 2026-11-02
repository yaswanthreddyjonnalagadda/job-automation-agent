"""A receipt that adds its own closing sentence is still the receipt (f286).

Color Health on Ashby, 1 November 2026: after the owner pressed Submit the page said 'Success' and 'Your application was
successfully submitted. We'll contact you if there are next steps.' The agent's receipt test wanted the confirming
sentence alone in its element, so the run kept waiting and the tracker stayed 'ready_to_submit' for an application
that had been sent.
"""
import pytest

from test_run_recovery_regressions import assistant, browser, page  # noqa: F401


@pytest.mark.parametrize("body", [
    "<h1>Success</h1><p>Your application was successfully submitted. We'll contact you if there are next steps.</p>",
    "<p>Your application has been submitted. We will be in touch if your background is a match.</p>",
    "<p>Your application was successfully submitted. We’ll reach out about next steps.</p>",
])
def test_a_receipt_with_a_closing_sentence_is_a_receipt(page, assistant, body):
    page.set_content(body)
    assert assistant.submission_confirmed(page) is True


@pytest.mark.parametrize("body", [
    "<p>Your application was successfully submitted. Verify your email address to finish applying.</p>",
    "<p>If your application was successfully submitted. We'll contact you if there are next steps.</p>",
    "<p hidden>Your application was successfully submitted. We'll contact you if there are next steps.</p>",
    "<p>Your application was successfully submitted. We'll contact you.</p><button>Submit Application</button>",
])
def test_advice_hidden_text_and_unfinished_forms_are_still_not_receipts(page, assistant, body):
    page.set_content(body)
    assert assistant.submission_confirmed(page) is False
