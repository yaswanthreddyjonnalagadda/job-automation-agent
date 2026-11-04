"""Queued suggestions register on the tracker's live copy, never its shadow (f329).

The suggestions feed lives in SQLite, but runs and the dashboard track in
Postgres. queue_discovered_job guarded and registered against the SQLite
applications table -- a shadow the live system never reads -- so submitted
postings (Fastly, Verizon, Rackspace, Roblox, all submitted 2 November) were
re-queued as ready_to_apply ghosts, and agent_status reported them as pending.
The guard and the registration now go through tracking.open_tracker().
"""
import sqlite3

import job_discovery
import tracking
from job_tracker import JobTracker


def _feed_db(path):
    conn = sqlite3.connect(str(path))
    job_discovery.ensure_discovery_schema(conn)
    conn.execute(
        """INSERT INTO discovered_jobs (dedup_key, company_name, company_token, ats_type, job_title, location, url,
           status, match_score, discovered_at, updated_at)
           VALUES ('feedhash', 'Fastly', 'fastly', 'greenhouse', 'Senior Engineer', 'Remote',
           'https://example.com/fastly-1', 'discovered', 90, 't', 't')"""
    )
    conn.commit()
    conn.close()


def test_a_submitted_posting_is_refused_even_when_the_shadow_is_empty(tmp_path, monkeypatch):
    feed = tmp_path / "feed.db"
    live = tmp_path / "live.db"
    _feed_db(feed)
    tracker = JobTracker(live)
    key = tracker.create(dedup_key="k" * 64, title="Senior Engineer", company="Fastly",
                         url="https://example.com/fastly-1")
    tracker.update_status("k" * 64, "submitted")
    assert key
    monkeypatch.setattr(tracking, "open_tracker", lambda db_path=None: JobTracker(live))
    item = job_discovery.queue_discovered_job(1, db_path=feed)
    assert item["already_submitted"] is True
    conn = sqlite3.connect(str(feed))
    assert conn.execute("SELECT status FROM discovered_jobs WHERE id=1").fetchone()[0] == "submitted"
    conn.close()


def test_queueing_registers_on_the_live_tracker_not_the_feed_file(tmp_path, monkeypatch):
    feed = tmp_path / "feed.db"
    live = tmp_path / "live.db"
    _feed_db(feed)
    monkeypatch.setattr(tracking, "open_tracker", lambda db_path=None: JobTracker(live))
    item = job_discovery.queue_discovered_job(1, db_path=feed)
    assert "already_submitted" not in item
    rows = JobTracker(live).list_all(status="ready_to_apply")
    assert len(rows) == 1 and rows[0].url == "https://example.com/fastly-1"
    from jd_analyzer import dedup_key_for_url
    assert rows[0].dedup_key == dedup_key_for_url("https://example.com/fastly-1")
    conn = sqlite3.connect(str(feed))
    shadow = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='applications'").fetchone()
    conn.close()
    assert shadow is None


def test_the_feed_hides_postings_submitted_on_the_live_tracker(tmp_path, monkeypatch):
    feed = tmp_path / "feed.db"
    live = tmp_path / "live.db"
    _feed_db(feed)
    tracker = JobTracker(live)
    tracker.create(dedup_key="k" * 64, title="Senior Engineer", company="Fastly",
                   url="https://example.com/fastly-1")
    tracker.update_status("k" * 64, "submitted")
    monkeypatch.setattr(tracking, "open_tracker", lambda db_path=None: JobTracker(live))
    engine = job_discovery.JobDiscoveryEngine(db_path=feed)
    assert engine.get_discovered_jobs(min_score=50) == []


def test_ingest_marks_a_live_submitted_posting_submitted(tmp_path):
    live = tmp_path / "live.db"
    tracker = JobTracker(live)
    tracker.create(dedup_key="k" * 64, title="Senior Engineer", company="Fastly",
                   url="https://example.com/fastly-1")
    tracker.update_status("k" * 64, "submitted")
    cover = job_discovery._submitted_cover(tracker)
    job = {"url": "https://example.com/fastly-1", "company_name": "Fastly", "job_title": "Senior Engineer"}
    assert job_discovery._ingest_status(job, cover) == "submitted"
    fresh = {"url": "https://example.com/other", "company_name": "Other", "job_title": "Engineer"}
    assert job_discovery._ingest_status(fresh, cover) == "discovered"
