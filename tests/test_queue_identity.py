"""Queued rows carry the same key the run uses (f329).

Runs key everything by sha256(url) (apply_flow:2006, jd_analyzer) while
queueing registered rows under the discovery hash (company|title|location|url):
every handoff, note, checkpoint and status update the run wrote landed under a
key with no row and evaporated -- update_status on a missing key is a silent
no-op. Queueing now registers the row under the run's own key, so recording
attaches; an empty URL keeps the discovered key (such a row can never run).
"""
import hashlib
import sqlite3

import job_discovery as jd


def seed(path, dedup="k1", url="https://x.example/1", company="Acme"):
    conn = sqlite3.connect(path)
    jd.ensure_discovery_schema(conn)
    conn.execute(
        "INSERT INTO discovered_jobs (dedup_key, company_name, company_token, ats_type, job_title,"
        " location, is_remote, url, match_score, discovered_at, updated_at)"
        " VALUES (?, ?, 'acme', 'greenhouse', 'Network Engineer', 'Austin, TX', 0,"
        " ?, 90, 't', 't')",
        (dedup, company, url),
    )
    conn.commit()
    conn.close()
    from job_tracker import JobTracker
    JobTracker(path)  # the live tracker owns the applications schema; the feed never hand-builds it


def urlkey(url):
    return hashlib.sha256(url.strip().lower().encode("utf-8")).hexdigest()


def test_the_row_key_is_the_runs_key(tmp_path, monkeypatch):
    db = tmp_path / "d.db"
    seed(db)
    monkeypatch.setattr(jd, "DB_PATH", db)
    assert jd.queue_discovered_job(1) is not None
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT dedup_key, status FROM applications").fetchone()
        assert row[0] == urlkey("https://x.example/1") and row[1] == "ready_to_apply"
    finally:
        conn.close()


def test_an_empty_url_keeps_the_discovered_key(tmp_path, monkeypatch):
    db = tmp_path / "d.db"
    seed(db, url="")
    monkeypatch.setattr(jd, "DB_PATH", db)
    assert jd.queue_discovered_job(1) is not None
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT dedup_key FROM applications").fetchone()[0] == "k1"
    finally:
        conn.close()


def test_the_same_posting_queued_twice_stays_one_row(tmp_path, monkeypatch):
    db = tmp_path / "d.db"
    seed(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO discovered_jobs (dedup_key, company_name, company_token, ats_type, job_title,"
        " location, is_remote, url, match_score, discovered_at, updated_at)"
        " VALUES ('k2', 'Acme', 'acme', 'greenhouse', 'Network Engineer', 'Austin, TX', 0,"
        " 'https://x.example/1', 90, 't', 't')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(jd, "DB_PATH", db)
    assert jd.queue_discovered_job(1) is not None
    assert jd.queue_discovered_job(2) is not None
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT count(*) FROM applications").fetchone()[0] == 1
    finally:
        conn.close()


def test_the_submitted_guard_still_fires_first(tmp_path, monkeypatch):
    db = tmp_path / "d.db"
    seed(db)
    from job_tracker import JobTracker
    tracker = JobTracker(db)
    tracker.create(dedup_key="old", title="Network Engineer", company="Acme", url="https://x.example/1")
    tracker.update_status("old", "submitted")
    monkeypatch.setattr(jd, "DB_PATH", db)
    assert jd.queue_discovered_job(1)["already_submitted"] is True
