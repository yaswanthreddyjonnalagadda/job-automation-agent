"""Phone alerts for how an application ended: submitted, ready to submit, job gone, run failed.

One place decides it: the tracker, every time a run changes an application's status. A stop that needs the owner
is not announced here -- the wait for the owner does that (with the console link) -- and neither is anything the
owner changed by hand, a repeat of the same status, or a step along the way.
"""
import pytest

import phone_alert
from job_tracker import JobTracker


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(phone_alert, "send", lambda *args, **kw: calls.append((args, kw)) or True)
    return calls


@pytest.fixture
def tracker(tmp_path):
    t = JobTracker(tmp_path / "applications.db")
    t.create(dedup_key="k1", title="Senior Network Engineer", company="Copart, Inc", location="Las Vegas",
             url="https://jobs.example.com/1")
    return t


def test_a_submitted_application_is_announced_once(tracker, sent):
    tracker.update_status("k1", "submitted", notes="receipt ZX-91 sent to jane@example.invalid")
    assert len(sent) == 1
    (title, message), kw = sent[0]
    assert title == "Submitted: Copart, Inc" and "Senior Network Engineer" in message
    assert kw["priority"] == 3
    assert "jane@example.invalid" not in message and "ZX-91" not in message         # the note is not sent
    tracker.update_status("k1", "submitted", notes="a second note")
    assert len(sent) == 1                                                            # the same status: no repeat


def test_ready_to_submit_asks_for_the_owners_click(tracker, sent):
    tracker.update_status("k1", "ready_to_submit")
    (title, message), kw = sent[0]
    assert title == "Ready to submit: Copart, Inc" and "laptop" in message and kw["priority"] == 4


def test_a_job_that_does_not_exist_is_announced_quietly(tracker, sent):
    tracker.update_status("k1", "job_not_found")
    (title, _message), kw = sent[0]
    assert title == "Job doesn't exist: Copart, Inc" and kw["priority"] == 2


@pytest.mark.parametrize("status", ["prepared", "form_filled", "needs_user_review", "skipped",
                                    "DISQUALIFIED_POLICY_MISMATCH", "interviewing", "rejected", "offer"])
def test_other_statuses_are_not_announced(tracker, sent, status):
    tracker.update_status("k1", status)
    assert sent == []


def test_what_the_owner_changed_by_hand_is_not_announced(tracker, sent):
    tracker.update_status("k1", "submitted", by_owner=True)
    assert sent == []


def test_a_status_the_tracker_refused_is_not_announced(tracker, sent):
    tracker.update_status("k1", "submitted", by_owner=True)       # the owner says it is sent
    tracker.update_status("k1", "ready_to_submit")                # a run may not move it back
    assert sent == []


def test_a_failing_alert_never_stops_the_status_being_kept(tracker, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("no network")
    monkeypatch.setattr(phone_alert, "send", boom)
    tracker.update_status("k1", "submitted")
    assert tracker.get("k1").status == "submitted"


# ------------------------------------------------------------------ a run that failed
@pytest.mark.parametrize("code", [0, 3, 4])
def test_a_finished_or_expected_stop_is_not_a_failure(sent, code):
    phone_alert.run_ended("https://copart.wd12.myworkdayjobs.com/Copart/job/x?token=abc", code)
    assert sent == []


def test_a_failed_run_names_the_site_and_not_the_address(sent):
    phone_alert.run_ended("https://copart.wd12.myworkdayjobs.com/Copart/job/x?token=abc", 1)
    (title, message), kw = sent[0]
    assert title == "Run failed: copart.wd12.myworkdayjobs.com" and kw["priority"] == 4
    assert "token" not in message and "abc" not in message
