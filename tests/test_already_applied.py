"""A job the site says you already applied for ends the run with its own status (f180).

Marathon: signed in with the owner's email, Workday said "You've already applied for this job." The agent answered
'needs owner', so the row read 'Needs you', the browser stayed open on a hand-over, and nothing was left for the owner
to do. The run now ends with the status 'Already applied' and the site's own words -- never counted as a submission by
the agent, since this run did not send it.
"""
import pytest

import page_agent
import phone_alert
import progress
import ui_shell
from job_tracker import STATUS_ALREADY_APPLIED, JobTracker
from page_agent import Outcome


def test_the_status_reads_as_already_applied_and_is_not_counted_as_a_submission():
    assert STATUS_ALREADY_APPLIED == "already_applied"
    assert ui_shell.status_label(STATUS_ALREADY_APPLIED) == "Already applied"
    assert ui_shell.status_group(STATUS_ALREADY_APPLIED) == "skipped"          # nothing left for the owner
    assert STATUS_ALREADY_APPLIED not in progress.SUBMITTED_OR_LATER           # the agent did not send it
    assert STATUS_ALREADY_APPLIED in progress.SKIPPED


def test_the_postgres_tracker_knows_the_status_too():
    db = pytest.importorskip("db")
    assert db.STATUS_ALREADY_APPLIED == STATUS_ALREADY_APPLIED


def test_the_dashboard_accepts_the_status_from_its_menu():
    import web_ui
    assert STATUS_ALREADY_APPLIED in web_ui.VALID_STATUSES


def test_the_owner_is_told_on_the_phone():
    assert phone_alert.STATUS_ALERTS[STATUS_ALREADY_APPLIED][0] == "Already applied"


class Tab:
    def __init__(self, text):
        self.text = text

    def inner_text(self, selector, timeout=0):
        return self.text


def agent():
    return page_agent.PageAgent.__new__(page_agent.PageAgent)


@pytest.mark.parametrize("text, expected", [
    ("Senior Network Engineer\nYou've already applied for this job.", True),
    ("You have already applied for this job", True),
    ("You already applied to this position.", True),
    ("Thank you for applying! We received your application.", False),         # a confirmation, not 'before'
    ("Have you already applied? Check your status.", False),
    ("My Information\nFirst name", False),
    ("", False),
])
def test_only_the_sites_own_already_applied_notice_counts(text, expected):
    assert agent()._says_already_applied(Tab(text)) is expected


def test_the_outcome_is_its_own_kind():
    assert Outcome("already_applied", object(), ["the site says you already applied"]).kind == "already_applied"


def test_the_run_records_it_with_the_sites_words_and_no_submission(tmp_path):
    import apply_flow
    tracker = JobTracker(tmp_path / "applications.db")
    tracker.create(dedup_key="k1", title="Senior Network Engineer", company="Marathon", location="", url="https://x.example/1")
    job = type("Job", (), {"title": "Senior Network Engineer", "company": "Marathon"})()
    apply_flow.record_already_applied(tracker, "k1", job, Outcome("already_applied", object(), ["the site says you already applied for this job"]))
    record = tracker.get("k1")
    assert record.status == "already_applied"
    assert "already applied" in record.notes.lower()
    assert tracker.find_submitted(url="https://x.example/1") is None          # not a submission by the agent
