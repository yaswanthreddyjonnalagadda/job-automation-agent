"""Unit tests for notifications.py."""

import json
from unittest.mock import patch, MagicMock
import pytest

import notifications


def test_notification_config(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/123/abc")
    monkeypatch.setenv("NOTIFICATION_MIN_SCORE", "90")

    cfg = notifications.get_notification_config()
    assert cfg["telegram_bot_token"] == "test-token"
    assert cfg["telegram_chat_id"] == "12345"
    assert cfg["discord_webhook_url"] == "https://discord.com/api/webhooks/123/abc"
    assert cfg["min_score"] == 90


def test_format_discovered_job_telegram():
    job = {
        "id": 42,
        "job_title": "Senior Network Engineer",
        "company_name": "Datadog",
        "location": "Remote",
        "ats_type": "greenhouse",
        "url": "https://boards.greenhouse.io/datadog/jobs/42",
        "match_score": 95,
        "matched_skills": ["Network", "Python", "BGP"],
    }
    msg = notifications.format_discovered_job_telegram(job)
    assert "95% Match" in msg
    assert "Senior Network Engineer" in msg
    assert "Datadog" in msg
    assert "/apply 42" in msg
    assert "/dismiss 42" in msg


def test_format_discovered_job_discord():
    job = {
        "id": 99,
        "job_title": "Lead Cloud Infrastructure Engineer",
        "company_name": "Coinbase",
        "location": "Remote - US",
        "ats_type": "lever",
        "url": "https://jobs.lever.co/coinbase/99",
        "match_score": 92,
        "matched_skills": ["Cloud", "AWS"],
    }
    embed = notifications.format_discovered_job_discord(job)
    assert "92%" in embed["content"]
    assert len(embed["embeds"]) == 1
    assert embed["embeds"][0]["title"] == "Lead Cloud Infrastructure Engineer @ Coinbase"
    assert embed["embeds"][0]["url"] == "https://jobs.lever.co/coinbase/99"


def test_notify_discovered_job_below_threshold(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_MIN_SCORE", "90")
    job = {"id": 1, "match_score": 80}
    with patch("notifications.send_http_json") as mock_send:
        res = notifications.notify_discovered_job(job)
        assert res == {}
        assert not mock_send.called


def test_notify_discovered_job_delivery(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_MIN_SCORE", "85")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg-tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "tg-chat")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/test")
    monkeypatch.setenv("GENERIC_WEBHOOK_URL", "https://example.com/webhook")

    job = {
        "id": 10,
        "job_title": "Network Engineer",
        "company_name": "Cloudflare",
        "match_score": 95,
        "ats_type": "greenhouse",
        "url": "https://cloudflare.com/job/10",
    }

    with patch("notifications.send_http_json", return_value=True) as mock_send:
        results = notifications.notify_discovered_job(job)
        assert results["telegram"] is True
        assert results["discord"] is True
        assert results["slack"] is True
        assert results["generic"] is True
        assert mock_send.call_count == 4


def test_send_http_json():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        assert notifications.send_http_json("https://example.com/api", {"test": 1}) is True


def test_send_http_json_failure():
    with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
        assert notifications.send_http_json("https://example.com/api", {"test": 1}) is False


def test_handle_remote_command_apply():
    with patch("job_discovery.JobDiscoveryEngine.queue_for_application", return_value={"id": 12, "job_title": "DevOps", "company_name": "TestCo"}):
        res = notifications.handle_remote_command("/apply 12")
        assert res["status"] == "ok"
        assert "Successfully queued" in res["message"]


def test_handle_remote_command_dismiss():
    with patch("job_discovery.JobDiscoveryEngine.dismiss_job", return_value=True):
        res = notifications.handle_remote_command("/dismiss 12")
        assert res["status"] == "ok"
        assert "Dismissed job #12" in res["message"]


def test_handle_remote_command_unknown():
    res = notifications.handle_remote_command("/invalid")
    assert res["status"] == "error"
    assert "Unknown command" in res["message"]

