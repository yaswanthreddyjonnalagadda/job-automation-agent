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
    monkeypatch.setattr(web_ui, "_run_apply", lambda url: started.append(url))
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
    assert client.started == [record.url]


def test_resume_refuses_an_application_already_submitted(client, monkeypatch):
    record = Record(status="submitted")
    monkeypatch.setattr(web_ui, "get_tracker", lambda: type("T", (), {"list_all": lambda s: [record]})())
    response = client.post("/resume/1", follow_redirects=True)
    assert client.started == []
    assert "already submitted" in response.get_data(as_text=True)


def test_resume_refuses_while_another_run_is_live(client, monkeypatch):
    record = Record()
    monkeypatch.setattr(web_ui, "get_tracker", lambda: type("T", (), {"list_all": lambda s: [record]})())
    web_ui._RUNS["other"] = {"state": "running", "log": "", "started": None, "proc": None, "pid": None,
                             "stopping": False}
    response = client.post("/resume/1", follow_redirects=True)
    assert client.started == []
    assert "already running" in response.get_data(as_text=True)


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
