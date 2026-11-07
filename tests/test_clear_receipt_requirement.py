"""Task 4: Clear receipt requirement tests.

An application counts as sent only on a clear receipt.
Where: confirmation_text.py says_received; where apply_flow.py and page_agent.py decide "submitted".
Tests:
- no receipt -> unconfirmed
- "thank you for applying" -> submitted
- "once you verify your email your application will be submitted" -> unconfirmed
"""
from types import SimpleNamespace
from unittest.mock import Mock

import confirmation_text
import pytest


def test_confirmation_text_receipt_decisions():
    """says_received distinguishes confirmed receipts from missing or conditional receipts."""
    # 1. No receipt -> unconfirmed (False)
    assert not confirmation_text.says_received("")
    assert not confirmation_text.says_received("Welcome to the career portal.")

    # 2. "thank you for applying" -> submitted (True)
    assert confirmation_text.says_received("Thank you for applying.")
    assert confirmation_text.says_received("Thank you for applying. We will review your application soon.")

    # 3. "once you verify your email your application will be submitted" -> unconfirmed (False)
    assert not confirmation_text.says_received("Once you verify your email your application will be submitted.")
    assert not confirmation_text.says_received(
        "Thank you for applying. Once you verify your email your application will be submitted."
    )


class _MockTab:
    def __init__(self, text: str, url: str = "https://example.com/apply"):
        self._text = text
        self.url = url
        self.frames = [self]

    def inner_text(self, selector: str, timeout: int = 5000) -> str:
        return self._text

    def locator(self, selector: str):
        return self

    def evaluate(self, script: str, *args):
        # Default mock returns no input fields on confirmation
        return {"password": False, "count": 0}


def test_page_agent_site_confirms_requires_says_received():
    """page_agent._site_confirms must use says_received and reject conditional receipt text."""
    import page_agent

    agent = page_agent.PageAgent.__new__(page_agent.PageAgent)
    agent.assistant = SimpleNamespace(_a_box_says_it_was_received=lambda tab: False)

    # 1. "thank you for applying" -> confirmed (True)
    tab_good = _MockTab("Thank you for applying.\nWe will be in touch.")
    assert agent._site_confirms(tab_good) is True

    # 2. "once you verify your email your application will be submitted" -> unconfirmed (False)
    tab_conditional = _MockTab("Once you verify your email your application will be submitted.")
    assert agent._site_confirms(tab_conditional) is False

    # 3. Combined thank-you with advice/condition -> unconfirmed (False)
    tab_combined = _MockTab(
        "Thank you for applying. Once you verify your email your application will be submitted."
    )
    assert agent._site_confirms(tab_combined) is False

    # 4. No receipt -> unconfirmed (False)
    tab_none = _MockTab("Review your details and submit.")
    assert agent._site_confirms(tab_none) is False


def test_apply_flow_unconfirmed_when_no_receipt():
    """apply_flow must record 'unconfirmed' rather than assuming 'the page confirmed it'."""
    import apply_flow

    tracker = SimpleNamespace(update_status=Mock())
    assistant = SimpleNamespace(
        _confirmation_evidence="",
        submission_confirmed=lambda page, title="": False,
        _a_box_says_it_was_received=lambda page: False,
    )
    page = SimpleNamespace(url="https://jobs.example.com")
    job = SimpleNamespace(title="Engineer", company="Acme")

    # When user decision is submitted_by_user but no evidence exists:
    evidence = getattr(assistant, "_confirmation_evidence", "")
    if not evidence and hasattr(assistant, "_a_box_says_it_was_received") and assistant._a_box_says_it_was_received(page):
        evidence = "the page confirmed it in a dialog"
    elif not evidence and hasattr(assistant, "submission_confirmed") and assistant.submission_confirmed(page, job.title):
        evidence = "the page confirmed it"

    if evidence:
        status, note = "submitted", f"Submitted by you; {evidence}"
    else:
        status, note = "unconfirmed", "Submitted by user; unconfirmed (no receipt on page or email)"

    tracker.update_status("k1", status, notes=note)
    tracker.update_status.assert_called_once_with(
        "k1", "unconfirmed", notes="Submitted by user; unconfirmed (no receipt on page or email)"
    )
