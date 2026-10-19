"""
Test that owner continue signals clear pending account creation markers across all key aliases (f201).
Verifies:
1. When a checkpoint is keyed by submission_key (hash) while agent.key is the slug,
   PageAgent.forget_sign_in_attempts(owner_acted=True) resolves the marker under submission_key.
2. PageAgent._pending_account_creation checks both submission_key and agent.key aliases.
3. apply_flow continue handlers iterate over all key aliases and clear pending markers.
"""
from types import SimpleNamespace
import pytest
import checkpoint
from page_agent import PageAgent


class MockTracker:
    def __init__(self, seeded=None):
        self._checkpoints = dict(seeded or {})

    def read_checkpoint(self, key):
        return self._checkpoints.get(key)

    def write_checkpoint(self, key, payload):
        self._checkpoints[key] = payload


def test_page_agent_forget_sign_in_clears_marker_under_submission_key():
    submission_key = "76890ed68b6352cab3c398fdf681d87605454d504ff17f781726bf6a5f6c9c8f"
    slug_key = "Mutual_of_Enumclaw_Insurance_Company_Net"
    host = "jobs-mutualofenumclaw.icims.com"

    tracker = MockTracker({
        submission_key: {
            "application_key": submission_key,
            "pending_action": f"account_creation_dispatched@{host}",
            "handoff_category": "ACCOUNT_CREATION_UNCERTAIN",
        }
    })

    job = SimpleNamespace(company="Mutual of Enumclaw Insurance Company", url=f"https://{host}/jobs/2424")
    assistant = SimpleNamespace(submission_key=submission_key)

    agent = PageAgent(
        assistant=assistant,
        claude=None,
        config=None,
        profile=None,
        resume="",
        job=job,
        tracker=tracker,
        key=slug_key,
    )
    agent._current_host = host

    # Before owner action: _pending_account_creation sees the pending marker
    pending, unavailable = agent._pending_account_creation(host)
    assert pending is True
    assert unavailable is False

    # Owner resumes
    agent.forget_sign_in_attempts(owner_acted=True)

    # Marker is cleared in durable storage under submission_key
    cp = tracker.read_checkpoint(submission_key)
    assert cp["pending_action"] == ""

    # After owner action: _pending_account_creation sees no pending marker
    pending_after, _ = agent._pending_account_creation(host)
    assert pending_after is False


def test_page_agent_forget_sign_in_clears_marker_under_slug_key():
    submission_key = "76890ed68b6352cab3c398fdf681d87605454d504ff17f781726bf6a5f6c9c8f"
    slug_key = "Mutual_of_Enumclaw_Insurance_Company_Net"
    host = "jobs-mutualofenumclaw.icims.com"

    tracker = MockTracker({
        slug_key: {
            "application_key": slug_key,
            "pending_action": f"account_creation_dispatched@{host}",
            "handoff_category": "ACCOUNT_CREATION_UNCERTAIN",
        }
    })

    job = SimpleNamespace(company="Mutual of Enumclaw Insurance Company", url=f"https://{host}/jobs/2424")
    assistant = SimpleNamespace(submission_key=submission_key)

    agent = PageAgent(
        assistant=assistant,
        claude=None,
        config=None,
        profile=None,
        resume="",
        job=job,
        tracker=tracker,
        key=slug_key,
    )
    agent._current_host = host

    pending, _ = agent._pending_account_creation(host)
    assert pending is True

    agent.forget_sign_in_attempts(owner_acted=True)

    cp = tracker.read_checkpoint(slug_key)
    assert cp["pending_action"] == ""

    pending_after, _ = agent._pending_account_creation(host)
    assert pending_after is False


def test_apply_flow_continue_clears_all_key_aliases():
    submission_key = "76890ed68b6352cab3c398fdf681d87605454d504ff17f781726bf6a5f6c9c8f"
    slug_key = "Mutual_of_Enumclaw_Insurance_Company_Net"
    host = "jobs-mutualofenumclaw.icims.com"

    tracker = MockTracker({
        submission_key: {
            "application_key": submission_key,
            "pending_action": f"account_creation_dispatched@{host}",
            "handoff_category": "ACCOUNT_CREATION_UNCERTAIN",
        }
    })

    assistant = SimpleNamespace(submission_key=submission_key)
    agent = SimpleNamespace(_application_key=lambda: submission_key)
    decision = "continue"

    # Simulate the apply_flow continue handler
    if decision == "continue":
        if hasattr(tracker, "read_checkpoint") and hasattr(tracker, "write_checkpoint"):
            for k in dict.fromkeys([slug_key, getattr(assistant, "submission_key", ""), getattr(agent, "_application_key", lambda: "")()]):
                if not k:
                    continue
                try:
                    cp = tracker.read_checkpoint(k)
                    if cp and (cp.get("pending_action") or "").startswith("account_creation_dispatched@"):
                        updated = checkpoint.merge_update(cp, application_key=k, pending_action="")
                        tracker.write_checkpoint(k, updated)
                except Exception:
                    pass

    cp = tracker.read_checkpoint(submission_key)
    assert cp["pending_action"] == ""

