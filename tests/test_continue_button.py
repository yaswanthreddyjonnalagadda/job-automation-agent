"""A run waiting for the owner has a Continue button on the dashboard.

Rackspace, 29 September: the run stopped for the owner and waited for its signal file; the dashboard showed
Continue only for signal files that already existed -- an answer already sent -- so there was no button, and
Resume refused because the run was still going. A waiting run now leaves a _waiting_ note the dashboard reads.
"""
import json
import threading
import time
from pathlib import Path

import web_ui
from browser_automation import JobApplicationAssistant, waiting_note_for


def test_a_waiting_note_sits_beside_the_signal_file():
    assert waiting_note_for(Path("data/_signal_Acme_Engineer.txt")) == Path("data/_waiting_Acme_Engineer.txt")


def test_the_dashboard_lists_each_waiting_run_with_its_job(tmp_path):
    (tmp_path / "_waiting_Acme_Engineer.txt").write_text("2026-09-29T08:00:00")
    (tmp_path / "_job_Acme_Engineer.json").write_text(json.dumps({"title": "Network Engineer", "company": "Acme"}))
    (tmp_path / "_waiting_Globex_Admin.txt").write_text("x")                     # no job file: named from the file
    assert web_ui.waiting_runs(tmp_path) == [
        {"signal": "_signal_Acme_Engineer.txt", "label": "Network Engineer at Acme"},
        {"signal": "_signal_Globex_Admin.txt", "label": "Globex Admin"},
    ]


def test_nothing_waiting_nothing_listed(tmp_path):
    (tmp_path / "_signal_Acme_Engineer.txt").write_text("continue")                # an answer already sent
    assert web_ui.waiting_runs(tmp_path) == []


def test_waiting_shows_the_button_and_continue_ends_the_wait(tmp_path, monkeypatch):
    signal = tmp_path / "_signal_Acme_Engineer.txt"
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    result = {}

    def wait():
        result["decision"] = assistant.wait_for_signal(signal, poll_seconds=0.05, timeout_seconds=10)
    thread = threading.Thread(target=wait)
    thread.start()
    for _ in range(100):
        if waiting_note_for(signal).exists():
            break
        time.sleep(0.05)
    assert [w["signal"] for w in web_ui.waiting_runs(tmp_path)] == [signal.name]    # the button is there
    signal.write_text("continue")                                                    # what the button writes
    thread.join(10)
    assert result["decision"] == "continue"
    assert not waiting_note_for(signal).exists() and web_ui.waiting_runs(tmp_path) == []


def test_the_card_on_the_page_carries_a_continue_button(tmp_path, monkeypatch):
    import profile_setup
    from job_tracker import JobTracker
    monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
    monkeypatch.setattr(web_ui, "get_tracker", lambda: JobTracker(tmp_path / "a.db"))
    monkeypatch.setattr(web_ui, "waiting_runs", lambda data_dir: [{"signal": "_signal_Acme_Engineer.txt",
                                                                    "label": "Network Engineer at Acme"}])
    web_ui.app.config["TESTING"] = True
    page = web_ui.app.test_client().get("/").data.decode()
    assert "Network Engineer at Acme" in page and 'action="/signal/_signal_Acme_Engineer.txt"' in page
    assert 'value="continue">Continue</button>' in page
