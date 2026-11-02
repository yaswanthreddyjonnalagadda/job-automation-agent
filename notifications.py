"""
Remote notification and 1-tap mobile approval system.

Supports Telegram, Discord, Slack, and generic HTTP webhooks for alerting
when high-fit opportunities (e.g. >= 85% match) are discovered, or when an
application requires user attention or review.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
import urllib.error
from typing import Any, Dict, Optional

logger = logging.getLogger("notifications")


def get_notification_config() -> Dict[str, Any]:
    """Load notification credentials and thresholds from environment."""
    return {
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        "discord_webhook_url": os.getenv("DISCORD_WEBHOOK_URL", "").strip(),
        "slack_webhook_url": os.getenv("SLACK_WEBHOOK_URL", "").strip(),
        "generic_webhook_url": os.getenv("GENERIC_WEBHOOK_URL", "").strip(),
        "webhook_secret": os.getenv("NOTIFICATION_WEBHOOK_SECRET", "agent-secret").strip(),
        "min_score": int(os.getenv("NOTIFICATION_MIN_SCORE", "85")),
    }


def send_http_json(url: str, payload: dict, timeout: int = 10, headers: Optional[dict] = None) -> bool:
    """Send JSON payload over HTTP POST."""
    req_headers = {"Content-Type": "application/json", "User-Agent": "JobAutomationAgent/1.0"}
    if headers:
        req_headers.update(headers)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception as e:
        logger.warning(f"Failed to deliver notification to {url}: {e}")
        return False


def format_discovered_job_telegram(job: dict) -> str:
    """Format markdown message for Telegram."""
    score = job.get("match_score", 0)
    company = job.get("company_name", "Unknown")
    title = job.get("job_title", "Unknown Role")
    loc = job.get("location") or ("Remote" if job.get("is_remote") else "Unstated")
    ats = str(job.get("ats_type", "")).upper()
    url = job.get("url", "")
    skills = job.get("matched_skills", [])
    if isinstance(skills, str):
        try:
            skills = json.loads(skills)
        except Exception:
            skills = []
    skills_str = ", ".join(skills) if skills else "General Alignment"
    job_id = job.get("id", "")

    return (
        f"🎯 *High-Fit Opportunity Discovered ({score}% Match)*\n\n"
        f"💼 *{title}* @ *{company}*\n"
        f"📍 {loc} | ⚡ ATS: {ats}\n"
        f"🛠 Skills: {skills_str}\n\n"
        f"🔗 [View Job Posting]({url})\n\n"
        f"🚀 *Quick Commands:*\n"
        f"`/apply {job_id}` — 1-Click Apply\n"
        f"`/dismiss {job_id}` — Dismiss role"
    )


def format_discovered_job_discord(job: dict) -> dict:
    """Format webhook embed for Discord."""
    score = job.get("match_score", 0)
    company = job.get("company_name", "Unknown")
    title = job.get("job_title", "Unknown Role")
    loc = job.get("location") or ("Remote" if job.get("is_remote") else "Unstated")
    ats = str(job.get("ats_type", "")).upper()
    url = job.get("url", "")
    skills = job.get("matched_skills", [])
    if isinstance(skills, str):
        try:
            skills = json.loads(skills)
        except Exception:
            skills = []
    skills_str = ", ".join(skills) if skills else "General Alignment"
    job_id = job.get("id", "")

    # Green for 90%+, Amber for 85%+
    color = 0x22C55E if score >= 90 else 0xF59E0B

    return {
        "content": f"🎯 **High-Fit Job Match ({score}%) Found!**",
        "embeds": [
            {
                "title": f"{title} @ {company}",
                "url": url,
                "color": color,
                "fields": [
                    {"name": "Match Score", "value": f"**{score}%**", "inline": True},
                    {"name": "Location", "value": loc, "inline": True},
                    {"name": "ATS Platform", "value": ats, "inline": True},
                    {"name": "Matched Skills", "value": skills_str, "inline": False},
                    {"name": "Quick Action", "value": f"Reply `/apply {job_id}` to trigger local agent runner", "inline": False},
                ],
                "footer": {"text": "Job Automation Agent • Autonomous ATS Discovery"}
            }
        ]
    }


def notify_discovered_job(job: dict) -> dict[str, bool]:
    """Send notification across all configured channels if match_score exceeds threshold."""
    cfg = get_notification_config()
    score = job.get("match_score", 0)
    if score < cfg["min_score"]:
        return {}

    results = {}

    # Telegram
    if cfg["telegram_bot_token"] and cfg["telegram_chat_id"]:
        tg_url = f"https://api.telegram.org/bot{cfg['telegram_bot_token']}/sendMessage"
        tg_payload = {
            "chat_id": cfg["telegram_chat_id"],
            "text": format_discovered_job_telegram(job),
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        }
        results["telegram"] = send_http_json(tg_url, tg_payload)

    # Discord
    if cfg["discord_webhook_url"]:
        discord_payload = format_discovered_job_discord(job)
        results["discord"] = send_http_json(cfg["discord_webhook_url"], discord_payload)

    # Slack
    if cfg["slack_webhook_url"]:
        slack_payload = {
            "text": f"🎯 High-Fit Job Match ({score}%): *{job.get('job_title')}* at *{job.get('company_name')}*\nLink: {job.get('url')}"
        }
        results["slack"] = send_http_json(cfg["slack_webhook_url"], slack_payload)

    # Generic Webhook
    if cfg["generic_webhook_url"]:
        gen_payload = {
            "event": "discovered_job",
            "job": job,
        }
        results["generic"] = send_http_json(cfg["generic_webhook_url"], gen_payload)

    return results


def handle_remote_command(command_str: str) -> dict[str, Any]:
    """
    Parse and execute a remote command string, such as:
      /apply <id>
      /dismiss <id>
      /status
      /scan
    """
    cmd = (command_str or "").strip()
    if not cmd:
        return {"status": "error", "message": "Empty command"}

    parts = cmd.split()
    verb = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else None

    if verb == "/apply":
        if not arg or not arg.isdigit():
            return {"status": "error", "message": "Usage: /apply <job_id>"}
        job_id = int(arg)
        from job_discovery import JobDiscoveryEngine
        engine = JobDiscoveryEngine()
        queued = engine.queue_for_application(job_id)
        if queued:
            return {
                "status": "ok",
                "message": f"Successfully queued job #{job_id} ({queued.get('job_title')} at {queued.get('company_name')}) for application.",
                "job": queued,
            }
        else:
            return {"status": "error", "message": f"Discovered job #{job_id} not found."}

    elif verb == "/dismiss":
        if not arg or not arg.isdigit():
            return {"status": "error", "message": "Usage: /dismiss <job_id>"}
        job_id = int(arg)
        from job_discovery import JobDiscoveryEngine
        engine = JobDiscoveryEngine()
        success = engine.dismiss_job(job_id)
        if success:
            return {"status": "ok", "message": f"Dismissed job #{job_id}."}
        else:
            return {"status": "error", "message": f"Discovered job #{job_id} not found."}

    elif verb == "/scan":
        from job_discovery import JobDiscoveryEngine
        engine = JobDiscoveryEngine()
        stats = engine.scan_all()
        return {
            "status": "ok",
            "message": f"Scan completed: evaluated {stats.get('total_evaluated', 0)} jobs across {stats.get('companies_scanned', 0)} companies; found {stats.get('new_matches_saved', 0)} matches.",
            "stats": stats,
        }

    elif verb == "/status":
        from tracking import open_tracker
        tracker = open_tracker()
        apps = tracker.list_all()
        from job_discovery import JobDiscoveryEngine
        discovered = JobDiscoveryEngine().get_discovered_jobs(min_score=80, limit=5)

        needs_review = [a for a in apps if a.status in ("needs_user_review", "ready_to_submit")]
        submitted = [a for a in apps if a.status == "submitted"]

        summary = (
            f"📊 **Job Automation Agent Status**\n"
            f"• Total Tracked Applications: {len(apps)}\n"
            f"• Submitted: {len(submitted)}\n"
            f"• Needs Review / Attention: {len(needs_review)}\n"
            f"• High-Fit Discovered Opportunities: {len(discovered)}"
        )
        return {"status": "ok", "message": summary, "total": len(apps), "needs_review": len(needs_review)}

    else:
        return {"status": "error", "message": f"Unknown command: {verb}. Available: /apply <id>, /dismiss <id>, /status, /scan"}

