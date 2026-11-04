"""Review vs hybrid submit policy (f320)."""
from datetime import datetime
from types import SimpleNamespace

import submit_policy as policy


def record(status, updated):
    return SimpleNamespace(status=status, updated_at=updated)


def tracker_with(*records):
    return SimpleNamespace(list_all=lambda: list(records))


def test_unset_mode_keeps_legacy_behavior(monkeypatch):
    monkeypatch.delenv("SUBMIT_MODE", raising=False)
    assert policy.auto_submit_allowed(True, tracker_with())[0] is True
    assert policy.auto_submit_allowed(False, tracker_with())[0] is True


def test_review_mode_holds_everything(monkeypatch):
    monkeypatch.setenv("SUBMIT_MODE", "review")
    ok, reason = policy.auto_submit_allowed(True, tracker_with())
    assert ok is False and "Review mode" in reason


def test_an_unknown_mode_waits_loudly(monkeypatch):
    monkeypatch.setenv("SUBMIT_MODE", "auto")
    ok, reason = policy.auto_submit_allowed(True, tracker_with())
    assert ok is False and "auto" in reason


def test_hybrid_needs_eligibility_and_room_under_the_cap(monkeypatch):
    monkeypatch.setenv("SUBMIT_MODE", "hybrid")
    monkeypatch.setenv("AUTO_SUBMIT_DAILY_CAP", "2")
    assert policy.auto_submit_allowed(False, tracker_with())[0] is False
    day = datetime.now().date().isoformat()
    full = tracker_with(record("submitted", day), record("submitted", day + "T10:00:00"))
    ok, reason = policy.auto_submit_allowed(True, full)
    assert ok is False and "cap" in reason.lower()
    room = tracker_with(record("submitted", day), record("needs_user_review", day))
    ok, reason = policy.auto_submit_allowed(True, room)
    assert ok is True and "1 of 2" in reason


def test_a_zero_cap_is_a_kill_switch(monkeypatch):
    monkeypatch.setenv("SUBMIT_MODE", "hybrid")
    monkeypatch.setenv("AUTO_SUBMIT_DAILY_CAP", "0")
    ok, reason = policy.auto_submit_allowed(True, tracker_with())
    assert ok is False and "off" in reason


def test_only_todays_confirmed_submits_count(monkeypatch):
    monkeypatch.setenv("SUBMIT_MODE", "hybrid")
    monkeypatch.delenv("AUTO_SUBMIT_DAILY_CAP", raising=False)
    assert policy.daily_cap() == 10
    tracker = tracker_with(record("submitted", "2026-01-01"), record("already_applied", "2099-01-01"),
                           record("submitted", datetime.now().date().isoformat()))
    assert policy.submitted_today(tracker) == 1
