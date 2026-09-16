"""
The dashboard's controls: resuming a stopped run, reloading agent code into a
live run, and keeping track of a run across a restart of the dashboard itself.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import web_ui  # noqa: E402


class Record:
    def __init__(self, app_id=1, status="needs_user_review", url="https://jobs.example.com/apply/42"):
        self.id, self.status, self.url = app_id, status, url
        self.title, self.company, self.location = "Network Engineer", "Example Corp", "Remote"
        self.dedup_key, self.notes = "key", "required fields still blank: State"
        self.updated_at = None


@pytest.fixture
def client(monkeypatch):
    started = []
    monkeypatch.setattr(web_ui, "_run_apply",
                        lambda url, open_url="": started.append((url, open_url)))
    monkeypatch.setattr(web_ui.threading, "Thread",
                        lambda target, args=(), daemon=None: type("T", (), {"start": lambda s: target(*args)})())
    web_ui._RUNS.clear()
    c = web_ui.app.test_client()
    c.started = started
    return c


def test_resume_starts_the_application_again(client, monkeypatch):
    record = Record()
    monkeypatch.setattr(web_ui, "get_tracker", lambda: type("T", (), {"list_all": lambda s: [record]})())
    response = client.post("/resume/1")
    assert response.status_code == 302
    assert client.started == [(record.url, "")]  # no page recorded: start from the posting


def test_resume_reopens_the_page_the_application_reached(client, monkeypatch):
    """Resuming from the posting meant walking the whole wizard again. The
    page the last run reached is reopened instead -- the part-filled form."""
    record = Record()
    record.last_page_url = "https://careers.example.com/apply/42/step/3"
    monkeypatch.setattr(web_ui, "get_tracker", lambda: type("T", (), {"list_all": lambda s: [record]})())
    client.post("/resume/1")
    assert client.started == [(record.url, record.last_page_url)]


def test_deleting_an_application_removes_it(client, monkeypatch):
    """The user asks for this from the row itself; nothing deletes on its own."""
    record, deleted = Record(), []
    tracker = type("T", (), {"list_all": lambda s: [record],
                             "delete": lambda s, key: deleted.append(key) or True})()
    monkeypatch.setattr(web_ui, "get_tracker", lambda: tracker)
    response = client.post("/delete/1")
    assert response.status_code == 302
    assert deleted == [record.dedup_key]


def test_stopping_one_application_does_not_need_the_run_bookkeeping(client, monkeypatch):
    """The Stop button refused to work on a run the user could see, because
    this server's record of it had been lost to a reload. Stopping now acts on
    the processes themselves."""
    record, ended = Record(), []
    monkeypatch.setattr(web_ui, "get_tracker", lambda: type("T", (), {"list_all": lambda s: [record]})())
    monkeypatch.setattr(web_ui, "_end_any_run", lambda: ended.append(True) or 1)
    web_ui._RUNS.clear()  # nothing recorded here at all
    response = client.post("/stop-application/1")
    assert response.status_code == 302
    assert ended == [True]


def test_resume_refuses_an_application_already_submitted(client, monkeypatch):
    record = Record(status="submitted")
    monkeypatch.setattr(web_ui, "get_tracker", lambda: type("T", (), {"list_all": lambda s: [record]})())
    response = client.post("/resume/1", follow_redirects=True)
    assert client.started == []
    assert "already submitted" in response.get_data(as_text=True)


def test_resume_refuses_while_another_run_is_live(client, monkeypatch):
    record = Record()
    monkeypatch.setattr(web_ui, "get_tracker", lambda: type("T", (), {"list_all": lambda s: [record]})())
    monkeypatch.setattr(web_ui, "_process_alive", lambda pid: pid == 4242)
    web_ui._RUNS["https://jobs.example.com/other"] = {
        "state": "running", "log": "", "started": None, "proc": None,
        "pid": 4242, "stopping": False}          # a process that is still there
    response = client.post("/resume/1", follow_redirects=True)
    assert client.started == []
    assert "still running" in response.get_data(as_text=True)


def test_a_run_whose_process_is_gone_does_not_block_a_new_one(client, monkeypatch):
    """The dashboard refused to start an application because of a run it had
    recorded as running long after that run ended -- the state outlived the
    process, and the button did nothing with no explanation."""
    record = Record()
    monkeypatch.setattr(web_ui, "get_tracker", lambda: type("T", (), {"list_all": lambda s: [record]})())
    web_ui._RUNS["https://jobs.example.com/finished"] = {
        "state": "running", "log": "", "started": None, "proc": None,
        "pid": None, "stopping": False}          # nothing behind it any more
    client.post("/resume/1")
    assert client.started == [(record.url, "")]


def test_reload_agent_signals_a_waiting_run(client, tmp_path, monkeypatch):
    signals = tmp_path / "data"
    signals.mkdir()
    (signals / "_signal_Example.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(web_ui, "BASE_DIR", tmp_path)
    assert client.post("/reload-agent").status_code == 302
    assert (signals / "_signal_Example.txt").read_text(encoding="utf-8") == "reload_code"


def test_reload_agent_says_so_when_nothing_is_waiting(client, tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(web_ui, "BASE_DIR", tmp_path)
    response = client.post("/reload-agent", follow_redirects=True)
    assert "nothing to reload" in response.get_data(as_text=True).lower()


def test_a_live_run_survives_the_dashboard_restarting(tmp_path, monkeypatch):
    """Editing the code reloads the dashboard; the application in the browser
    keeps going, so its run must still be listed (and stoppable) afterwards."""
    monkeypatch.setattr(web_ui, "_RUNS_FILE", tmp_path / "_runs.json")
    monkeypatch.setattr(web_ui, "_process_alive", lambda pid: True)
    web_ui._RUNS.clear()
    web_ui._RUNS["https://jobs.example.com/apply/42"] = {
        "state": "running", "log": "run.log", "pid": 4242,
        "started": web_ui.datetime.now(web_ui.timezone.utc), "proc": None, "stopping": False}
    web_ui._save_runs()

    web_ui._RUNS.clear()          # as if the process had restarted
    web_ui._load_runs()
    restored = web_ui._RUNS["https://jobs.example.com/apply/42"]
    assert restored["state"] == "running" and restored["pid"] == 4242


def test_a_run_that_died_while_the_dashboard_was_down_is_not_shown_as_running(tmp_path, monkeypatch):
    monkeypatch.setattr(web_ui, "_RUNS_FILE", tmp_path / "_runs.json")
    (tmp_path / "_runs.json").write_text(json.dumps({
        "https://jobs.example.com/apply/42": {"state": "running", "log": "", "pid": 999999,
                                              "started": "2026-09-15T00:00:00+00:00"}}), encoding="utf-8")
    monkeypatch.setattr(web_ui, "_process_alive", lambda pid: False)
    web_ui._RUNS.clear()
    web_ui._load_runs()
    assert web_ui._RUNS["https://jobs.example.com/apply/42"]["state"].startswith("ended")


def test_an_error_page_is_not_recorded_as_progress():
    """An API error page was recorded as where an application had got to, and
    Resume walked the browser straight back into it."""
    import apply_flow

    assert not apply_flow.worth_returning_to(
        "https://jobs.dayforcehcm.com/api/auth/error?error=Request%20failed")
    assert not apply_flow.worth_returning_to("https://accounts.google.com/signin")
    assert not apply_flow.worth_returning_to("about:blank")
    assert apply_flow.worth_returning_to(
        "https://jobs.dayforcehcm.com/en-US/lumos/CANDIDATEPORTAL/jobs/9416/apply/manualApplication")
