"""
Local web UI for the job application assistant.

    python web_ui.py      then open http://127.0.0.1:5000

Paste a job URL to start an application, and see everything already tracked:
status, the exact resume and cover letter that were sent (served from the
database, not the filesystem), and what was answered to each employer's
questions.

Runs on loopback ONLY and is never exposed to the network: it reads a
database holding a real person's resume, address and phone number, and it can
launch a browser that is signed into their employer ATS accounts.
"""

from __future__ import annotations

import io
import logging
import json
from typing import Optional
import re
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, abort, redirect, render_template_string, request, send_file, url_for

import diagnostics
diagnostics.install_log_privacy()
import application_status
import ui_shell
import visible_desktop
import run_outcomes
from config import get_app_config
from tracking import chosen, open_tracker as get_tracker
import sqlite3
import handoff
try:
    from runtime_events import RuntimeEvent, EventName, ReasonCode, AnswerSource
except ImportError:
    try:
        import sys
        _pkg_root = str(Path(__file__).resolve().parent)
        if _pkg_root not in sys.path:
            sys.path.insert(0, _pkg_root)
        from runtime_events import RuntimeEvent, EventName, ReasonCode, AnswerSource
    except Exception:
        RuntimeEvent = None

BASE_DIR = Path(__file__).resolve().parent
app = Flask(__name__)
logger = logging.getLogger(__name__)
# the page script and styles are small and change with the code: never let a browser keep a stale copy
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

from launch_dashboard import source_id
RUNTIME_INFO = {'directory': str(BASE_DIR), 'source_id': source_id(BASE_DIR),
                'interpreter': sys.executable}

import web_guard  # noqa: E402

web_guard.install(app)
ui_shell.install(app)

from web_setup import setup_pages  # noqa: E402  (the pages import web_ui back, lazily)

app.register_blueprint(setup_pages)
from web_progress import progress_pages  # noqa: E402
app.register_blueprint(progress_pages)
import answer_ledger  # noqa: E402
from answer_ledger import get_answers_for_application, init_answer_ledger, record_answer_ledger  # noqa: E402,F401
from web_templates import BASE_CSS, DETAIL_HTML, INDEX_HTML, SETTINGS_HTML  # noqa: E402,F401  (the pages' markup)


@app.get('/runtime')
def runtime_info():
    """The loaded source identity, so startup can refuse a stale dashboard."""
    return RUNTIME_INFO

def runtime_matches_source():
    return RUNTIME_INFO['source_id'] == source_id(BASE_DIR)

# Applications launched from this UI, so their progress can be shown. Keyed by
# the URL that started them, and written to disk so restarting this server --
# or letting it reload after a code change -- doesn't lose track of a run that
# is still going in its own process.
_RUNS: dict[str, dict] = {}
_RUNS_LOCK = threading.Lock()
_RUNS_FILE = BASE_DIR / "data" / "_runs.json"   # its folder also holds the runs' waiting and signal files


def _save_runs() -> None:
    try:
        _RUNS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _RUNS_FILE.write_text(json.dumps({
            url: {"state": r["state"], "log": r["log"], "pid": r.get("pid"),
                  "started": r["started"].isoformat() if hasattr(r["started"], "isoformat") else r["started"]}
            for url, r in _RUNS.items()
        }, indent=2), encoding="utf-8")
    except Exception as _swallowed:
        logger.debug("_save_runs: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)


def _process_alive(pid) -> bool:
    if not pid:
        return False
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
            return str(pid) in out
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _load_runs() -> None:
    """Restores what was running before this server started."""
    try:
        if not _RUNS_FILE.is_file():
            return
        for url, r in json.loads(_RUNS_FILE.read_text(encoding="utf-8")).items():
            if not url.startswith("http"):
                continue  # not a posting: a row an older copy of this file left
            state = r.get("state", "")
            if state == "running" and not _process_alive(r.get("pid")):
                state = "ended while the dashboard was restarting"
            _RUNS[url] = {"state": state, "log": r.get("log", ""), "pid": r.get("pid"),
                          "started": datetime.fromisoformat(r["started"]) if r.get("started") else None,
                          "proc": None, "stopping": False}
    except Exception as _swallowed:
        logger.debug("_load_runs: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)


_load_runs()


# ----------------------------------------------------------------------
# Launching an application
# ----------------------------------------------------------------------
def run_environment() -> dict[str, str]:
    """The environment a run starts with: this server's, with .env as it is now on top.

    The server read .env once, when it started, and every run inherited that copy; the run's own load_dotenv
    does not override what it inherits. So a choice saved on Settings (which AI answers, which writes the
    resume) went unused until the dashboard was restarted."""
    from dotenv import dotenv_values
    env = dict(os.environ)
    path = BASE_DIR / ".env"
    if path.is_file():
        env.update({k: v for k, v in dotenv_values(path).items() if v is not None})
    return env


def _run_apply(url: str, open_url: str = "") -> None:
    """Runs apply.py for one URL, capturing output for the UI to display.

    open_url: the page a previous run reached, reopened instead of the posting.
    """
    log_path = BASE_DIR / "logs" / f"ui_run_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with _RUNS_LOCK:
        _RUNS[url] = {"state": "running", "log": str(log_path), "started": datetime.now(timezone.utc),
                      "proc": None, "pid": None, "stopping": False}
        _save_runs()

    try:
        if not runtime_matches_source():
            raise RuntimeError('Dashboard code changed; restart the dashboard before starting another application.')
        private_log = diagnostics.PrivateRunLog(log_path, RUNTIME_INFO["source_id"])
        # Popen rather than run() so /stop can reach the process. On POSIX
        # it gets its own session so the whole group can be signalled.
        command = [sys.executable, "apply.py", url]
        if open_url:
            command += ["--open-url", open_url]
        proc = subprocess.Popen(
            command,
            cwd=str(BASE_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            start_new_session=(os.name != "nt"), env=run_environment(),
        )
        with _RUNS_LOCK:
            _RUNS[url]["proc"] = proc
            _RUNS[url]["pid"] = proc.pid
            _save_runs()
        # Drain bounded chunks, not arbitrary unbounded readline output.
        if proc.stdout is not None:
            while True:
                chunk = proc.stdout.readline(2048)
                if not chunk:
                    break
                private_log.append(chunk)
        proc.wait()
        state = run_outcomes.display_state(proc.returncode)
    except Exception as exc:
        state = "failed (runtime error; private details omitted)"

    if diagnostics.retention_days() == 0:
        diagnostics.cleanup_expired_diagnostics(BASE_DIR, days=0)
    with _RUNS_LOCK:
        if _RUNS[url]["stopping"]:
            state = "stopped by you"
        _RUNS[url]["state"] = state
        _RUNS[url]["proc"] = None
        _RUNS[url]["pid"] = None
        _save_runs()


def _kill_pid_tree(pid: int) -> None:
    """Ends a run by process id: the dashboard may have restarted since it
    started, so the Popen object is gone but the run is still going."""
    if not pid:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
    else:
        try:
            os.killpg(int(pid), signal.SIGTERM)
        except Exception as _swallowed:
            logger.debug("_kill_pid_tree: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)


def _kill_tree(proc: subprocess.Popen) -> None:
    """Ends apply.py and everything under it: apply_flow.py, the Playwright
    driver, and the Chromium window. Killing only the top process would leave
    the browser holding the profile, and the next run couldn't start."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, check=False)
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError as _swallowed:
            logger.debug("_kill_tree: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)


@app.post("/api/discovered/scan")
def scan_discovered():
    from job_discovery import JobDiscoveryEngine
    engine = JobDiscoveryEngine()
    engine.scan_all(min_match_threshold=60)
    return redirect(url_for("index"))


@app.post("/api/discovered/<int:job_id>/apply")
def apply_discovered(job_id: int):
    from job_discovery import JobDiscoveryEngine
    engine = JobDiscoveryEngine()
    job = engine.queue_for_application(job_id)
    if job and job.get("already_submitted"):
        return redirect(url_for("index", error=f"You already submitted an application for {job.get('job_title')} at {job.get('company_name')}."))
    if job and job.get("url"):
        url = job["url"].strip()
        busy = _running_url()
        if busy:
            return redirect(url_for("index", error=f"{busy[:80]} is still running. Stop it before starting another."))
        threading.Thread(target=_run_apply, args=(url,), daemon=True).start()
    return redirect(url_for("index"))


@app.post("/api/discovered/<int:job_id>/dismiss")
def dismiss_discovered(job_id: int):
    from job_discovery import JobDiscoveryEngine
    engine = JobDiscoveryEngine()
    engine.dismiss_job(job_id)
    return redirect(url_for("index"))


@app.post("/api/webhook/command")
def remote_webhook_command():
    """Execute a remote command string (/apply <id>, /dismiss <id>, /status, /scan)."""
    import notifications
    cmd = request.form.get("command") or (request.get_json(silent=True) or {}).get("command") or ""
    res = notifications.handle_remote_command(cmd)
    if res.get("status") == "ok" and cmd.strip().startswith("/apply"):
        job = res.get("job")
        if job and job.get("url"):
            url = job["url"].strip()
            busy = _running_url()
            if not busy:
                threading.Thread(target=_run_apply, args=(url,), daemon=True).start()
    return res, 200


@app.post("/api/webhook/telegram")
def telegram_webhook():
    """Webhook receiver for Telegram bot messages."""
    import notifications
    data = request.get_json(silent=True) or {}
    msg = data.get("message") or {}
    text = msg.get("text", "")
    chat_id = (msg.get("chat") or {}).get("id")

    res = notifications.handle_remote_command(text)
    if res.get("status") == "ok" and text.strip().startswith("/apply"):
        job = res.get("job")
        if job and job.get("url"):
            url = job["url"].strip()
            busy = _running_url()
            if not busy:
                threading.Thread(target=_run_apply, args=(url,), daemon=True).start()

    reply_text = res.get("message", "Done.")
    if chat_id:
        cfg = notifications.get_notification_config()
        if cfg["telegram_bot_token"]:
            tg_url = f"https://api.telegram.org/bot{cfg['telegram_bot_token']}/sendMessage"
            notifications.send_http_json(tg_url, {"chat_id": chat_id, "text": reply_text})
    return res, 200


@app.post("/apply")
def start_apply():
    if not runtime_matches_source():
        return redirect(url_for('index', error='Dashboard code changed. Restart the dashboard to use the corrected version.'))
    url = (request.form.get("url") or "").strip()
    if not url:
        return redirect(url_for("index"))
    # A browser profile can only be held by one process, so refuse to start a
    # second run while one is live rather than failing confusingly later.
    busy = _running_url()
    if busy:
        return redirect(url_for("index", error=(
            f"{busy[:80]} is still running. Stop it from its row, or wait for it to finish.")))
    threading.Thread(target=_run_apply, args=(url,), daemon=True).start()
    return redirect(url_for("index"))


@app.post("/stop")
def stop_apply():
    """Stops a running application and closes its browser. Nothing is
    submitted, and the application's tracked status is left as it was (NOT
    marked skipped), so it can simply be started again."""
    url = (request.form.get("url") or "").strip()
    if not _stop_run(url):
        return redirect(url_for("index", error="Nothing was running."))
    return redirect(url_for("index"))


@app.post("/restart")
def restart_apply():
    """Reload: ends the run that is going -- nothing is submitted -- and starts the same application again from its
    posting, with the agent's latest code. A fresh start, where Resume carries on from the page it is on."""
    url = _running_url()
    if not url:
        return redirect(url_for("index", error="Nothing is running to reload."))
    _stop_run(url)

    def start_again() -> None:
        time.sleep(3)                      # the closed browser lets go of its profile before the next one opens it
        _run_apply(url)

    threading.Thread(target=start_again, daemon=True).start()
    return redirect(url_for("index"))


def _stop_run(url: str) -> bool:
    """Ends the run for this posting (or whatever application process is going) and its browser. True when
    something was running."""
    with _RUNS_LOCK:
        run = _RUNS.get(url)
        if run:
            run["stopping"] = True
        proc, pid = (run or {}).get("proc"), (run or {}).get("pid")
    if proc is not None:
        _kill_tree(proc)
    elif pid:
        _kill_pid_tree(pid)
    # Whatever this server thinks it knows, the run is a process on this
    # machine: stop it. Its bookkeeping has been lost before -- to a reload, or
    # to a state file written by an older copy -- and the button then refused
    # to work on a run the user could see in their browser.
    ended = _end_any_run()
    with _RUNS_LOCK:
        if run:
            run["state"] = "stopped by you"
            run["proc"] = run["pid"] = None
            _save_runs()
    if proc is None and not pid and not ended:
        return False
    clear_waiting_files(_RUNS_FILE.parent)
    return True


def clear_waiting_files(data_dir: Path) -> None:
    """Remove what a run leaves while it waits: its answer file and its waiting note.

    A run removes its own note when the wait ends; a run that is killed cannot, and the dashboard went on
    showing it as "Paused in the browser" with a Continue nobody would read (Aristocrat, 29 September)."""
    for pattern in ("_signal_*.txt", "_waiting_*.txt"):
        for leftover in Path(data_dir).glob(pattern):
            leftover.unlink(missing_ok=True)


def _end_any_run() -> int:
    """Ends the application process and its browser, by what they are rather
    than by what this server recorded about them."""
    if os.name != "nt":
        return 0
    try:
        listing = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=45).stdout
        rows = json.loads(listing or "[]")
    except Exception:
        return 0
    marks = ("apply.py", "apply_flow.py", str(BASE_DIR / "browser_profile").lower())
    pids = [str(r.get("ProcessId")) for r in rows
            if any(m in (r.get("CommandLine") or "").lower() for m in marks)]
    for pid in pids:
        subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True, check=False)
    clear_waiting_files(_RUNS_FILE.parent)
    return len(pids)


def _save_env_values(values: dict[str, str]) -> None:
    """Update selected local .env keys without rewriting unrelated settings."""
    if any("\n" in value or "\r" in value for value in values.values()):
        raise ValueError("credential values cannot contain line breaks")
    path = BASE_DIR / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    remaining = dict(values)
    output = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
        if key in remaining:
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line)
    if output and output[-1].strip():
        output.append("")
    output.extend(f"{key}={value}" for key, value in remaining.items())
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


@app.route("/settings", methods=["GET", "POST"])
def settings():
    env_path = BASE_DIR / ".env"
    if request.method == "POST":
        values = {"ATS_EMAIL": (request.form.get("ats_email") or "").strip()}
        # API keys: only write when the field is non-empty (blank = keep existing)
        for field_name, env_key in [
            ("anthropic_api_key", "ANTHROPIC_API_KEY"),
            ("gemini_api_key",    "GEMINI_API_KEY"),
            ("openai_api_key",    "OPENAI_API_KEY"),
        ]:
            val = (request.form.get(field_name) or "").strip()
            if val:
                values[env_key] = val
        password = request.form.get("ats_password") or ""
        if password:
            values["ATS_PASSWORD"] = password
        try:
            _save_env_values(values)
        except (OSError, ValueError) as exc:
            return render_template_string(SETTINGS_HTML, error=f"Could not save settings: {exc}",
                                          saved=False, ats_email=values["ATS_EMAIL"],
                                          has_claude=False, has_gemini=False, has_openai=False,
                                          has_password=bool(password))
        return redirect(url_for("settings", saved="1"))
    from dotenv import dotenv_values
    current = dotenv_values(env_path) if env_path.is_file() else {}
    return render_template_string(
        SETTINGS_HTML,
        error=request.args.get("error"), saved=request.args.get("saved") == "1",
        ats_email=current.get("ATS_EMAIL") or "",
        has_claude=bool(current.get("ANTHROPIC_API_KEY")),
        has_gemini=bool(current.get("GEMINI_API_KEY")),
        has_openai=bool(current.get("OPENAI_API_KEY")),
        has_password=bool(current.get("ATS_PASSWORD")),
        ai=_ai_settings(current, refresh=request.args.get("refresh") == "1"),
    )


AI_PROVIDERS = ("gemini", "claude", "openai")
AI_TIERS = ("page", "quick")
AI_MAX_ROWS = 60      # the most models one list can hold
_MODEL_NAME = re.compile(r"[A-Za-z0-9._:-]{0,120}")


def _ai_settings(current: dict, refresh: bool = False) -> dict:
    """What the two AI choices on Settings show: the models each key can use, the owner's picks as .env has
    them now (not as this server read it at start), and how each model is doing today."""
    import ai_choice
    import ai_models
    import model_ladder
    from types import SimpleNamespace
    base = get_app_config()
    view = SimpleNamespace(**{**vars(base), **{
        "anthropic_api_key": current.get("ANTHROPIC_API_KEY") or "",
        "gemini_api_key": current.get("GEMINI_API_KEY") or "",
        "openai_api_key": current.get("OPENAI_API_KEY") or ""}})
    try:
        offered = ai_models.catalogue(view, refresh=refresh)
    except Exception as exc:
        offered = {p: {"models": [], "error": str(exc)[:120], "has_key": False} for p in AI_PROVIDERS}
    ladders = {}
    for provider in AI_PROVIDERS:
        for tier in AI_TIERS:
            name = f"{provider.upper()}_{tier.upper()}_MODELS"
            saved = current.get(name)
            if saved is None:
                chosen = model_ladder.ladder_for(base, provider, tier)
            else:
                chosen = [m.strip() for m in saved.split(",") if m.strip()]
            # The chosen models first, in their order and ticked; then the rest the key offers, unticked.
            rest = [m for m in offered[provider]["models"] if m not in chosen]
            ladders[f"{provider}_{tier}"] = ([{"model": m, "on": True} for m in chosen]
                                             + [{"model": m, "on": False} for m in rest])[:AI_MAX_ROWS]
    every = [m for p in AI_PROVIDERS for m in offered[p]["models"]] + [r["model"] for v in ladders.values() for r in v]
    state = {row["model"]: row for row in model_ladder.usage(list(dict.fromkeys(every)))}
    return {"providers": ai_choice.PROVIDERS, "offered": offered, "ladders": ladders, "state": state,
            "mode": (current.get("FORM_ANSWER_MODE") or base.form_answer_mode or "profile").lower(),
            "writer": current.get("RESUME_WRITER") or "", "writer_fallback": current.get("RESUME_WRITER_FALLBACK") or "",
            "tiers": AI_TIERS}


@app.post("/settings/ai")
def settings_ai():
    """Saves the owner's two choices: who answers the forms (and with which models), who writes the resume."""
    import ai_choice
    values = {}
    mode = (request.form.get("form_answer_mode") or "").strip().lower()
    if mode in ("profile", "gemini", "claude", "openai"):
        values["FORM_ANSWER_MODE"] = mode
    for provider in AI_PROVIDERS:
        for tier in AI_TIERS:
            # The page marks each list it sends, so a list with every model unticked is seen, and refused.
            if f"{provider}_{tier}_sent" not in request.form:
                continue
            fields = [f"{provider}_{tier}_{i}" for i in range(1, AI_MAX_ROWS + 1)]
            picked = [(request.form.get(f) or "").strip() for f in fields]
            if not all(_MODEL_NAME.fullmatch(m) for m in picked):
                return redirect(url_for("settings", error="A model name had characters a model name cannot have."))
            listed = ",".join(dict.fromkeys(m for m in picked if m))
            if not listed:
                return redirect(url_for("settings", error="Tick at least one model in each list, so a question "
                                                          "always has a model to go to.") + "#ai")
            values[f"{provider.upper()}_{tier.upper()}_MODELS"] = listed
    for field_name, env_key in (("resume_writer", "RESUME_WRITER"), ("resume_writer_fallback", "RESUME_WRITER_FALLBACK")):
        if field_name not in request.form:
            continue
        chosen = (request.form.get(field_name) or "").strip()
        if chosen and not (ai_choice.parse_writer(chosen)[0] and _MODEL_NAME.fullmatch(chosen)):
            return redirect(url_for("settings", error="That writer is not one the page offered."))
        values[env_key] = chosen
    try:
        _save_env_values(values)
    except (OSError, ValueError) as exc:
        return redirect(url_for("settings", error=f"Could not save: {exc}"))
    return redirect(url_for("settings", saved="1") + "#ai")


@app.post("/resume/<int:app_id>")
def resume_application(app_id: int):
    """Picks an application back up where it stopped.

    Used after a run ended with an error, was stopped, or was left needing
    attention. It starts the flow again for the same posting: the tailored
    resume and cover letter already stored are reused, answers already on the
    employer's form are left alone, and a job that is already submitted is
    refused as a duplicate.
    """
    if not runtime_matches_source():
        return redirect(url_for('index', error='Dashboard code changed. Restart the dashboard to use the corrected version.'))
    tracker = get_tracker()
    record = next((a for a in tracker.list_all() if a.id == app_id), None)
    if not record or not record.url:
        return redirect(url_for("index", error="That application has no URL to resume."))
    if record.status == "submitted":
        return redirect(url_for("index", error=f"{record.title} was already submitted."))
    if hasattr(tracker, "find_submitted"):
        already = tracker.find_submitted(url=record.url, company=record.company, title=record.title)
        if already and already.id != record.id:
            return redirect(url_for("index", error=f"{record.title} at {record.company} was already submitted."))
    busy = _running_url()
    if busy:
        return redirect(url_for("index", error=(
            f"{busy[:80]} is still running. Stop it from its row, or wait for it to finish.")))
    # The page the application actually reached, so the agent picks the form
    # back up rather than walking the posting from the start again.
    open_url = getattr(record, "last_page_url", "") or ""
    threading.Thread(target=_run_apply, args=(record.url, open_url), daemon=True).start()
    return redirect(url_for("application", app_id=app_id))


@app.post("/stop-application/<int:app_id>")
def stop_application(app_id: int):
    """Stops whatever run is working on this application.

    Offered per application, because that is how the user thinks about it --
    they stop an application, not "the run keyed by its posting URL", which is
    the bookkeeping that had gone stale when the button last refused to work.
    """
    tracker = get_tracker()
    record = next((a for a in tracker.list_all() if a.id == app_id), None)
    if not record:
        return redirect(url_for("index", error="That application is gone."))
    ended = _end_any_run()
    with _RUNS_LOCK:
        run = _RUNS.get(record.url or "")
        if run:
            run["state"] = "stopped by you"
            run["proc"] = run["pid"] = None
            _save_runs()
    if not ended:
        return redirect(url_for("index", error="Nothing was running for that application."))
    return redirect(url_for("index"))


# What the status menu accepts (after submitting: interviewing, rejected, offer, for the Progress page).
VALID_STATUSES = {"prepared", "form_filled", "ready_to_submit", "needs_user_review", "submitted", "skipped",
                  "job_not_found", "already_applied", "interviewing", "rejected", "offer"}


@app.post("/application/<int:app_id>/status")
def change_application_status(app_id: int):
    """Updates an application's status directly from the UI (e.g. submitted, needs_user_review, etc.)."""
    new_status = (request.form.get("status") or "").strip()
    if new_status not in VALID_STATUSES:
        return redirect(url_for("application", app_id=app_id, error="Invalid status."))
    tracker = get_tracker()
    record = next((a for a in tracker.list_all() if a.id == app_id), None)
    if not record:
        return redirect(url_for("index", error="That application is gone."))
    note = (request.form.get("notes") or "").strip() or f"Status updated to {new_status.replace('_', ' ')} via UI"
    tracker.update_status(record.dedup_key, new_status, notes=note, by_owner=True)
    if hasattr(tracker, "record_event"):
        tracker.record_event(record.dedup_key, "status_change", f"Status set to {new_status} by user in web UI")
    # An application the owner calls finished has nothing left for its run to do: the run is ended, not left
    # re-reading the page and reopening tabs (Aristocrat, 29 September).
    if new_status in application_status.DONE | {"skipped"} and record.url and _running_url() == record.url:
        _end_any_run()
        with _RUNS_LOCK:
            run = _RUNS.get(record.url)
            if run:
                run["state"] = f"ended -- you marked it {new_status}"
                run["proc"] = run["pid"] = None
                _save_runs()
    next_url = request.form.get("next") or url_for("application", app_id=app_id)
    return redirect(next_url)


@app.post("/delete/<int:app_id>")
def delete_application(app_id: int):
    """Removes an application and everything filed under it.

    The user asks for this explicitly, from the row itself; nothing deletes an
    application on its own. A run still working on it is stopped first, so its
    browser is not left holding a job that no longer exists.
    """
    tracker = get_tracker()
    record = next((a for a in tracker.list_all() if a.id == app_id), None)
    if not record:
        return redirect(url_for("index", error="That application is already gone."))
    with _RUNS_LOCK:
        running = _RUNS.get(record.url or "", {}).get("state") == "running"
    if running:
        _end_any_run()
        with _RUNS_LOCK:
            _RUNS[record.url]["state"] = "stopped -- the application was deleted"
            _save_runs()
    if not hasattr(tracker, "delete"):
        return redirect(url_for("index", error="This tracker cannot delete applications."))
    tracker.delete(record.dedup_key)
    return redirect(url_for("index"))


@app.post("/reload-agent")
def reload_agent():
    """Loads edited agent code into the run that is already going, without
    closing its browser or losing the part-filled form (the flow's
    'reload_code' signal)."""
    written = _signal_waiting_run("reload_code")
    if not written:
        # No flow is waiting on a signal right now; leave one for when it is.
        return redirect(url_for("index", error="No run is waiting -- nothing to reload."))
    return redirect(url_for("index"))


@app.post("/signal/<path:signal_file>")
def send_signal(signal_file: str):
    """Writes a review signal (submit / refresh / skip) for a running flow."""
    decision = (request.form.get("decision") or "").strip()
    target = (BASE_DIR / "data" / signal_file).resolve()
    # Never let a crafted name write outside data/.
    root = (BASE_DIR / "data").resolve()
    if target != root and root not in target.parents:
        abort(400)
    # "submit" is deliberately not accepted: the agent does not submit
    # applications, and this UI must not offer a button that looks like it does.
    if decision in {"refresh", "skip", "continue", "reload_code", "close"}:
        target.write_text(decision, encoding="utf-8")
    return redirect(url_for("index"))


@app.template_filter("local")
def local_time(value: datetime | None, fmt: str = "%d %b %I:%M %p") -> str:
    """Shows a stored timestamp (Postgres returns them in UTC) in this
    computer's own time zone -- the UI runs on the user's machine."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime(fmt)


# ----------------------------------------------------------------------
# Answer Transparency Ledger & Live Agent Cockpit Backend
# ----------------------------------------------------------------------
def update_answer_in_ledger(answer_id: int, new_value: str) -> bool:
    """A cockpit override of one answer, mirrored into the tracker's answer bank."""
    return answer_ledger.update_answer(answer_id, new_value, tracker_factory=lambda: get_tracker())


def _runtime_event_listener(ev):
    if not ev or not getattr(ev, "event", None):
        return
    ev_name = ev.event.value if hasattr(ev.event, "value") else str(ev.event)
    meta = ev.safe_metadata or {}
    app_key = ev.application_key or ""
    if not app_key:
        return

    if ev_name == "ANSWER_WRITTEN":
        q = meta.get("question")
        ans = meta.get("answer")
        src = meta.get("source", "PROFILE")
        if q and ans:
            record_answer_ledger(
                application_key=app_key,
                question_text=str(q),
                final_answer=str(ans),
                source=str(src),
                verification_result="ACCEPTED",
                status="COMMITTED",
            )
    elif ev_name == "ANSWER_CORRECTED":
        q = meta.get("question")
        prev = meta.get("previous_value", "")
        new = meta.get("new_value", "")
        src = meta.get("source", "PROFILE")
        reason = meta.get("reason", "")
        if q and new:
            record_answer_ledger(
                application_key=app_key,
                question_text=str(q),
                final_answer=str(new),
                source=str(src),
                verification_result="CORRECTED",
                previous_value=str(prev),
                correction_reason=str(reason),
                status="COMMITTED",
            )


if RuntimeEvent is not None:
    RuntimeEvent.register_listener(_runtime_event_listener)


# ----------------------------------------------------------------------
# Live Agent Cockpit API Routes
# ----------------------------------------------------------------------
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
    with _RUNS_LOCK:
        started = (_RUNS.get(live_url) or {}).get("started")
    since = started.timestamp() if isinstance(started, datetime) else (float(started) if isinstance(started, (int, float)) else 0.0)
    try:
        candidates = [f for f in (BASE_DIR / "logs").glob("run_*.jsonl") if f.stat().st_mtime >= since - 10]
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


@app.get("/api/cockpit-state")
def api_cockpit_state():
    live_url = _running_url()
    tracker = get_tracker()
    active_handoff = handoff.get_active_handoff()
    is_waiting = bool(live_url and waiting_runs(_RUNS_FILE.parent))
    
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
    if RuntimeEvent is not None:
        recent_events = [e.as_dict() for e in reversed(RuntimeEvent.get_recent(limit=40))]
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
        with _RUNS_LOCK:
            run_data = _RUNS.get(live_url, {})
            started = run_data.get("started")
            if started:
                if isinstance(started, datetime):
                    tz = started.tzinfo or timezone.utc
                    started_utc = started if started.tzinfo else started.replace(tzinfo=timezone.utc)
                    elapsed_seconds = int((datetime.now(timezone.utc) - started_utc).total_seconds())
                elif isinstance(started, (int, float)):
                    elapsed_seconds = int(time.time() - started)

    recent_answers = get_answers_for_application(app_key) if app_key else []

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


@app.post("/api/cockpit/resume")
def api_cockpit_resume():
    _continue_waiting_run()
    return {"ok": True, "status": "resumed"}


def _continue_waiting_run():
    return _signal_waiting_run("continue")


def _signal_waiting_run(decision):
    """Waiting notes exist before an answer file; resume creates that file."""
    if not _running_url():
        return 0
    written = 0
    for waiting in waiting_runs(_RUNS_FILE.parent):
        (_RUNS_FILE.parent / waiting["signal"]).write_text(decision, encoding="utf-8")
        written += 1
    return written


@app.post("/api/cockpit/stop")
def api_cockpit_stop():
    stopped = _stop_run(_running_url() or "")
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


@app.post("/api/cockpit/raise-browser")
def api_cockpit_raise_browser():
    try:
        raised = raise_browser_window()
        return {"ok": True, "raised": raised}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/handoff/resolve")
def api_handoff_resolve():
    data = request.form if request.form else (request.get_json(silent=True) or {})
    handoff_id = data.get("handoff_id")
    action = data.get("action") or "check_and_resume"

    active = handoff.get_active_handoff()
    if not active:
        return {"ok": True, "message": "No active handoff"}

    if action == "more_time":
        return {"ok": True, "message": "Timeout extended"}

    if action == "cancel":
        result = api_cockpit_stop()
        handoff.clear_active_handoff()
        return result

    ok, msg = handoff.resolve_active_handoff()
    if ok:
        _continue_waiting_run()
        return {"ok": True, "message": msg}
    return {"ok": False, "error": msg}


@app.post("/api/answers/update")
def api_answers_update():
    data = request.form if request.form else (request.get_json(silent=True) or {})
    answer_id = data.get("answer_id")
    new_value = data.get("new_value")
    if not answer_id or new_value is None:
        return {"ok": False, "error": "Missing answer_id or new_value"}, 400
    try:
        aid = int(answer_id)
        success = update_answer_in_ledger(aid, str(new_value))
        return {"ok": success}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 500


@app.get("/api/application/<int:app_id>/answers")
def api_application_answers(app_id: int):
    tracker = get_tracker()
    record = tracker.get(app_id) if hasattr(tracker, "get") else None
    if not record:
        return {"ok": False, "error": "Application not found"}, 404
    app_key = getattr(record, "dedup_key", "") or getattr(record, "url", "")
    answers = get_answers_for_application(app_key)
    return {"ok": True, "answers": answers}


@app.get("/health")
def health_check():
    import config as cfg_mod

    checks = {}

    # 1. Playwright Chromium
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            exec_path = p.chromium.executable_path
            checks["playwright"] = {
                "title": "Playwright Chromium",
                "ok": True,
                "message": f"Browser located: {Path(exec_path).name}",
            }
    except Exception as exc:
        checks["playwright"] = {
            "title": "Playwright Chromium",
            "ok": False,
            "message": f"Chromium not runnable: {str(exc).splitlines()[0][:100]}",
            "hint": "Run `python -m playwright install chromium`",
        }

    # 2. User profile
    try:
        prof = cfg_mod.get_user_profile()
        missing = [f for f in ("full_name", "email", "phone") if not getattr(prof, f, None)]
        if missing:
            checks["profile"] = {
                "title": "User Profile",
                "ok": False,
                "message": f"Missing required fields: {', '.join(missing)}",
                "hint": "Update your profile on the Profile tab.",
            }
        else:
            checks["profile"] = {
                "title": "User Profile",
                "ok": True,
                "message": f"Configured for {prof.full_name} ({prof.email})",
            }
    except Exception as exc:
        checks["profile"] = {
            "title": "User Profile",
            "ok": False,
            "message": f"Profile failed to load: {exc}",
        }

    # 3. Resume document
    try:
        cfg = cfg_mod.get_app_config()
        resume_p = getattr(cfg, "resume_path", None)
        if resume_p and Path(resume_p).is_file():
            checks["resume"] = {
                "title": "Resume Document",
                "ok": True,
                "message": f"Found resume: {Path(resume_p).name} ({Path(resume_p).stat().st_size // 1024} KB)",
            }
        else:
            checks["resume"] = {
                "title": "Resume Document",
                "ok": False,
                "message": "Resume file not found",
                "hint": "Provide a valid resume PDF in profile or settings.",
            }
    except Exception as exc:
        checks["resume"] = {
            "title": "Resume Document",
            "ok": False,
            "message": f"Resume check failed: {exc}",
        }

    # 4. AI Provider
    try:
        anthropic_k = os.getenv("ANTHROPIC_API_KEY")
        gemini_k = os.getenv("GEMINI_API_KEY")
        if anthropic_k or gemini_k:
            provider = "Gemini" if gemini_k else "Anthropic Claude"
            checks["ai_provider"] = {
                "title": "AI Model Engine",
                "ok": True,
                "message": f"API key configured ({provider})",
            }
        else:
            checks["ai_provider"] = {
                "title": "AI Model Engine",
                "ok": False,
                "message": "No AI API key found (GEMINI_API_KEY or ANTHROPIC_API_KEY)",
                "hint": "Configure your API key in .env or Settings.",
            }
    except Exception as exc:
        checks["ai_provider"] = {
            "title": "AI Model Engine",
            "ok": False,
            "message": f"AI provider check failed: {exc}",
        }

    # 5. Database
    try:
        tracker = get_tracker()
        checks["database"] = {
            "title": "Application Database",
            "ok": True,
            "message": f"Database connected ({chosen()})",
        }
    except Exception as exc:
        checks["database"] = {
            "title": "Application Database",
            "ok": False,
            "message": f"Database unavailable: {str(exc).splitlines()[0][:100]}",
        }

    # 6. Remote Worker & Offload Architecture
    all_workers = []
    local_fallback = False
    try:
        from remote_client import RemoteControlClient
        client = RemoteControlClient(BASE_DIR)
        remote_w = client.get_active_remote_worker()
        local_fallback = client.local_fallback_enabled
        all_workers = client.get_system_workers()

        if remote_w:
            metrics = remote_w.metrics or {}
            cpu = metrics.get("cpu_percent", "N/A")
            mem = metrics.get("memory_percent", "N/A")
            disk = metrics.get("disk_free_gb", "N/A")
            checks["remote_worker"] = {
                "title": "Oracle Runtime Worker",
                "ok": True,
                "message": f"Worker '{remote_w.worker_id}' is {remote_w.status.value} (CPU: {cpu}%, RAM: {mem}%, Free Disk: {disk} GB, SHA: {remote_w.git_sha[:8]})",
            }
        else:
            checks["remote_worker"] = {
                "title": "Oracle Runtime Worker",
                "ok": local_fallback,
                "message": (
                    "Local fallback active (ENABLE_LOCAL_FALLBACK=1)"
                    if local_fallback
                    else "ORACLE WORKER OFFLINE: Remote execution is required. Local execution disabled to protect Windows resources."
                ),
                "hint": (
                    "Deploy remote worker via `scripts/remote/bootstrap_worker.sh`."
                    if not local_fallback
                    else "Local laptop execution enabled. CPU/RAM usage will increase during applications."
                ),
            }
    except Exception as exc:
        checks["remote_worker"] = {
            "title": "Oracle Runtime Worker",
            "ok": False,
            "message": f"Worker manager error: {exc}",
        }

    all_ok = all(c["ok"] for c in checks.values())
    status_str = "ok" if all_ok else "warning"

    if request.accept_mimetypes.accept_html and not request.is_json:
        tmpl_path = BASE_DIR / "templates" / "health.html"
        body_html = tmpl_path.read_text(encoding="utf-8") if tmpl_path.is_file() else ""
        return render_template_string(
            ui_shell.page("System Health &middot; Job Agent", body_html),
            checks=checks,
            status=status_str,
            workers=all_workers,
            local_fallback=local_fallback,
        )
    return {"status": status_str, "checks": checks, "workers": all_workers, "local_fallback": local_fallback}


# ----------------------------------------------------------------------
# Views
# ----------------------------------------------------------------------
PAGE_SIZE = 10        # applications shown at first, and added by "Show 10 more"
MAX_SHOWN = 300       # as far as the list grows


@app.get("/")
def index():
    import profile_setup
    if profile_setup.needs_setup():
        # A new person has no profile, and every answer comes from it: set it up first.
        return redirect(url_for("setup_pages.setup"))
    tracker = get_tracker()
    apps = tracker.list_all()
    runs = _current_runs()
    # Only a live run can read a Continue; notes left by a run that died or was stopped are cleared.
    live = _running_url()
    if live:
        signals = waiting_runs(_RUNS_FILE.parent)
    else:
        clear_waiting_files(_RUNS_FILE.parent)
        signals = []
    # A run still working is not waiting for the owner, whatever its application's status says from an earlier
    # run: it was listed under "Needs you" while the agent was busy on its page (UKG, 30 September).
    working_url = live if (live and not signals) else ""
    try:
        show = int(request.args.get("show", PAGE_SIZE))
    except ValueError:
        show = PAGE_SIZE
    show = max(PAGE_SIZE, min(show, MAX_SHOWN))
    active_h = handoff.get_active_handoff()
    job_title = ""
    employer = ""
    stage = "Idle"
    if live:
        for a in apps:
            if a.url == live:
                job_title = a.title
                employer = a.company
                stage = "Waiting for you" if signals else "Working"
                break
    elif active_h:
        job_title = active_h.get("category", "")
        employer = active_h.get("employer", "")
        stage = "Handoff"

    discovered_jobs = []
    try:
        from job_discovery import JobDiscoveryEngine
        discovery_engine = JobDiscoveryEngine()
        discovered_jobs = discovery_engine.get_discovered_jobs(min_score=60, limit=12)
    except Exception:
        discovered_jobs = []

    return render_template_string(
        INDEX_HTML, apps=apps[:show], total=len(apps), shown=min(show, len(apps)),
        more=min(show + PAGE_SIZE, MAX_SHOWN), can_show_more=show < min(len(apps), MAX_SHOWN),
        groups=ui_shell.group_counts(apps), runs=latest_run(runs), signals=signals, error=request.args.get("error"),
        working_url=working_url, is_running=bool(live), job_title=job_title, employer=employer, stage=stage,
        discovered_jobs=discovered_jobs,
    )


def waiting_runs(data_dir: Path) -> list[dict]:
    """The runs waiting for the owner, each with the signal file its Continue writes and a readable label.

    A run marks itself waiting with _waiting_<name>.txt (browser_automation.waiting_note_for). The card used to
    list only signal files that already existed -- an answer already sent -- so a run that was waiting had no
    Continue button at all (Rackspace, 29 September)."""
    found = []
    for note in sorted(Path(data_dir).glob("_waiting_*.txt")):
        name = note.name.replace("_waiting_", "", 1)
        label = name[:-4].replace("_", " ")
        try:
            job = json.loads((Path(data_dir) / f"_job_{name[:-4]}.json").read_text(encoding="utf-8-sig"))
            label = f"{job.get('title', '')} at {job.get('company', '')}".strip() or label
        except (OSError, ValueError) as _swallowed:
            logger.debug("waiting_runs: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)
        found.append({"signal": f"_signal_{name}", "label": label})
    return found


def latest_run(runs: dict) -> dict:
    """The one run worth showing: the live one, else the most recent.

    The list grew with every application of the session and pushed everything
    else down the page; only the run in hand is of any use.
    """
    if not runs:
        return {}
    live = [(url, run) for url, run in runs.items() if run.get("state") == "running"]
    if live:
        return dict(live[-1:])
    return dict(list(runs.items())[-1:])


def _running_url() -> str:
    """The posting whose run is actually live, if any.

    A run only counts as running while its process is: the dashboard used to
    refuse a new application on the strength of its own bookkeeping, which
    outlived the run itself and left the button doing nothing.
    """
    with _RUNS_LOCK:
        for url, run in _RUNS.items():
            if run.get("state") == "running" and (run.get("proc") is not None
                                                  or _process_alive(run.get("pid"))):
                return url
    return ""


def _current_runs() -> dict:
    """The runs as they actually are.

    A run ends in its own process, which cannot write back here if this server
    was reloaded in the meantime -- so a finished application went on being
    shown as "running" until the dashboard was restarted. Anything whose
    process is gone is settled here instead, using the tracker's status when it
    has one.
    """
    tracker = get_tracker()
    app_records = tracker.list_all()
    by_url = {r.url: r for r in app_records if r.url}
    with _RUNS_LOCK:
        for url, run in _RUNS.items():
            rec = by_url.get(url)
            if rec:
                run["last_page_url"] = getattr(rec, "last_page_url", None) or rec.url
                run["app_id"] = rec.id
                run["company"] = rec.company
                run["title"] = rec.title
                events = tracker.events(rec.dedup_key) if hasattr(tracker, "events") else []
                for ev in events:
                    if ev.get("screenshot_path") and Path(ev["screenshot_path"]).is_file():
                        run["screenshot"] = ev["screenshot_path"]
                        break
                if run["state"].startswith(("failed", "finished", "ended")) and rec.status in {"submitted", "skipped"}:
                    run["state"] = f"finished -- {rec.status}"
            if run["state"] != "running" or _process_alive(run.get("pid")):
                continue
            status = rec.status if rec else ""
            run["state"] = f"finished -- {status.replace('_', ' ')}" if status else "ended"
            run["proc"], run["pid"] = None, None
            _save_runs()
        return dict(_RUNS)


@app.get("/application/<int:app_id>")
def application(app_id: int):
    tracker = get_tracker()
    record = next((a for a in tracker.list_all() if a.id == app_id), None)
    if not record:
        abort(404)
    docs = [dict(d) for d in tracker.documents_for(app_id)]
    # The newest of each kind is the one in use; older ones were attached on an earlier attempt, which may have
    # been abandoned -- listing them all as "sent" read as two resumes sent to one employer (Aristocrat).
    newest = {}
    for d in docs:
        if d.get("kind") not in newest or (d.get("created_at"), d.get("id")) > (newest[d["kind"]].get("created_at"),
                                                                                newest[d["kind"]].get("id")):
            newest[d.get("kind")] = d
    for d in docs:
        d["current"] = newest.get(d.get("kind")) is d
    docs.sort(key=lambda d: (not d["current"], str(d.get("kind")), str(d.get("created_at"))), reverse=False)
    answers = tracker.answers_for(app_id)
    events = tracker.events(record.dedup_key) if hasattr(tracker, "events") else []
    decision, validation = latest_decision(events), latest_validation(record)
    latest_screenshot = ""
    for ev in events:
        if ev.get("screenshot_path") and Path(ev["screenshot_path"]).is_file():
            latest_screenshot = ev["screenshot_path"]
            break
    handoff = {}
    if hasattr(tracker, "read_checkpoint_by_dedup_key"):
        try:
            stored = tracker.read_checkpoint_by_dedup_key(record.dedup_key)
        except Exception:
            stored = None
        if stored:
            handoff = {
                "category": stored.get("handoff_category") or "",
                "reason": stored.get("handoff_reason") or "",
                "required_action": stored.get("handoff_required_action") or "",
                "resume_condition": stored.get("handoff_resume_condition") or "",
            }
    return render_template_string(
        DETAIL_HTML, a=record, docs=docs, answers=answers, events=events,
        decision=decision, validation=validation, progress=progress_of(record, validation),
        auto_submit_on=get_app_config().auto_submit_verified_only,
        latest_screenshot=latest_screenshot, run_log=run_log_for(record.url), handoff=handoff,
    )


def run_log_for(url: str) -> str:
    """The log of the latest run on this posting, if it is still on disk."""
    with _RUNS_LOCK:
        path = (_RUNS.get(url or "") or {}).get("log") or ""
    return path if path and Path(path).is_file() else ""


def latest_decision(events) -> dict:
    """The newest verified-auto-submit decision for this application."""
    for event in events:
        if event.get("kind") == "auto_submit" and isinstance(event.get("payload"), dict):
            return event["payload"]
    return {}


def latest_validation(record) -> dict:
    """The newest validation report written beside a review package."""
    folder = BASE_DIR / "output"
    candidates = sorted(folder.glob("*/step_*/validation.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    company = (record.company or "").replace(" ", "_").lower()
    for path in candidates:
        if company and company.split("_")[0] not in path.as_posix().lower():
            continue
        try:
            content = diagnostics.read_safe_artifact(path)
            if content is None:
                continue
            return json.loads(content)
        except Exception:
            continue
    return {}


def progress_of(record, validation: dict) -> dict:
    """How far along an application is, for the dashboard's progress bar."""
    order = ["prepared", "form_filled", "ready_to_submit", "submitted"]
    step = order.index(record.status) + 1 if record.status in order else 0
    blocked = record.status == "needs_user_review"
    missing = list(validation.get("required_still_blank") or [])
    return {"step": step, "of": len(order), "blocked": blocked, "missing": missing,
            "errors": list(validation.get("errors_shown") or []),
            "attestations": list(validation.get("attestations_pending") or []),
            "captcha": bool(validation.get("captcha"))}


@app.get("/evidence")
def evidence():
    """Serve only B5-marked artifacts within the existing resolved output root."""
    target = Path(request.args.get("path", "")).resolve()
    root = (BASE_DIR / "output").resolve()
    if target != root and root not in target.parents:
        abort(404)
    content = diagnostics.read_safe_artifact(target)
    if content is None:
        abort(404)
    response = Response(content, mimetype="image/png" if target.suffix.lower() == ".png" else "text/plain")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; script-src 'none'; sandbox"
    return response



@app.get("/document/<int:doc_id>")
def document(doc_id: int):
    """Serves a stored document straight from the database."""
    row = get_tracker().document(doc_id)
    if not row:
        abort(404)
    return send_file(
        io.BytesIO(row["content"]), mimetype=row["content_type"],
        download_name=row["filename"], as_attachment=False,
    )


@app.get("/log")
def log():
    path = request.args.get("path", "")
    target = Path(path).resolve()
    root = (BASE_DIR / "logs").resolve()
    if (target != root and root not in target.parents) or target.suffix not in {".log", ".jsonl", ".txt"}:
        abort(404)
    content = diagnostics.read_safe_artifact(target)
    if content is None:
        abort(404)
    response = Response(content, mimetype="text/plain")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; script-src 'none'; sandbox"
    return response


def serve() -> None:
    diagnostics.cleanup_expired_diagnostics(BASE_DIR)
    # Every run starts from here, so a dashboard on a desktop the owner does not
    # see would open every browser there too (visible_desktop.py): it does not start.
    reason = visible_desktop.why_invisible()
    if reason:
        print(reason, file=sys.stderr)
        sys.exit(2)
    print("Job application UI:  http://127.0.0.1:5000")
    # Loopback only, on purpose -- see the module docstring.
    #
    # use_reloader: editing the code restarts this server by itself, so a
    # change doesn't mean stopping and starting it by hand. Run state lives in
    # data/_runs.json, so a reload keeps track of a run that is still going,
    # and an application already in the browser is unaffected -- it runs in its
    # own process. (Set WEB_UI_NO_RELOAD=1 to switch it off.)
    app.run(host="127.0.0.1", port=5000, debug=False,
            use_reloader=os.getenv("WEB_UI_RELOAD", "") in {"1", "true", "yes"})


if __name__ == "__main__":
    serve()

