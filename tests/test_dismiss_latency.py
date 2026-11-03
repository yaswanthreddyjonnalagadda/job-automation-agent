""""Not interested" answers at once, without reloading the page (f313).

Pressing it built a whole discovery engine (profile, resume) for a one-row
update, then reloaded the entire dashboard (another tracker listing, run
reconciliation, a 40-job query) to show one card gone. Now the button posts
lightly: JavaScript removes the card in place, and the plain form still
redirects for no-JavaScript.
"""
import json
import os
import sqlite3

import pytest

import job_discovery as jd


def seed(path):
    conn = sqlite3.connect(path)
    jd.ensure_discovery_schema(conn)
    conn.execute(
        "INSERT INTO discovered_jobs (dedup_key, company_name, company_token, ats_type, job_title,"
        " location, is_remote, url, match_score, discovered_at, updated_at)"
        " VALUES ('k1', 'Acme', 'acme', 'greenhouse', 'Network Engineer', 'Austin, TX', 0,"
        " 'https://x.example/1', 90, 't', 't')")
    conn.commit()
    conn.close()


def client(monkeypatch, tmp_path):
    import web_ui
    db = tmp_path / "d.db"
    seed(db)
    monkeypatch.setattr(jd, "DB_PATH", db)
    web_ui.app.config["TESTING"] = True
    return web_ui.app.test_client(), db


def status_of(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT status FROM discovered_jobs WHERE dedup_key = 'k1'").fetchone()[0]
    finally:
        conn.close()


def job_id(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT id FROM discovered_jobs WHERE dedup_key = 'k1'").fetchone()[0]
    finally:
        conn.close()


def test_dismiss_answers_json_without_reload(monkeypatch, tmp_path):
    browser, db = client(monkeypatch, tmp_path)
    resp = browser.post(f"/api/discovered/{job_id(db)}/dismiss", headers={"X-Requested-With": "fetch"})
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "dismissed": job_id(db), "removed": True}
    assert status_of(db) == "dismissed"


def test_dismiss_needs_no_profile_or_resume(monkeypatch, tmp_path):
    def fail(*a, **k):
        raise AssertionError("dismiss must not load this")
    monkeypatch.setattr(jd, "load_profile", fail)
    monkeypatch.setattr(jd, "load_resume_text", fail)
    browser, db = client(monkeypatch, tmp_path)
    resp = browser.post(f"/api/discovered/{job_id(db)}/dismiss", headers={"X-Requested-With": "fetch"})
    assert resp.status_code == 200 and resp.get_json()["ok"] is True
    assert status_of(db) == "dismissed"


def test_dismiss_without_javascript_still_redirects(monkeypatch, tmp_path):
    browser, db = client(monkeypatch, tmp_path)
    resp = browser.post(f"/api/discovered/{job_id(db)}/dismiss")
    assert resp.status_code == 302 and resp.location.endswith("/")
    assert status_of(db) == "dismissed"


def test_dismiss_form_marks_itself_for_in_place_removal():
    import re

    import web_ui
    forms = re.findall(r"<form[^>]*>", web_ui.INDEX_HTML)
    dismiss = [f for f in forms if "/dismiss" in f]
    assert dismiss and all("data-remove-card" in f for f in dismiss)


def test_current_runs_with_no_runs_touches_no_tracker(monkeypatch):
    import web_ui

    def fail():
        raise AssertionError("no runs: no tracker needed")
    monkeypatch.setattr(web_ui, "get_tracker", fail)
    monkeypatch.setattr(web_ui, "_RUNS", {})
    assert web_ui._current_runs() == {}


def test_current_runs_reuses_preloaded_records(monkeypatch):
    from types import SimpleNamespace

    import web_ui

    def fail():
        raise AssertionError("records were preloaded")
    monkeypatch.setattr(web_ui, "get_tracker", lambda: SimpleNamespace(list_all=fail, events=lambda k: []))
    monkeypatch.setattr(web_ui, "_RUNS", {"http://x.example/": {
        "state": "finished", "log": "", "pid": None, "started": None, "proc": None}})
    assert web_ui._current_runs(app_records=[])["http://x.example/"]["state"] == "finished"


@pytest.mark.skipif(os.name != "nt", reason="tasklist liveness checks are Windows-only")
def test_process_liveness_is_checked_once_per_pid(monkeypatch):
    import web_ui
    calls = []

    class Reply:
        stdout = "no such process"
    monkeypatch.setattr(web_ui.subprocess, "run", lambda *a, **k: calls.append(a) or Reply())
    assert web_ui._process_alive(424242) is False
    assert web_ui._process_alive(424242) is False
    assert len(calls) == 1
