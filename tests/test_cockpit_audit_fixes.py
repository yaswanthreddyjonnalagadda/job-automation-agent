"""Control routes and answer propagation must exercise real caller contracts."""
from types import SimpleNamespace
from unittest.mock import create_autospec

import pytest
import answer_ledger
import web_ui
from job_tracker import JobTracker


@pytest.mark.parametrize("route,payload", [
    ("/api/cockpit/stop", {}),
    ("/api/handoff/resolve", {"action": "cancel"}),
])
def test_cockpit_stop_and_cancel_stop_the_active_run(monkeypatch, route, payload):
    calls = []
    monkeypatch.setattr(web_ui, "_running_url", lambda: "https://jobs.example.com/role")
    monkeypatch.setattr(web_ui, "_stop_run", lambda url: calls.append(url) or True)
    monkeypatch.setattr(web_ui.handoff, "get_active_handoff", lambda: {"id": "test"})
    monkeypatch.setattr(web_ui.handoff, "clear_active_handoff", lambda: calls.append("cleared"))
    import web_guard
    response = web_ui.app.test_client().post(route, data={**payload, "csrf_token": web_guard.TOKEN})
    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert "https://jobs.example.com/role" in calls


def test_override_reaches_real_tracker_signature(monkeypatch, tmp_path):
    monkeypatch.setattr(answer_ledger, "DB_PATH", tmp_path / "ledger.db")
    tracker = create_autospec(JobTracker, instance=True)
    tracker.get.return_value = SimpleNamespace(url="https://jobs.example.com/role")
    monkeypatch.setattr(web_ui, "get_tracker", lambda: tracker)
    answer_id = web_ui.record_answer_ledger("key", "Question?", "Old")
    assert web_ui.update_answer_in_ledger(answer_id, "New")
    tracker.record_answer.assert_called_once_with(
        "key", "jobs.example.com", "Question?", "New", answered_by="user")


def test_override_is_persisted_in_sqlite_tracker(monkeypatch, tmp_path):
    monkeypatch.setattr(answer_ledger, "DB_PATH", tmp_path / "ledger.db")
    tracker = JobTracker(tmp_path / "tracker.db")
    tracker.create(dedup_key="key", title="Engineer", company="Example", url="https://jobs.example.com/role")
    monkeypatch.setattr(web_ui, "get_tracker", lambda: tracker)
    answer_id = web_ui.record_answer_ledger("key", "Question?", "Old")
    assert web_ui.update_answer_in_ledger(answer_id, "New")
    saved = tracker.answers_for(tracker.get("key").id)
    assert saved == [{"question": "Question?", "answer": "New", "host": "jobs.example.com", "answered_by": "user"}]


def test_stop_without_a_run_is_idempotent(monkeypatch):
    import web_guard
    monkeypatch.setattr(web_ui, "_running_url", lambda: None)
    monkeypatch.setattr(web_ui, "_stop_run", lambda url: False)
    response = web_ui.app.test_client().post("/api/cockpit/stop", data={"csrf_token": web_guard.TOKEN})
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "status": "not_running"}
