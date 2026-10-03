"""A run waiting for the owner has a Continue button on the dashboard.

Rackspace, 29 September: the run stopped for the owner and waited for its signal file; the dashboard showed
Continue only for signal files that already existed -- an answer already sent -- so there was no button, and
Resume refused because the run was still going. A waiting run now leaves a _waiting_ note the dashboard reads.
"""
import json
import threading
import time
from pathlib import Path
from bs4 import BeautifulSoup

import web_ui
from browser_automation import JobApplicationAssistant, waiting_note_for


def _continue_button(page):
    return BeautifulSoup(page, "html.parser").select_one(
        'form[action="/signal/_signal_Acme_Engineer.txt"] button[name="decision"][value="continue"]')


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
    monkeypatch.setattr(web_ui, "_running_url", lambda: "https://jobs.example.com/acme")
    web_ui.app.config["TESTING"] = True
    page = web_ui.app.test_client().get("/").data.decode()
    assert "Network Engineer at Acme" in page and 'action="/signal/_signal_Acme_Engineer.txt"' in page
    assert _continue_button(page).get_text(strip=True) == "Continue"


# --- Aristocrat, 29 September: the run was stopped while it waited; its note stayed, and the dashboard went on
# --- showing "Paused in the browser" with a Continue no process would ever read.

def _dashboard(tmp_path, monkeypatch, live=""):
    import profile_setup
    from job_tracker import JobTracker
    monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
    monkeypatch.setattr(web_ui, "get_tracker", lambda: JobTracker(tmp_path / "a.db"))
    monkeypatch.setattr(web_ui, "_RUNS_FILE", tmp_path / "_runs.json")
    monkeypatch.setattr(web_ui, "_running_url", lambda: live)
    (tmp_path / "_waiting_Acme_Engineer.txt").write_text("2026-09-29T09:09:14")
    (tmp_path / "_signal_Acme_Engineer.txt").write_text("reload_code")
    web_ui.app.config["TESTING"] = True
    return web_ui.app.test_client()


def test_a_note_left_by_a_run_that_is_gone_shows_no_continue_and_is_cleared(tmp_path, monkeypatch):
    page = _dashboard(tmp_path, monkeypatch, live="").get("/").data.decode()
    assert "Paused in the browser" not in page and _continue_button(page) is None
    assert not list(tmp_path.glob("_waiting_*")) and not list(tmp_path.glob("_signal_*"))


def test_a_live_run_keeps_its_continue(tmp_path, monkeypatch):
    page = _dashboard(tmp_path, monkeypatch, live="https://jobs.example.com/acme").get("/").data.decode()
    assert _continue_button(page).get_text(strip=True) == "Continue"
    assert (tmp_path / "_waiting_Acme_Engineer.txt").exists()


def test_stopping_clears_the_waiting_note(tmp_path, monkeypatch):
    client = _dashboard(tmp_path, monkeypatch)
    monkeypatch.setattr(web_ui, "_end_any_run", lambda: 1)
    client.post("/stop", data={"url": "https://jobs.example.com/acme"})
    assert not list(tmp_path.glob("_waiting_*")) and not list(tmp_path.glob("_signal_*"))
