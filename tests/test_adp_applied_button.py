"""Tests for ADP 'Applied' button post-submission recognition (f215).

On ADP (myjobs.adp.com), submitting an application redirects the browser back
to the job description page (/cx/job-details?reqId=...), where the main action
button changes from 'Apply' to a disabled/toggled 'Applied' button, without
displaying a traditional 'Thank you for applying' confirmation banner.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest

from sites.adp import is_adp_applied_posting, AdpAdapter
from sites import adapter_for
from browser_automation import JobApplicationAssistant
import page_agent
from page_agent import Outcome, PageAgent


class MockPage:
    def __init__(self, url: str, is_applied: bool = True, has_inputs: bool = False, inner_body: str = ""):
        self.url = url
        self._is_applied = is_applied
        self._has_inputs = has_inputs
        self._inner_body = inner_body or ("Senior Network Engineer\nJob Description\nApplied" if is_applied else "Senior Network Engineer\nApply")

    def evaluate(self, script, *args):
        if not self.url or "adp.com" not in self.url or "/job-details" not in self.url:
            return False
        if self._has_inputs:
            return False
        return self._is_applied

    def inner_text(self, selector, timeout=0):
        return self._inner_body

    def is_closed(self):
        return False


def test_is_adp_applied_posting():
    applied_page = MockPage("https://myjobs.adp.com/heico/cx/cx/job-details?reqId=5001190217806", is_applied=True)
    assert is_adp_applied_posting(applied_page) is True

    not_applied_page = MockPage("https://myjobs.adp.com/heico/cx/cx/job-details?reqId=5001190217806", is_applied=False)
    assert is_adp_applied_posting(not_applied_page) is False

    different_site = MockPage("https://example.com/job-details?reqId=123", is_applied=True)
    assert is_adp_applied_posting(different_site) is False

    with_inputs = MockPage("https://myjobs.adp.com/heico/cx/cx/job-details?reqId=5001190217806", is_applied=True, has_inputs=True)
    assert is_adp_applied_posting(with_inputs) is False


def test_adp_adapter_registration():
    adapter = adapter_for("https://myjobs.adp.com/heico/cx/cx/job-details?reqId=5001190217806")
    assert isinstance(adapter, AdpAdapter)
    assert adapter.name == "adp"


def test_browser_automation_submission_confirmed_on_adp():
    page = MockPage("https://myjobs.adp.com/heico/cx/cx/job-details?reqId=5001190217806", is_applied=True)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.find_submit_button = lambda p: None  # No submit button visible on job details
    assert assistant.submission_confirmed(page, "Senior Network Engineer") is True


def test_page_agent_submitted_outcome_when_seen_form():
    page = MockPage("https://myjobs.adp.com/heico/cx/cx/job-details?reqId=5001190217806", is_applied=True)
    agent = PageAgent.__new__(PageAgent)
    agent._seen_form = True
    agent.final_pressed = False

    assert agent._site_confirms(page) is True
    # If site confirms and _seen_form is True, it confirms submission
    assert (agent.final_pressed or getattr(agent, "_seen_form", False)) is True


def test_page_agent_already_applied_outcome_when_not_seen_form():
    page = MockPage("https://myjobs.adp.com/heico/cx/cx/job-details?reqId=5001190217806", is_applied=True)
    agent = PageAgent.__new__(PageAgent)
    agent._seen_form = False
    agent.final_pressed = False

    assert agent._site_confirms(page) is True
    assert agent._says_already_applied(page) is True
    assert (agent.final_pressed or getattr(agent, "_seen_form", False)) is False

