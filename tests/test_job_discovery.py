"""Tests for Autonomous ATS Job Discovery Fleet (job_discovery.py / P3-1)."""

import json
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from job_discovery import (
    ensure_discovery_schema,
    calculate_match_score,
    compute_job_hash,
    fetch_greenhouse_feed,
    fetch_lever_feed,
    JobDiscoveryEngine,
)


def test_ensure_discovery_schema(tmp_path):
    db_file = tmp_path / "test_apps.db"
    conn = sqlite3.connect(str(db_file))
    ensure_discovery_schema(conn)

    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='discovered_jobs'")
    assert cur.fetchone() is not None

    cur.execute("PRAGMA table_info(discovered_jobs)")
    cols = [col[1] for col in cur.fetchall()]
    assert "dedup_key" in cols
    assert "match_score" in cols
    assert "matched_skills" in cols
    assert "status" in cols
    conn.close()


def test_calculate_match_score_high_fit():
    profile = {
        "target_titles": ["Network Engineer", "Cloud Network Engineer", "Senior Network Engineer"],
        "locations": ["Remote", "Fairfax", "VA"],
    }
    score, skills = calculate_match_score("Senior Network Engineer - Cloud Infrastructure", "Remote", profile)
    assert score >= 70
    assert "Senior Network Engineer" in skills
    assert "Remote" in skills
    assert "Senior-Level" in skills


def test_calculate_match_score_negative_disqualification():
    profile = {
        "target_titles": ["Network Engineer"],
        "locations": ["Remote"],
    }
    score, skills = calculate_match_score("Corporate Counsel & IP Attorney", "San Francisco, CA", profile)
    assert score == 0
    assert len(skills) == 0

    score_intern, _ = calculate_match_score("Network Engineer Intern (Summer 2027)", "Remote", profile)
    assert score_intern == 0


def test_compute_job_hash():
    h1 = compute_job_hash("Cloudflare", "Senior Network Engineer", "Remote", "https://boards.greenhouse.io/cloudflare/jobs/12345?gh_jid=12345")
    h2 = compute_job_hash("Cloudflare", "Senior Network Engineer", "Remote", "https://boards.greenhouse.io/cloudflare/jobs/12345")
    # Same base URL should produce exact same hash
    assert h1 == h2


def test_engine_queue_and_dismiss_flow(tmp_path):
    db_file = tmp_path / "test_flow.db"
    conn = sqlite3.connect(str(db_file))
    # Create applications table mock
    conn.execute("""
        CREATE TABLE applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dedup_key TEXT UNIQUE,
            title TEXT,
            company TEXT,
            location TEXT,
            source_site TEXT,
            url TEXT,
            status TEXT,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    conn.commit()
    conn.close()

    engine = JobDiscoveryEngine(db_path=db_file)
    conn = engine._get_connection()
    conn.execute("""
        INSERT INTO discovered_jobs (
            dedup_key, company_name, company_token, ats_type, job_title,
            location, is_remote, url, match_score, matched_skills, status,
            discovered_at, updated_at
        ) VALUES ('k1', 'Cloudflare', 'cloudflare', 'greenhouse', 'Senior Network Engineer',
                  'Remote', 1, 'https://example.com/job1', 90, '[\"Network\"]', 'discovered',
                  '2026-10-22T00:00:00Z', '2026-10-22T00:00:00Z')
    """)
    conn.commit()
    conn.close()

    # Verify discovered jobs retrieval
    jobs = engine.get_discovered_jobs(min_score=50)
    assert len(jobs) == 1
    assert jobs[0]["company_name"] == "Cloudflare"
    assert jobs[0]["match_score"] == 90

    # Queue for application
    queued = engine.queue_for_application(jobs[0]["id"])
    assert queued is not None

    # Check status updated in discovered_jobs
    jobs_after = engine.get_discovered_jobs(status="discovered")
    assert len(jobs_after) == 0

    # Check inserted in applications table
    conn = engine._get_connection()
    app = conn.execute("SELECT * FROM applications WHERE dedup_key = 'k1'").fetchone()
    assert app is not None
    assert app["title"] == "Senior Network Engineer"
    assert app["status"] == "ready_to_apply"
    conn.close()



def test_already_submitted_jobs_are_excluded_from_discovery_and_queue(tmp_path):
    """Jobs already submitted in applications must never appear in discovered opportunities or queue duplicates."""
    db_file = tmp_path / "test_submitted_exclusion.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("""
        CREATE TABLE applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dedup_key TEXT UNIQUE,
            title TEXT,
            company TEXT,
            location TEXT,
            source_site TEXT,
            url TEXT,
            status TEXT,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    # Insert an already-submitted Coinbase application
    conn.execute("""
        INSERT INTO applications (dedup_key, title, company, url, status, created_at, updated_at)
        VALUES ('app_cb', 'Senior Network Engineer', 'Coinbase', 'https://example.com/coinbase-job', 'submitted',
                '2026-10-22T00:00:00Z', '2026-10-22T01:00:00Z')
    """)
    conn.commit()
    conn.close()

    engine = JobDiscoveryEngine(db_path=db_file)
    conn = engine._get_connection()
    # Insert two discovered jobs: one for Coinbase (already submitted), one for Roblox (unsubmitted)
    conn.execute("""
        INSERT INTO discovered_jobs (
            dedup_key, company_name, company_token, ats_type, job_title,
            location, is_remote, url, match_score, matched_skills, status,
            discovered_at, updated_at
        ) VALUES 
        ('k_cb', 'Coinbase', 'coinbase', 'greenhouse', 'Senior Network Engineer',
         'Remote', 1, 'https://example.com/coinbase-job', 95, '["Network"]', 'discovered',
         '2026-10-22T00:00:00Z', '2026-10-22T00:00:00Z'),
        ('k_rb', 'Roblox', 'roblox', 'greenhouse', 'Network Engineer',
         'Remote', 1, 'https://example.com/roblox-job', 90, '["Network"]', 'discovered',
         '2026-10-22T00:00:00Z', '2026-10-22T00:00:00Z')
    """)
    conn.commit()
    conn.close()

    # 1. get_discovered_jobs must exclude the submitted Coinbase job
    available = engine.get_discovered_jobs(min_score=50)
    assert len(available) == 1
    assert available[0]["company_name"] == "Roblox"

    # 2. queue_for_application on the submitted Coinbase job must refuse and mark submitted
    conn = engine._get_connection()
    cb_row = conn.execute("SELECT id FROM discovered_jobs WHERE company_name = 'Coinbase'").fetchone()
    conn.close()
    
    queued_cb = engine.queue_for_application(cb_row["id"])
    assert queued_cb is not None
    assert queued_cb.get("already_submitted") is True

    # Check applications table: no duplicate row was created
    conn = engine._get_connection()
    cb_apps = conn.execute("SELECT * FROM applications WHERE company = 'Coinbase'").fetchall()
    assert len(cb_apps) == 1
    assert cb_apps[0]["status"] == "submitted"
    conn.close()
