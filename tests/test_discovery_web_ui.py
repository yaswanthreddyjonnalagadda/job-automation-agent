"""Tests for Discovered Opportunities feed and routes in web_ui.py."""

import sqlite3
import pytest
from unittest.mock import patch, MagicMock

import web_ui
from job_discovery import ensure_discovery_schema


@pytest.fixture
def client(monkeypatch, tmp_path):
    test_db = tmp_path / "test_applications.db"
    conn = sqlite3.connect(str(test_db))
    # applications table
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
    ensure_discovery_schema(conn)
    # Insert test discovered job
    conn.execute("""
        INSERT INTO discovered_jobs (
            id, dedup_key, company_name, company_token, ats_type, job_title,
            location, is_remote, url, match_score, matched_skills, status,
            discovered_at, updated_at
        ) VALUES (1, 'k1', 'Cloudflare', 'cloudflare', 'greenhouse', 'Senior Network Engineer',
                  'Remote', 1, 'https://boards.greenhouse.io/cloudflare/jobs/123', 95, '[\"Network\"]',
                  'discovered', '2026-10-22T00:00:00Z', '2026-10-22T00:00:00Z')
    """)
    conn.commit()
    conn.close()

    import job_discovery
    monkeypatch.setattr(job_discovery, "DB_PATH", test_db)

    web_ui.app.config["TESTING"] = True
    with web_ui.app.test_client() as c:
        yield c, test_db


def test_index_renders_discovered_jobs(client):
    c, _ = client
    resp = c.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Suggested jobs" in html
    assert "Cloudflare" in html
    assert "Senior Network Engineer" in html
    assert "95 percent match" in html and "Start application" in html


def test_dismiss_discovered_job(client):
    c, test_db = client
    resp = c.post("/api/discovered/1/dismiss", data={"csrf_token": web_ui.web_guard.TOKEN})
    assert resp.status_code == 302

    conn = sqlite3.connect(str(test_db))
    row = conn.execute("SELECT status FROM discovered_jobs WHERE id = 1").fetchone()
    assert row[0] == "dismissed"
    conn.close()


def test_scan_discovered_jobs_endpoint(client):
    c, _ = client
    with patch("job_discovery.JobDiscoveryEngine.scan_all", return_value={"companies_scanned": 1}) as mock_scan:
        resp = c.post("/api/discovered/scan", data={"csrf_token": web_ui.web_guard.TOKEN})
        assert resp.status_code == 302
        assert mock_scan.called


def test_remote_webhook_command_endpoint(client):
    c, _ = client
    with patch("notifications.handle_remote_command", return_value={"status": "ok", "message": "Command executed"}) as mock_cmd:
        resp = c.post("/api/webhook/command", data={"command": "/status", "csrf_token": web_ui.web_guard.TOKEN})
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"
        mock_cmd.assert_called_with("/status")


def test_telegram_webhook_endpoint(client):
    c, _ = client
    with patch("notifications.handle_remote_command", return_value={"status": "ok", "message": "Status OK"}) as mock_cmd:
        payload = {"message": {"text": "/status", "chat": {"id": 12345}}}
        resp = c.post("/api/webhook/telegram", json=payload, headers={"X-CSRF-Token": web_ui.web_guard.TOKEN})
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"
        mock_cmd.assert_called_with("/status")

