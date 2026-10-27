"""The dashboard's live cockpit API: what the running agent is doing, and the controls (resume, stop, raise the browser,
resolve a hand-off, correct an answer).

Moved out of web_ui.py (code-quality audit 26 October 2026) as a blueprint, the way web_setup.py and web_progress.py are.
The run state and helpers stay in web_ui and are read through `web_ui.` at call time, never copied here, so a test that
patches `web_ui.waiting_runs` or `web_ui.get_tracker` still steers these routes.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from flask import Blueprint, request

cockpit_pages = Blueprint("cockpit_pages", __name__)
logger = logging.getLogger(__name__)


# What a run's own progress log says, in words. A run is a separate process, so the dashboard's in-memory event buffer never
# holds its events; the run writes one JSON line per step to logs/run_*.jsonl (event names only, never the values), and that
# is what the live card reads. (event name) -> (what it says, stage it belongs to)
_RUN_LOG_WORDS = {
    "APPLY_ENTRY_NAVIGATED": ("Opened the application form", None),
    "REUSED_RESUME": ("Reused the resume already written for this job", None),
    "AUTOFILL": ("Filled in the form from your resume", "form_filling"),
    "FIELD_ACTION": ("Filled in a field", "form_filling"),
    "ATTACHED": ("Attached your resume", "form_filling"),
    "AI_ANSWERED_QUESTION": ("Had the AI answer a question", "form_filling"),
    "READ": ("Read the page", None),
    "NEXT": ("Moved on to the next step", "form_filling"),
    "CAPTCHA": ("A CAPTCHA appeared", "form_filling"),
    "AUTO_SUBMIT": ("Checked the form before submitting", "submit"),
    "FORENSIC_CAPTURE": ("Saved the page for review", None),
}
_RUN_LOG_QUIET = {"RUNTIME", "STARTED", "SOURCE_ID", "BRAIN", "ANSWER_BANK"}


def _events_from_run_log(live_url: str, limit: int = 40) -> tuple[list[dict], Optional[float]]:
    """(newest-first activity lines, seconds since the run last wrote anything) for the run on `live_url`."""
    import web_ui
    with web_ui._RUNS_LOCK:
        started = (web_ui._RUNS.get(live_url) or {}).get("started")
    since = started.timestamp() if isinstance(started, datetime) else (float(started) if isinstance(started, (int, float)) else 0.0)
    try:
        candidates = [f for f in (web_ui.BASE_DIR / "logs").glob("run_*.jsonl") if f.stat().st_mtime >= since - 10]
        newest = max(candidates, key=lambda f: f.stat().st_mtime)
        with open(newest, "rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 65536))
            lines = handle.read().decode("utf-8", "replace").splitlines()
    except (OSError, ValueError):
        return [], None
    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
            when = datetime.fromisoformat(str(entry["time"]))
        except (ValueError, KeyError, TypeError):
            continue
        entries.append((when, str(entry.get("event") or "")))
    if not entries:
        return [], None
    seen_for = max(0.0, (datetime.now(timezone.utc) - entries[-1][0]).total_seconds())
    events = []
    for when, name in reversed(entries):
        if name in _RUN_LOG_QUIET:
            continue
        words, stage = _RUN_LOG_WORDS.get(name, (name.replace("_", " ").capitalize(), None))
        events.append({"timestamp": when.isoformat(), "event": name, "display_message": words, "stage": stage or "",
                       "is_verified": False})
        if len(events) >= limit:
            break
    return events, seen_for


@cockpit_pages.get("/api/cockpit-state")
def api_cockpit_state():
    import web_ui
    live_url = web_ui._running_url()
    tracker = web_ui.get_tracker()
    active_handoff = web_ui.handoff.get_active_handoff()
    is_waiting = bool(live_url and web_ui.waiting_runs(web_ui._RUNS_FILE.parent))
    
    app_record = None
    app_key = ""
    if live_url:
        if hasattr(tracker, "find_by_url"):
            app_record = tracker.find_by_url(live_url)
        if not app_record and hasattr(tracker, "list_all"):
            for r in tracker.list_all():
                if r.url == live_url:
                    app_record = r
                    break
    if not app_record and hasattr(tracker, "list_all"):
        all_apps = tracker.list_all()
        if all_apps:
            app_record = all_apps[0]

    if app_record:
        app_key = getattr(app_record, "dedup_key", "") or getattr(app_record, "url", "")

    recent_events = []
    if web_ui.RuntimeEvent is not None:
        recent_events = [e.as_dict() for e in reversed(web_ui.RuntimeEvent.get_recent(limit=40))]
    last_activity_seconds = None
    if live_url and not recent_events:
        recent_events, last_activity_seconds = _events_from_run_log(live_url)

    stage = "Discovery"
    stage_step = 1
    loop_status = "normal"
    last_verified = "None recorded"
    current_action = "Agent idle"

    for e in recent_events:
        if e.get("is_verified"):
            last_verified = e.get("display_message") or e.get("event")
            break

    for e in recent_events[:6]:
        ev_name = e.get("event", "")
        if "LOOP" in ev_name or "CIRCUIT_BREAKER" in ev_name:
            loop_status = "stalled"
            break
        elif "STALL" in ev_name or "WARNING" in ev_name:
            loop_status = "warning"

    if live_url:
        current_action = "Agent processing form"
        if recent_events:
            current_action = recent_events[0].get("display_message") or recent_events[0].get("event")

        rec_status = getattr(app_record, "status", "") if app_record else ""
        if rec_status == "submitted":
            stage = "Submitted"
            stage_step = 5
        elif rec_status == "ready_to_submit":
            stage = "Review"
            stage_step = 4
        elif active_handoff:
            stage = f"Challenge ({active_handoff.get('category')})"
            stage_step = 3
        elif rec_status == "form_filled":
            stage = "Form Fill"
            stage_step = 3
        else:
            ev_stage = recent_events[0].get("stage") if recent_events else ""
            if ev_stage == "account":
                stage = "Auth"
                stage_step = 2
            elif ev_stage == "form_filling":
                stage = "Form Fill"
                stage_step = 3
            elif ev_stage == "submit":
                stage = "Review"
                stage_step = 4
            else:
                stage = "Discovery"
                stage_step = 1
    elif active_handoff:
        current_action = f"Waiting for human intervention: {active_handoff.get('reason')}"
        stage = f"Challenge ({active_handoff.get('category')})"
        stage_step = 3

    if is_waiting:
        stage = "Waiting for you"
        stage_step = 4 if getattr(app_record, "status", "") == "ready_to_submit" else 3
        current_action = (getattr(app_record, "notes", "") or
                          "Waiting for your action in the browser or Continue on the dashboard")

    elapsed_seconds = 0
    if live_url:
        with web_ui._RUNS_LOCK:
            run_data = web_ui._RUNS.get(live_url, {})
            started = run_data.get("started")
            if started:
                if isinstance(started, datetime):
                    tz = started.tzinfo or timezone.utc
                    started_utc = started if started.tzinfo else started.replace(tzinfo=timezone.utc)
                    elapsed_seconds = int((datetime.now(timezone.utc) - started_utc).total_seconds())
                elif isinstance(started, (int, float)):
                    elapsed_seconds = int(time.time() - started)

    recent_answers = web_ui.get_answers_for_application(app_key) if app_key else []

    portal_name = "Direct Portal"
    if app_record and getattr(app_record, "url", ""):
        from urllib.parse import urlparse
        netloc = urlparse(app_record.url).netloc
        portal_name = netloc.replace("www.", "").split(".")[0].capitalize()

    return {
        "is_running": bool(live_url),
        "is_waiting": is_waiting,
        "running_url": live_url,
        "job_title": getattr(app_record, "title", "") if app_record else "No active job",
        "employer": getattr(app_record, "company", "") if app_record else "No active employer",
        "portal": portal_name,
        "stage": stage,
        "stage_step": stage_step,
        "current_action": current_action,
        "last_verified_action": last_verified,
        "loop_status": loop_status,
        "elapsed_seconds": max(0, elapsed_seconds),
        "active_handoff": active_handoff,
        "recent_events": recent_events,
        "recent_answers": recent_answers,
        "last_activity_seconds": last_activity_seconds,
    }


@cockpit_pages.post("/api/cockpit/resume")
def api_cockpit_resume():
    import web_ui
    web_ui._continue_waiting_run()
    return {"ok": True, "status": "resumed"}


@cockpit_pages.post("/api/cockpit/stop")
def api_cockpit_stop():
    import web_ui
    stopped = web_ui._stop_run(web_ui._running_url() or "")
    return {"ok": True, "status": "stopped" if stopped else "not_running"}


def raise_browser_window() -> bool:
    """Brings any visible agent Chrome / browser window to the foreground on Windows."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        target_hwnds = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit(hwnd, _):
            length = user32.GetWindowTextLengthW(hwnd)
            if length and (user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd)):
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value.lower()
                if any(w in title for w in ("chrome", "chromium", "job", "apply", "greenhouse", "workday", "lever")):
                    target_hwnds.append(hwnd)
            return True

        user32.EnumWindows(visit, 0)
        for hwnd in target_hwnds:
            user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0043)
            user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0043)
            user32.SetForegroundWindow(hwnd)
        return bool(target_hwnds)
    except Exception as exc:
        logger.debug("Failed to raise browser window: %s", exc)
        return False


@cockpit_pages.post("/api/cockpit/raise-browser")
def api_cockpit_raise_browser():
    import web_ui
    try:
        raised = web_ui.raise_browser_window()
        return {"ok": True, "raised": raised}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@cockpit_pages.post("/api/handoff/resolve")
def api_handoff_resolve():
    import web_ui
    data = request.form if request.form else (request.get_json(silent=True) or {})
    handoff_id = data.get("handoff_id")
    action = data.get("action") or "check_and_resume"

    active = web_ui.handoff.get_active_handoff()
    if not active:
        return {"ok": True, "message": "No active handoff"}

    if action == "more_time":
        return {"ok": True, "message": "Timeout extended"}

    if action == "cancel":
        result = api_cockpit_stop()
        web_ui.handoff.clear_active_handoff()
        return result

    ok, msg = web_ui.handoff.resolve_active_handoff()
    if ok:
        web_ui._continue_waiting_run()
        return {"ok": True, "message": msg}
    return {"ok": False, "error": msg}


@cockpit_pages.post("/api/answers/update")
def api_answers_update():
    import web_ui
    data = request.form if request.form else (request.get_json(silent=True) or {})
    answer_id = data.get("answer_id")
    new_value = data.get("new_value")
    if not answer_id or new_value is None:
        return {"ok": False, "error": "Missing answer_id or new_value"}, 400
    try:
        aid = int(answer_id)
        success = web_ui.update_answer_in_ledger(aid, str(new_value))
        return {"ok": success}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 500


@cockpit_pages.get("/api/application/<int:app_id>/answers")
def api_application_answers(app_id: int):
    import web_ui
    tracker = web_ui.get_tracker()
    record = tracker.get(app_id) if hasattr(tracker, "get") else None
    if not record:
        return {"ok": False, "error": "Application not found"}, 404
    app_key = getattr(record, "dedup_key", "") or getattr(record, "url", "")
    answers = web_ui.get_answers_for_application(app_key)
    return {"ok": True, "answers": answers}
