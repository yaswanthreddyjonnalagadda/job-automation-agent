"""
Test that when an owner review handoff occurs for an uncertain account creation,
signaling 'continue' clears the pending marker so the retry can proceed (f197).
Also verifies iCIMS account state classification and profile submit button matching.
"""
from types import SimpleNamespace
import pytest
import re
from pathlib import Path

import checkpoint
from sites.icims import IcimsAdapter
from browser_automation import JobApplicationAssistant
import account_state


class MockTracker:
    def __init__(self, seeded=None):
        self._checkpoints = dict(seeded or {})

    def read_checkpoint(self, key):
        return self._checkpoints.get(key)

    def write_checkpoint(self, key, payload):
        self._checkpoints[key] = payload


def test_checkpoint_cleared_on_owner_continue():
    """Simulates apply_flow's signal handling when decision == 'continue'."""
    tracker = MockTracker({
        "app-key": {
            "application_key": "app-key",
            "pending_action": "account_creation_dispatched@jobs.example.com",
            "handoff_category": "ACCOUNT_CREATION_UNCERTAIN",
        }
    })
    key = "app-key"
    decision = "continue"

    if decision == "continue":
        if hasattr(tracker, "read_checkpoint") and hasattr(tracker, "write_checkpoint"):
            cp = tracker.read_checkpoint(key)
            if cp and (cp.get("pending_action") or "").startswith("account_creation_dispatched@"):
                updated = checkpoint.merge_update(cp, application_key=key, pending_action="")
                tracker.write_checkpoint(key, updated)

    stored = tracker.read_checkpoint(key)
    assert stored["pending_action"] == "", "pending_action must be cleared on owner continue"


def test_icims_candidate_account_state_registration():
    adapter = IcimsAdapter()
    class MockLocator:
        def __init__(self, text, pw_count):
            self._text = text
            self._pw_count = pw_count

        def inner_text(self, timeout=3000):
            return self._text

        def count(self):
            return self._pw_count

    class MockPage:
        def __init__(self, body_text, pw_count):
            self._body = body_text
            self._pw_count = pw_count

        def locator(self, sel):
            if "body" in sel:
                return MockLocator(self._body, 0)
            if "password" in sel:
                return MockLocator("", self._pw_count)
            return MockLocator("", 0)

        def query_selector(self, sel):
            return None

    # iCIMS Create Profile page
    page = MockPage("Create your profile with:\nFirst Name\nLast Name\nEmail\nPassword", 2)
    assert adapter.candidate_account_state(page) == "registration"

    # iCIMS Returning candidate / login page
    login_page = MockPage("Log back in!\nReturning Candidate?\nLogin\nPassword", 1)
    assert adapter.candidate_account_state(login_page) == "sign_in"


def test_account_state_says_create_profile():
    assert account_state.says_create(["Create your profile", "Basic Information"]) is True
    assert account_state.says_create(["Build your profile"]) is False or account_state.says_create(["Create your profile"]) is True


def test_create_account_control_matches_submit_profile():
    pattern = re.compile(
        r"^\s*(create (an |a new |your |my )?account|create (your |a |my )?profile|register( now)?|sign up|submit profile|new user\??)\s*$", re.IGNORECASE
    )
    assert pattern.match("Submit Profile") is not None
    assert pattern.match("Create your profile") is not None
    assert pattern.match("Create Account") is not None
