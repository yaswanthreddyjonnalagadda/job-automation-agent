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

import functools
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

from flask import Flask, Response, abort, g, has_request_context, jsonify, redirect, render_template_string, request, send_file, url_for

import diagnostics
import detail_view
import discovered_view
import health_view
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


# ----------------------------------------------------------------------
# Links to files the agent wrote (logs, screenshots, page captures)
# ----------------------------------------------------------------------
def _artifact_ref(path) -> str:
    """The reference a link carries for a file: its place inside the project, never the owner's own folders.
    A file outside the project has no reference."""
    if re.match(r"^[A-Za-z]:[\\/]", str(path)) and not re.match(r"^[A-Za-z]:", str(BASE_DIR)):
        return ""            # a Windows drive path is outside the project on a host that has no drives
    try:
        return Path(path).resolve().relative_to(BASE_DIR.resolve()).as_posix()
    except (ValueError, OSError):
        return ""


def _artifact_path(ref) -> Path:
    """The file a link's reference names (a project-relative reference; an absolute path from an old link still works).
    Whether it may be served is the route's decision, not this one's."""
    candidate = Path(str(ref or ""))
    return (candidate if candidate.is_absolute() else BASE_DIR / candidate).resolve()


@functools.lru_cache(maxsize=512)
def _servable_at(path: str, mtime: float, size: int) -> bool:
    return diagnostics.read_safe_artifact(Path(path)) is not None


def _artifact_servable(path) -> bool:
    """Whether the viewer will really serve this file. A link that answers 404 is worse than none."""
    try:
        target = Path(path)
        stat = target.stat()
        return _servable_at(str(target), stat.st_mtime, stat.st_size)
    except OSError:
        return False


def artifact_ok(path) -> bool:
    return bool(path) and bool(_artifact_ref(path)) and _artifact_servable(path)


def needs_you_count() -> int:
    """How many applications are waiting for the owner (the badge on the Applications tab). Never breaks a page."""
    if "needs_you" in g:
        return g.needs_you
    try:
        g.needs_you = ui_shell.group_counts(get_tracker().list_all())["needs"]
    except Exception:
        g.needs_you = 0
    return g.needs_you


app.jinja_env.filters["aref"] = _artifact_ref
app.jinja_env.globals.update(decision_view=detail_view.decision_view, answered_by=detail_view.answered_by,
                             history_rows=detail_view.group_events, worker_state=health_view.worker_state)
app.jinja_env.globals.update(artifact_ok=lambda p: artifact_ok(p), needs_you_count=lambda: needs_you_count())
app.jinja_env.globals["status_badge"] = ui_shell.status_badge


from web_setup import setup_pages  # noqa: E402  (the pages import web_ui back, lazily)

app.register_blueprint(setup_pages)
from web_progress import progress_pages  # noqa: E402
app.register_blueprint(progress_pages)

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
        logger.debug("_save_runs: %s", diagnostics.short_exc(_swallowed, 100))


#: Liveness answers by pid, kept for seconds: each check spawns tasklist on
#: Windows, and the runs are re-read on every page load.
_ALIVE_CACHE: dict = {}
_ALIVE_TTL = 10.0


@diagnostics.probe
def _process_alive(pid) -> bool:
    if not pid:
        return False
    now = time.monotonic()
    hit = _ALIVE_CACHE.get(pid)
    if hit is not None and now - hit[1] < _ALIVE_TTL:
        return hit[0]
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
            alive = str(pid) in out
        else:
            os.kill(int(pid), 0)
            alive = True
    except Exception:
        alive = False
    _ALIVE_CACHE[pid] = (alive, now)
    return alive


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
        logger.debug("_load_runs: %s", diagnostics.short_exc(_swallowed, 100))


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
            logger.debug("_kill_pid_tree: %s", diagnostics.short_exc(_swallowed, 100))


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
            logger.debug("_kill_tree: %s", diagnostics.short_exc(_swallowed, 100))


_SCAN = {"thread": None, "started": None}
_SCAN_LOCK = threading.Lock()


def scan_running() -> bool:
    thread = _SCAN["thread"]
    return bool(thread and thread.is_alive())


def wait_for_scan(seconds: float = 600) -> None:
    """Waits for a scan that is running (used by tests and by anything that must see its result)."""
    thread = _SCAN["thread"]
    if thread is not None:
        thread.join(seconds)


def _run_scan(engine) -> None:
    try:
        _remember_scan(engine.scan_all(min_match_threshold=60))
    except Exception as exc:
        logger.warning("The job scan failed: %s", diagnostics.short_exc(exc, 100))
    finally:
        _SCAN["started"] = None


@app.post("/api/discovered/scan")
def scan_discovered():
    """Starts a scan and returns at once: reading a hundred employers takes minutes, so it runs in the background."""
    from job_discovery import JobDiscoveryEngine
    with _SCAN_LOCK:
        if not scan_running():
            engine = JobDiscoveryEngine()
            # Besides the owner's own list, every employer an application was already made through is scanned.
            try:
                engine.history = [(a.company, a.url) for a in get_tracker().list_all() if getattr(a, "url", "")]
            except Exception as exc:
                logger.warning("Could not read the applications to find employers: %s", diagnostics.short_exc(exc, 100))
            _SCAN["started"] = datetime.now(timezone.utc)
            _SCAN["thread"] = threading.Thread(target=_run_scan, args=(engine,), daemon=True, name="job-scan")
            _SCAN["thread"].start()
    return redirect(url_for("index"))


_LAST_SCAN = BASE_DIR / "data" / "_last_scan.json"


def _remember_scan(stats) -> None:
    """What the last scan did, so the page can say so after it reloads (when, and how many boards)."""
    try:
        record = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "boards": int((stats or {}).get("companies_scanned") or 0),
                  "saved": int((stats or {}).get("relevant_jobs_saved") or 0),
                  "failed": int((stats or {}).get("boards_failed") or 0)}
        _LAST_SCAN.write_text(json.dumps(record), encoding="utf-8")
    except (OSError, TypeError, ValueError) as _swallowed:
        logger.debug("_remember_scan: %s", diagnostics.short_exc(_swallowed, 100))


def last_scan() -> dict:
    try:
        record = json.loads(_LAST_SCAN.read_text(encoding="utf-8"))
        record["at"] = datetime.fromisoformat(record["at"])
        return record
    except (OSError, ValueError, KeyError):
        return {}


@app.post("/api/discovered/<int:job_id>/apply")
def apply_discovered(job_id: int):
    from job_discovery import queue_discovered_job
    wants_json = request.headers.get("X-Requested-With") == "fetch"
    busy = _running_url()
    if busy:
        error = f"{busy[:80]} is still running. Stop it before starting another."
        if wants_json:
            return jsonify({"ok": False, "error": error})
        return redirect(url_for("index", error=error))
    job = queue_discovered_job(job_id)
    if job is None:
        if wants_json:
            return jsonify({"ok": False, "error": "That suggestion is no longer listed."}), 404
        return redirect(url_for("index"))
    if job.get("already_submitted"):
        error = f"You already submitted an application for {job.get('job_title')} at {job.get('company_name')}."
        if wants_json:
            return jsonify({"ok": False, "error": error})
        return redirect(url_for("index", error=error))
    if job.get("url"):
        threading.Thread(target=_run_apply, args=(job["url"].strip(),), daemon=True).start()
    if wants_json:
        return jsonify({"ok": True, "started": True, "job_id": job_id,
                        "title": job.get("job_title"), "company": job.get("company_name")})
    return redirect(url_for("index"))


@app.post("/api/discovered/<int:job_id>/readiness")
def check_readiness(job_id: int):
    from job_discovery import refresh_readiness
    import readiness as readiness_mod
    found = refresh_readiness(job_id)
    if request.headers.get("X-Requested-With") == "fetch":
        if not found:
            return jsonify({"ok": False, "error": "This posting's form cannot be pre-read."})
        return jsonify({"ok": True, "line": readiness_mod.summarize(found),
                        "needs_owner": found.get("needs_owner", [])})
    return redirect(url_for("index"))


@app.post("/api/discovered/<int:job_id>/dismiss")
def dismiss_discovered(job_id: int):
    from job_discovery import dismiss_discovered_job
    removed = dismiss_discovered_job(job_id)
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": True, "dismissed": job_id, "removed": removed})
    return redirect(url_for("index"))


@app.post("/api/discovered/bulk")
def bulk_discovered():
    from job_discovery import dismiss_discovered_job, queue_discovered_job
    wants_json = request.headers.get("X-Requested-With") == "fetch"
    action = (request.form.get("action") or "").strip()
    raw_ids = request.form.getlist("ids")
    if action not in ("dismiss", "queue") or not raw_ids:
        error = "Choose Queue or Pass and tick at least one job."
        if wants_json:
            return jsonify({"ok": False, "error": error}), 400
        return redirect(url_for("index", error=error))
    done, failed = [], []
    for raw in raw_ids:
        try:
            picked = int(raw)
        except (TypeError, ValueError):
            failed.append(raw)
            continue
        if action == "dismiss":
            (done if dismiss_discovered_job(picked) else failed).append(picked)
        else:
            queued = queue_discovered_job(picked)
            if queued is None or queued.get("already_submitted"):
                failed.append(picked)
            else:
                done.append(picked)
    if wants_json:
        return jsonify({"ok": True, "action": action, "done": done, "failed": failed})
    return redirect(url_for("index"))


@app.post("/api/auto-apply")
def auto_apply():
    """Starts the oldest queued application: the same run a Start press begins, so every gate still holds."""
    wants_json = request.headers.get("X-Requested-With") == "fetch"
    busy = _running_url()
    if busy:
        error = f"{busy[:80]} is still running. Stop it before starting another."
        if wants_json:
            return jsonify({"ok": False, "error": error})
        return redirect(url_for("index", error=error))
    queued = [a for a in get_tracker().list_all(status="ready_to_apply")
              if getattr(a, "status", "") == "ready_to_apply" and (getattr(a, "url", "") or "").strip()]
    if not queued:
        error = "Nothing is queued. Queue suggestions first."
        if wants_json:
            return jsonify({"ok": False, "error": error})
        return redirect(url_for("index", error=error))
    nxt = queued[-1]
    threading.Thread(target=_run_apply, args=(nxt.url.strip(),), daemon=True).start()
    if wants_json:
        return jsonify({"ok": True, "started": True, "id": nxt.id,
                        "title": nxt.title, "company": nxt.company})
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
_LEDGER_DB_PATH = BASE_DIR / "data" / "applications.db"
_LEDGER_LOCK = threading.RLock()


def init_answer_ledger() -> None:
    """Initializes the answer transparency ledger SQLite schema."""
    _LEDGER_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LEDGER_LOCK, sqlite3.connect(_LEDGER_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS application_answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_key TEXT NOT NULL,
                question_text TEXT NOT NULL,
                final_answer TEXT NOT NULL,
                source TEXT NOT NULL,
                fact_reference TEXT DEFAULT '',
                timestamp TEXT NOT NULL,
                verification_result TEXT DEFAULT '',
                previous_value TEXT DEFAULT '',
                correction_reason TEXT DEFAULT '',
                status TEXT DEFAULT 'COMMITTED'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_answers_app ON application_answers(application_key)")
        conn.commit()


def record_answer_ledger(
    application_key: str,
    question_text: str,
    final_answer: str,
    source: str = "PROFILE",
    fact_reference: str = "",
    verification_result: str = "ACCEPTED",
    previous_value: str = "",
    correction_reason: str = "",
    status: str = "COMMITTED",
) -> int:
    """Records or updates an answer entry in the transparency ledger."""
    init_answer_ledger()
    now_iso = datetime.now(timezone.utc).isoformat()
    with _LEDGER_LOCK, sqlite3.connect(_LEDGER_DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, final_answer FROM application_answers WHERE application_key = ? AND question_text = ? ORDER BY id DESC LIMIT 1",
            (application_key, question_text)
        )
        row = cursor.fetchone()
        if row:
            row_id, old_val = row
            if previous_value or (old_val != final_answer):
                prev = previous_value or old_val
                cursor.execute("""
                    UPDATE application_answers
                    SET final_answer = ?, source = ?, fact_reference = ?, timestamp = ?,
                        verification_result = ?, previous_value = ?, correction_reason = ?, status = ?
                    WHERE id = ?
                """, (final_answer, source, fact_reference, now_iso, verification_result, prev, correction_reason, status, row_id))
                conn.commit()
                return row_id
            return row_id

        cursor.execute("""
            INSERT INTO application_answers
            (application_key, question_text, final_answer, source, fact_reference, timestamp, verification_result, previous_value, correction_reason, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (application_key, question_text, final_answer, source, fact_reference, now_iso, verification_result, previous_value, correction_reason, status))
        conn.commit()
        return cursor.lastrowid or 0


def get_answers_for_application(application_key: str) -> list[dict]:
    """Retrieves all transparency ledger answers for a given application key."""
    init_answer_ledger()
    with _LEDGER_LOCK, sqlite3.connect(_LEDGER_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM application_answers WHERE application_key = ? ORDER BY id ASC",
            (application_key,)
        )
        return [dict(r) for r in cursor.fetchall()]


def update_answer_in_ledger(answer_id: int, new_value: str) -> bool:
    """Updates an existing answer entry (user override) and updates the answer bank."""
    init_answer_ledger()
    now_iso = datetime.now(timezone.utc).isoformat()
    with _LEDGER_LOCK, sqlite3.connect(_LEDGER_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM application_answers WHERE id = ?", (answer_id,))
        row = cursor.fetchone()
        if not row:
            return False
        old_val = row["final_answer"]
        q_text = row["question_text"]
        app_key = row["application_key"]
        cursor.execute("""
            UPDATE application_answers
            SET final_answer = ?, source = 'USER_OVERRIDE', previous_value = ?,
                correction_reason = 'User manual override from Cockpit', timestamp = ?, status = 'OVERRIDDEN'
            WHERE id = ?
        """, (new_value, old_val, now_iso, answer_id))
        conn.commit()

    try:
        tracker = get_tracker()
        if hasattr(tracker, "record_answer"):
            from urllib.parse import urlparse
            application = tracker.get(app_key)
            host = (urlparse(application.url).hostname or "") if application else ""
            tracker.record_answer(app_key, host, q_text, new_value, answered_by="user")
    except Exception as exc:
        logger.warning("Cockpit override saved locally but tracker synchronization failed (%s)", type(exc).__name__)
    return True


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


def _browser_check() -> dict:
    """Whether the browser the agent drives can be found (slow: it starts Playwright's driver)."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            return {"title": "Playwright Chromium", "ok": True, "message": f"Browser located: {Path(p.chromium.executable_path).name}"}
    except Exception as exc:
        return {"title": "Playwright Chromium", "ok": False,
                "message": f"Chromium not runnable: {diagnostics.short_exc(exc, 100)}",
                "hint": "Run `python -m playwright install chromium`"}


@app.get("/health/browser")
def health_browser():
    return _browser_check()


@app.get("/health")
def health_check():
    import config as cfg_mod

    checks = {}

    # 1. Playwright Chromium: starting its driver takes seconds, so the page asks for it afterwards.
    wants_html = request.accept_mimetypes.accept_html and not request.is_json
    if wants_html:
        checks["playwright"] = {"title": "Playwright Chromium", "ok": None, "pending": True, "message": "Checking\u2026"}
    else:
        checks["playwright"] = _browser_check()

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
            "message": f"Database unavailable: {diagnostics.short_exc(exc, 100)}",
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

    all_ok = all(c["ok"] for c in checks.values() if not c.get("pending"))
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

# How recently a suggested job was posted, from the last hour to the last week (the owner's choice on the page).
JOB_WINDOWS = {
    "1h": {"hours": 1, "label": "Last hour", "phrase": "in the last hour"},
    "24h": {"hours": 24, "label": "Last 24 hours", "phrase": "in the last 24 hours"},
    "3d": {"hours": 72, "label": "Last 3 days", "phrase": "in the last 3 days"},
    "7d": {"hours": 168, "label": "Last week", "phrase": "in the last week"},
}
DEFAULT_JOB_WINDOW = "7d"


def _feed_filter_context(args) -> dict:
    """The suggestions list's filters from the query string: validated, with defaults."""
    country_code = (args.get("country", "") or "").strip().upper() or None
    if country_code is not None and not (len(country_code) == 2 and country_code.isalpha()):
        country_code = None
    within_key = args.get("within", "")
    if within_key not in JOB_WINDOWS:
        within_key = DEFAULT_JOB_WINDOW
    company = (args.get("company", "") or "").strip()[:64] or None
    title = (args.get("title", "") or "").strip()[:64] or None
    try:
        min_score = int(args.get("min_score", 60))
    except (TypeError, ValueError) as _swallowed:
        logger.debug("feed min_score: %s", diagnostics.short_exc(_swallowed, 100))
        min_score = 60
    min_score = max(0, min(min_score, 100))
    workplace = (args.get("workplace", "") or "").strip().lower()
    if workplace not in ("remote", "onsite"):
        workplace = "all"
    kept = {}
    if country_code:
        kept["country"] = country_code
    if company:
        kept["company"] = company
    if title:
        kept["title"] = title
    if min_score != 60:
        kept["min_score"] = str(min_score)
    if workplace != "all":
        kept["workplace"] = workplace
    from urllib.parse import urlencode
    return {
        "country_code": country_code, "within_key": within_key, "window": JOB_WINDOWS[within_key],
        "company": company, "title": title, "min_score": min_score, "workplace": workplace,
        "remote_only": {"remote": True, "onsite": False}.get(workplace),
        "kept_params": ("&" + urlencode(kept)) if kept else "",
    }


def _suggested_jobs(apps, filters) -> tuple:
    """The suggestion cards plus the filter row's country options; empty on failure, never raising."""
    try:
        from job_discovery import JobDiscoveryEngine
        discovery_engine = JobDiscoveryEngine()
        # Posted inside the chosen window, newest first, and nothing the owner has already applied to, skipped or
        # been turned down for: those never come back as suggestions.
        jobs = discovered_view.prepare(
            discovery_engine.get_discovered_jobs(min_score=filters["min_score"], limit=40, hide_applied=False,
                                                 within_hours=filters["window"]["hours"],
                                                 country_code=filters["country_code"],
                                                 company_like=filters["company"],
                                                 remote_only=filters["remote_only"],
                                                 title_like=filters.get("title")), apps, newest_first=True)
    except Exception as _swallowed:
        logger.debug("suggested jobs: %s", diagnostics.short_exc(_swallowed, 100))
        jobs = []
    options: list = []
    try:
        from job_discovery import get_discovered_countries
        import geo_reference
        options = [(code, geo_reference.display_name(code)) for code in get_discovered_countries()]
    except Exception as _swallowed:
        logger.debug("index countries: %s", diagnostics.short_exc(_swallowed, 100))
    return jobs, options


def _filter_summary(filters, country_options) -> tuple:
    """Whether any filter is on, and the one line naming them."""
    bits = []
    if filters["country_code"]:
        bits.append(dict(country_options).get(filters["country_code"], filters["country_code"]))
    if filters.get("title"):
        bits.append('Role "%s"' % filters["title"])
    if filters["company"]:
        bits.append('"%s"' % filters["company"])
    if filters["min_score"] != 60:
        bits.append(f"{filters['min_score']}%+")
    if filters["workplace"] == "remote":
        bits.append("Remote")
    elif filters["workplace"] == "onsite":
        bits.append("On-site")
    return bool(bits), " \u00b7 ".join(bits)


def _inbox_items(tracker) -> list:
    """The newest employer-email signals with display labels; empty when the tracker has none."""
    recent = getattr(tracker, "recent_employer_signals", None)
    if not callable(recent):
        return []
    try:
        import employer_signals
        items = []
        for item in recent(10) or []:
            payload = item.get("payload") or {}
            items.append({
                "label": employer_signals.label(payload.get("signal")),
                "company": item.get("company"),
                "title": item.get("title"),
                "application_id": item.get("application_id"),
                "created_at": item.get("created_at"),
            })
        return items
    except Exception as _swallowed:
        logger.debug("index inbox: %s", diagnostics.short_exc(_swallowed, 100))
        return []

@app.get("/")
def index():
    import profile_setup
    if profile_setup.needs_setup():
        # A new person has no profile, and every answer comes from it: set it up first.
        return redirect(url_for("setup_pages.setup"))
    tracker = get_tracker()
    apps = tracker.list_all()
    g.app_records = apps
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
    except ValueError as _swallowed:
        logger.debug("index show: %s", diagnostics.short_exc(_swallowed, 100))
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

    filters = _feed_filter_context(request.args)
    discovered_jobs, country_options = _suggested_jobs(apps, filters)
    has_filters, filter_summary = _filter_summary(filters, country_options)

    queued_count = sum(1 for a in apps if getattr(a, "status", "") == "ready_to_apply")
    inbox = _inbox_items(tracker)
    return render_template_string(
        INDEX_HTML, apps=apps[:show], total=len(apps), shown=min(show, len(apps)),
        more=min(show + PAGE_SIZE, MAX_SHOWN), can_show_more=show < min(len(apps), MAX_SHOWN),
        groups=ui_shell.group_counts(apps), runs=latest_run(runs), signals=signals, error=request.args.get("error"),
        working_url=working_url, is_running=bool(live), job_title=job_title, employer=employer, stage=stage,
        discovered_jobs=discovered_jobs, last_scan=last_scan(), rest_line=_rest_line(runs),
        job_windows=JOB_WINDOWS, job_window=filters["window"], scanning=scan_running(),
        scan_started=_SCAN["started"], country_code=filters["country_code"] or "",
        country_options=country_options, company=filters["company"] or "",
        title=filters.get("title") or "",
        min_score=filters["min_score"], workplace=filters["workplace"],
        within_key=filters["within_key"], kept_params=filters["kept_params"],
        has_filters=has_filters, filter_summary=filter_summary,
        queued_count=queued_count,
        inbox=inbox,
    )


def _rest_line(runs: dict) -> str:
    """What the idle card says after "No application is running": how the last run ended."""
    run = next(iter(latest_run(runs).values()), None)
    if not run:
        return "Nothing has run yet."
    who = " at ".join(x for x in (run.get("title"), run.get("company")) if x)
    return f"Last run: {run.get('state') or 'ended'}" + (f" · {who}" if who else "")


_WAITING_NOTE_STALE_AFTER_SECONDS = 300.0


def waiting_runs(data_dir: Path) -> list[dict]:
    """The runs waiting for the owner, each with the signal file its Continue writes and a readable label.

    A run marks itself waiting with _waiting_<name>.txt (browser_automation.waiting_note_for). The card used to
    list only signal files that already existed -- an answer already sent -- so a run that was waiting had no
    Continue button at all (Rackspace, 29 September)."""
    found = []
    for note in sorted(Path(data_dir).glob("_waiting_*.txt")):
        # A live run touches its note every poll; a note untouched for five minutes
        # belongs to a dead run, and offering Continue on it strands the owner.
        try:
            if time.time() - note.stat().st_mtime > _WAITING_NOTE_STALE_AFTER_SECONDS:
                continue
        except OSError:
            continue
        name = note.name.replace("_waiting_", "", 1)
        label = name[:-4].replace("_", " ")
        try:
            job = json.loads((Path(data_dir) / f"_job_{name[:-4]}.json").read_text(encoding="utf-8-sig"))
            label = f"{job.get('title', '')} at {job.get('company', '')}".strip() or label
        except (OSError, ValueError) as _swallowed:
            logger.debug("waiting_runs: %s", diagnostics.short_exc(_swallowed, 100))
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


def _current_runs(app_records=None) -> dict:
    """The runs as they actually are.

    A run ends in its own process, which cannot write back here if this server
    was reloaded in the meantime -- so a finished application went on being
    shown as "running" until the dashboard was restarted. Anything whose
    process is gone is settled here instead, using the tracker's status when it
    has one. The index page lists the applications once and shares them through
    Flask's request store, so the tracker is not listed twice for one page;
    direct callers may pass records explicitly.
    """
    with _RUNS_LOCK:
        if not _RUNS:
            return {}
    records = app_records
    if records is None and has_request_context():
        records = getattr(g, "app_records", None)
    tracker = get_tracker()
    if records is None:
        records = tracker.list_all()
    by_url = {r.url: r for r in records if r.url}
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
    target = _artifact_path(request.args.get("path", ""))
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
    target = _artifact_path(request.args.get("path", ""))
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


# ----------------------------------------------------------------------
# Templates
# ----------------------------------------------------------------------
BASE_CSS = ui_shell.BASE_CSS     # the blueprints' pages share it


SETTINGS_HTML = ui_shell.page("Settings", """
<style>
.settings { max-width:720px; }
.settings label { display:block; margin:16px 0 6px; font-weight:600; }
.provider-row { display:flex; align-items:center; gap:10px; margin:18px 0 6px; }
.provider-row label { margin:0; }
</style>
<main class="wrap settings">
  <div class="page-head"><div>
    <h1>Settings</h1>
    <p class="sub">Keys and passwords are kept on this computer, in <code>.env</code>. A saved secret is never shown again.</p>
  </div></div>
  {% if saved %}<div class="ok">Settings saved. Restart the dashboard before starting another application.</div>{% endif %}
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  <div class="card">
    <form method="post" action="/settings">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">

      <p class="section-title" style="margin-top:0">AI providers</p>
      <p class="hint">Tried in order: Claude &rarr; Gemini &rarr; OpenAI. With none set, your own resume is attached as it is.</p>

      <div class="provider-row">
        <label for="anthropic_api_key">Claude (Anthropic)</label>
        <span class="badge {% if has_claude %}active{% else %}inactive{% endif %}">{% if has_claude %}Active{% else %}Not set{% endif %}</span>
      </div>
      <input id="anthropic_api_key" type="password" name="anthropic_api_key"
             placeholder="{% if has_claude %}Saved &mdash; leave blank to keep{% else %}sk-ant-...{% endif %}"
             autocomplete="new-password">
      <p class="hint">Get a key at <a href="https://console.anthropic.com" target="_blank" rel="noopener">console.anthropic.com</a></p>

      <div class="provider-row">
        <label for="gemini_api_key">Gemini (Google)</label>
        <span class="badge {% if has_gemini %}active{% else %}inactive{% endif %}">{% if has_gemini %}Active{% else %}Not set{% endif %}</span>
      </div>
      <input id="gemini_api_key" type="password" name="gemini_api_key"
             placeholder="{% if has_gemini %}Saved &mdash; leave blank to keep{% else %}AQ. ... or AIza ...{% endif %}"
             autocomplete="new-password">
      <p class="hint">Get a key at <a href="https://aistudio.google.com" target="_blank" rel="noopener">aistudio.google.com</a> (free tier available)</p>

      <div class="provider-row">
        <label for="openai_api_key">OpenAI (GPT-4o)</label>
        <span class="badge {% if has_openai %}active{% else %}inactive{% endif %}">{% if has_openai %}Active{% else %}Not set{% endif %}</span>
      </div>
      <input id="openai_api_key" type="password" name="openai_api_key"
             placeholder="{% if has_openai %}Saved &mdash; leave blank to keep{% else %}sk-...{% endif %}"
             autocomplete="new-password">
      <p class="hint">Get a key at <a href="https://platform.openai.com/api-keys" target="_blank" rel="noopener">platform.openai.com</a></p>

      <p class="section-title" style="margin-top:28px">Employer site sign-in</p>
      <label for="ats_email">Employer ATS email</label>
      <input id="ats_email" type="email" name="ats_email" value="{{ ats_email }}"
             placeholder="you@example.com" autocomplete="username">
      <label for="ats_password">Employer ATS password</label>
      <input id="ats_password" type="password" name="ats_password"
             placeholder="{% if has_password %}Saved &mdash; leave blank to keep{% else %}Enter password{% endif %}"
             autocomplete="new-password">
      <p class="hint">For Workday, Greenhouse, Lever or iCIMS. Never a LinkedIn, Indeed, Dice or Google password.</p>

      <button type="submit" style="margin-top:20px">Save settings</button>
    </form>
  </div>

  {% if ai %}
  {% macro model_label(m) -%}
    {%- set s = ai.state.get(m) -%}
    {{ m }}{% if s and s.why == 'daily' %} — spent until {{ s.until }}{% elif s and s.why in ('no free tier', 'missing') %} — cannot be used with this key{% elif s and s.why == 'no credit' %} — account out of credit{% elif s and s.calls %} — {{ s.calls }} today{% endif %}
  {%- endmacro %}
  {% macro model_options(provider, current) -%}
    <option value="">—</option>
    {%- set listed = ai.offered[provider].models -%}
    {%- if current and current not in listed %}<option value="{{ current }}" selected>{{ model_label(current) }} (not in the list)</option>{% endif -%}
    {%- for m in listed %}<option value="{{ m }}" {% if m == current %}selected{% endif %}>{{ model_label(m) }}</option>{% endfor -%}
  {%- endmacro %}
  {% macro writer_options(current, empty_label) -%}
    <option value="">{{ empty_label }}</option>
    {%- for p, name in ai.providers.items() %}
      <optgroup label="{{ name }}{% if not ai.offered[p].has_key %} (no key yet){% endif %}">
        {%- set listed = ai.offered[p].models -%}
        {%- if current and current.startswith(p ~ ':') and current[p|length + 1:] not in listed %}
          <option value="{{ current }}" selected>{{ name.split()[-1] }} · {{ current[p|length + 1:] }}</option>{% endif -%}
        {%- for m in listed %}<option value="{{ p }}:{{ m }}" {% if current == p ~ ':' ~ m %}selected{% endif %}>{{ name.split()[-1] }} · {{ model_label(m) }}</option>{% endfor -%}
      </optgroup>
    {%- endfor %}
  {%- endmacro %}

  <h2 id="ai">Which AI does what</h2>
  <p class="hint" style="margin:-4px 0 10px">Two separate choices. Answering the forms takes many small requests on
    every application; writing the resume takes a few large ones, once per job. Neither uses the other's models,
    so one cannot use up the other's allowance. Only models that write text are listed, as your keys offer them.
    <a href="/settings?refresh=1#ai">Ask the providers again</a></p>
  <form method="post" action="/settings/ai" class="ai-form">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">

    <div class="card">
      <h3>1. Answering the forms</h3>
      <p class="hint">Who reads each page and answers what your profile and saved answers do not.</p>
      <div class="choice-row" role="radiogroup" aria-label="Who answers the forms">
        {% for value, name in [('profile', 'Profile only (no AI)'), ('gemini', 'Google Gemini'), ('claude', 'Anthropic Claude'), ('openai', 'OpenAI')] %}
          <label class="choice"><input type="radio" name="form_answer_mode" value="{{ value }}" {% if ai.mode == value or (value == 'claude' and ai.mode not in ('profile', 'gemini', 'openai')) %}checked{% endif %}>
            {{ name }}{% if value != 'profile' and not ai.offered[value].has_key %} <span class="muted">(no key yet)</span>{% endif %}</label>
        {% endfor %}
      </div>
      {% for p, name in ai.providers.items() %}
      <fieldset class="ladders" data-provider="{{ p }}">
        <legend>{{ name }} models, in order</legend>
        {% if ai.offered[p].error %}<p class="err" style="margin:6px 0">{{ ai.offered[p].error }}</p>{% endif %}
        {% if not ai.offered[p].has_key %}<p class="hint">Add a {{ name }} key above to choose its models.</p>{% endif %}
        <div class="grid-2">
          {% for tier in ai.tiers %}
          <div>
            <p class="section-title" style="margin-top:8px">{{ 'Whole pages and written answers' if tier == 'page' else 'Quick choices and short questions' }}</p>
            <p class="hint" style="margin-bottom:6px">{{ 'Stronger models do these better.' if tier == 'page' else 'Lighter models with bigger allowances are enough.' }}
              Tick the ones to use. The first answers; when it runs out, the next takes over.</p>
            <input type="hidden" name="{{ p }}_{{ tier }}_sent" value="1">
            <ol class="picklist" aria-label="{{ name }} models for {{ 'pages' if tier == 'page' else 'quick choices' }}">
            {% for row in ai.ladders[p ~ '_' ~ tier] %}
              {%- set st = ai.state.get(row.model) -%}
              <li class="pick{% if not row.on %} off{% endif %}">
                <label class="pick-main">
                  <input type="checkbox" name="{{ p }}_{{ tier }}_{{ loop.index }}" value="{{ row.model }}" {% if row.on %}checked{% endif %}>
                  <span class="rank"></span>
                  <code>{{ row.model }}</code>
                </label>
                <span class="pick-state">
                  {%- if st and st.why == 'daily' %}<span class="pill needs_user_review">Spent until {{ st.until }}</span>
                  {%- elif st and st.why == 'no free tier' %}<span class="pill skipped">No free allowance</span>
                  {%- elif st and st.why == 'missing' %}<span class="pill skipped">Not on your key</span>
                  {%- elif st and st.why == 'no credit' %}<span class="pill rejected">Out of credit</span>
                  {%- elif st and st.why == 'minute' %}<span class="pill form_filled">Paused a moment</span>
                  {%- else %}<span class="pill submitted">Ready{% if st and st.calls %} · {{ st.calls }} today{% endif %}</span>{% endif -%}
                </span>
                <span class="pick-move">
                  <button type="button" class="ghost small" data-move="-1" aria-label="Move {{ row.model }} up">&uarr;</button>
                  <button type="button" class="ghost small" data-move="1" aria-label="Move {{ row.model }} down">&darr;</button>
                </span>
              </li>
            {% else %}
              <li class="hint" style="padding:8px 10px">No models listed yet.</li>
            {% endfor %}
            </ol>
          </div>
          {% endfor %}
        </div>
      </fieldset>
      {% endfor %}
    </div>

    <div class="card">
      <h3>2. Writing the resume and cover letter</h3>
      <p class="hint">Written once per job and kept: a retry, Resume or a restart uses the same resume.</p>
      <label class="stack">First choice
        <select name="resume_writer">{{ writer_options(ai.writer, 'Automatic: Claude, then Gemini, then OpenAI (whichever has a key)') }}</select></label>
      <label class="stack">If the first cannot write it
        <select name="resume_writer_fallback">{{ writer_options(ai.writer_fallback, 'No second choice (attach your own resume)') }}</select></label>
      <p class="hint">Whichever writes it, anything your resume does not show is taken out before it is sent.</p>
    </div>

    <button type="submit">Save AI choices</button>
    <p class="hint">Saved choices apply to the next application; no restart needed.</p>
  </form>
  <style>
    .choice-row { display:flex; flex-wrap:wrap; gap:8px; margin:10px 0 4px; }
    .choice { display:flex !important; align-items:center; gap:8px; margin:0 !important; padding:8px 12px; font-weight:500 !important;
              border:1px solid var(--line); border-radius:var(--radius-sm); cursor:pointer; background:var(--surface); }
    .choice:has(input:checked) { border-color:var(--accent); background:var(--accent-soft); }
    .ladders { border:0; padding:0; margin:14px 0 0; }
    .ladders legend { font-weight:650; padding:0; }
    .picklist { list-style:none; margin:4px 0 0; padding:0; border:1px solid var(--line); border-radius:var(--radius-sm);
                counter-reset:rank; }
    .pick { display:flex; flex-wrap:wrap; align-items:center; gap:6px 10px; padding:7px 10px; border-top:1px solid var(--line-2); }
    .pick:first-child { border-top:0; }
    .pick.off { opacity:.55; }
    .pick.off .pick-move { visibility:hidden; }
    .pick-main { display:flex !important; align-items:center; gap:10px; margin:0 !important; flex:1 1 190px; min-width:0;
                 font-weight:500 !important; cursor:pointer; }
    .pick-main code { background:none; padding:0; font-size:13px; overflow-wrap:anywhere; }
    .pick-state { margin-left:auto; }
    .ai-form .grid-2 { grid-template-columns:repeat(auto-fit, minmax(min(100%, 340px), 1fr)); }
    .pick:not(.off) { counter-increment:rank; }
    .pick:not(.off) .rank::before { content:counter(rank); }
    .rank { width:16px; color:var(--muted); font-size:12px; font-variant-numeric:tabular-nums; text-align:right; }
    .pick-state .pill { font-size:11px; padding:2px 8px; }
    .pick-move { display:flex; gap:4px; }
    .pick-move button { padding:2px 8px; }
    .stack { display:block; margin:14px 0 6px; }
    .stack select { margin-top:6px; }
    .settings { max-width:980px; }
  </style>
  <script>
    (() => {
      const radios = [...document.querySelectorAll('input[name=form_answer_mode]')];
      const sets = [...document.querySelectorAll('fieldset.ladders')];
      function show() {
        const mode = (radios.find(r => r.checked) || {}).value;
        // Only the chosen provider's lists are shown -- and only they are sent, so the others keep their saved order.
        sets.forEach(f => { const on = f.dataset.provider === mode; f.hidden = !on; f.disabled = !on; });
      }
      radios.forEach(r => r.addEventListener('change', show));
      show();
      // A ticked model is tried in the order shown; the arrows move it, and unticked ones sit below.
      function renumber(list) {
        const prefix = list.previousElementSibling.name.replace(/_sent$/, '');
        [...list.querySelectorAll('li.pick')].forEach((li, i) => {
          const box = li.querySelector('input[type=checkbox]');
          box.name = prefix + '_' + (i + 1);
          li.classList.toggle('off', !box.checked);
        });
      }
      document.querySelectorAll('.picklist').forEach(list => {
        list.addEventListener('click', e => {
          const button = e.target.closest('button[data-move]');
          if (!button) return;
          const li = button.closest('li');
          const step = Number(button.dataset.move);
          const other = step < 0 ? li.previousElementSibling : li.nextElementSibling;
          if (other && other.matches('li.pick:not(.off)')) {
            if (step < 0) list.insertBefore(li, other); else list.insertBefore(other, li);
            renumber(list);
            button.focus();
          }
        });
        list.addEventListener('change', e => {
          const li = e.target.closest('li.pick');
          if (!li) return;
          // Ticking puts a model at the end of the ticked ones; unticking moves it below them.
          const ticked = [...list.querySelectorAll('li.pick')].filter(x => x !== li && x.querySelector('input').checked);
          const last = ticked[ticked.length - 1];
          if (last) last.after(li); else list.prepend(li);
          renumber(list);
        });
      });
    })();
  </script>
  {% endif %}
</main>
""")


INDEX_HTML = ui_shell.page("Applications &middot; Job Agent", """
<main id="main" class="dash" tabindex="-1">
  <p class="sr-only" id="needs-live" role="status" aria-live="polite">{% if groups.needs %}{{ groups.needs }} application{{ '' if groups.needs == 1 else 's' }} need{{ 's' if groups.needs == 1 else '' }} you{% else %}Nothing needs you{% endif %}</p>
  <header class="dash-head">
    <div>
      <h1>Applications</h1>
    </div>
    <form class="dash-start" method="post" action="/apply" aria-label="Start an application">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <label for="job-url" class="sr-only">Link to the job posting</label>
      <input id="job-url" type="url" name="url" required inputmode="url" autocomplete="off"
             placeholder="Paste a job link from the employer's own site" aria-describedby="job-url-hint">
      <button type="submit">Start application</button>
    </form>
  </header>
  <p class="dash-start-hint" id="job-url-hint">Workday, Greenhouse, Lever, iCIMS and other employer sites. Job boards and staffing agencies are refused. {{ submit_promise() }}</p>

  {% if error %}<div class="err" role="alert" style="margin-top:16px">{{ error }}</div>{% endif %}

  {% if total %}
  <p class="dash-bar-label"><b>Submitted of tracked:</b> {{ groups.done }} of {{ total }}</p>
  <div class="dash-bar" role="img"
       aria-label="Submitted of tracked: {{ groups.done }} of {{ total }}">
    <i class="b-done" style="flex-grow:{{ groups.done }}"></i><i class="b-needs" style="flex-grow:{{ groups.needs }}"></i><i class="b-working" style="flex-grow:{{ groups.working }}"></i><i class="b-skipped" style="flex-grow:{{ groups.skipped }}"></i>
  </div>
  {% endif %}

  {% set waiting = apps | selectattr('status', 'in', ['ready_to_submit', 'needs_user_review', 'BLOCKED_VALIDATION_LOOP']) | rejectattr('url', 'equalto', working_url or '-') | list %}

  {% if signals or waiting %}
  <section class="dash-needs" aria-labelledby="needs-h">
    <h2 id="needs-h">Needs your action</h2>
    {% for s in signals %}
      <div class="item">
        <div>
          <b>{{ s.label }}</b>
          <small>The agent has paused in its browser window. Finish what it asks there, then press Continue.</small>
        </div>
        <div class="dash-actions">
          <form method="post" action="/signal/{{ s.signal }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <button name="decision" value="continue" title="The agent re-reads the page and carries on">Continue</button>
          </form>
          <form method="post" action="/signal/{{ s.signal }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <button class="ghost" name="decision" value="reload_code" title="Load the latest agent code into the paused run, then carry on">Use latest code</button>
            <button class="ghost" name="decision" value="skip" title="Leave this application and move on">Skip it</button>
            <button class="ghost" name="decision" value="close" title="Close the agent's browser window">Close window</button>
          </form>
          <form method="post" action="/restart" data-confirm="Start this application again?"
                data-confirm-body="It restarts from the job posting with the latest version of the agent. Answers already on the employer's form are kept." data-confirm-text="Start again">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <button class="ghost" title="Start again from the posting with the latest agent code">Start again</button>
          </form>
        </div>
      </div>
    {% endfor %}
    {% for a in waiting[:4] %}
      <div class="item">
        <div style="min-width:0">
          <a href="/application/{{ a.id }}"><b>{{ a.title }}</b></a>
          <small>{{ company_label(a.company) }} &middot; {{ status_badge(a.status)|safe }}</small>
          {% if a.notes %}<small style="display:block;margin-top:2px">{{ a.notes[:180] }}</small>{% endif %}
        </div>
        <div class="dash-actions">
          <a class="button small" href="{{ a.last_page_url or a.url }}" target="_blank" rel="noopener">Open the form<span class="sr-only"> (opens in a new tab)</span></a>
          <a class="button ghost small" href="/application/{{ a.id }}">Details</a>
        </div>
      </div>
    {% endfor %}
  </section>
  {% endif %}

  <div class="dash-section" style="margin-top:20px">
    {% include 'cockpit_card.html' %}
    {% if runs %}
      {% for url, r in runs.items() %}
        <p class="dash-sub" style="margin-top:10px">
          {% if r.state == 'running' %}Running{% else %}Last run: {{ r.state }}{% endif %}
          {% if r.title %}&middot; {{ r.title }}{% endif %}{% if r.company %} at {{ r.company }}{% endif %}
          {% if not r.title %}&middot; <a href="{{ url }}" target="_blank" rel="noopener">{{ url|truncate(70) }}</a>{% endif %}
          {% if r.started %}&middot; started {{ r.started|local }}{% endif %}
          {% if r.log and artifact_ok(r.log) %}&middot; <a href="/log?path={{ r.log|aref|urlencode }}" target="_blank" rel="noopener">View log</a>{% endif %}
          {% if r.screenshot and artifact_ok(r.screenshot) %}&middot; <a href="/evidence?path={{ r.screenshot|aref|urlencode }}" target="_blank" rel="noopener">Last screenshot</a>{% endif %}
          {% if r.app_id %}&middot; <a href="/application/{{ r.app_id }}">Details</a>{% endif %}
          {% if r.state == 'running' %}
            &middot; <form method="post" action="/reload-agent" style="display:inline">
              <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
              <button class="ghost small" title="Load the latest agent code into the run in progress">Reload agent code</button></form>
          {% endif %}
        </p>
      {% endfor %}
    {% endif %}
  </div>

  <section class="dash-section" aria-labelledby="hist-h">
    <header>
      <h2 id="hist-h">Your applications <span class="count">{{ total }}</span></h2>
      {% if queued_count %}
      <form method="post" action="/api/auto-apply" data-auto-apply>
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <button type="submit" class="ghost" title="Start the oldest queued application; you review it before anything is sent">Start next queued ({{ queued_count }})</button>
      </form>
      {% endif %}
    </header>
    <div class="dash-history">
      {% if apps %}
        <div class="toolbar">
          <div class="dash-chips" role="group" aria-label="Filter by status">
            <button type="button" class="dash-chip" data-filter="all" aria-pressed="true">All</button>
            <button type="button" class="dash-chip {{ 'has-items' if groups.needs else 'is-zero' }}" data-filter="needs" aria-pressed="false">Needs you<span class="n">{{ groups.needs }}</span></button>
            <button type="button" class="dash-chip" data-filter="working" aria-pressed="false">In progress<span class="n">{{ groups.working }}</span></button>
            <button type="button" class="dash-chip" data-filter="done" aria-pressed="false">Submitted<span class="n">{{ groups.done }}</span></button>
            <button type="button" class="dash-chip" data-filter="skipped" aria-pressed="false">Skipped<span class="n">{{ groups.skipped }}</span></button>
          </div>
          <label class="sr-only" for="app-search">Search your applications</label>
          <input type="search" id="app-search" placeholder="Search company or role" autocomplete="off">
        </div>
        <p class="sr-only" id="apps-count" role="status" aria-live="polite"></p>
      {% endif %}

      <table class="cards" id="apps">
        <caption class="sr-only">Your applications, newest first</caption>
        <thead><tr class="head"><th scope="col" class="company">Company</th><th scope="col">Role</th><th scope="col">Status</th><th scope="col" class="when">Updated</th><th scope="col" class="actions"><span class="sr-only">Actions</span></th></tr></thead>
        <tbody>
        {% for a in apps %}
        <tr class="app" data-group="{{ status_group(a.status) }}" data-text="{{ (a.company ~ ' ' ~ a.title ~ ' ' ~ (a.location or ''))|lower }}">
          <td class="company"><strong>{{ company_label(a.company) }}</strong></td>
          <td class="role">
            <a href="/application/{{ a.id }}">{{ a.title }}</a>
            <span class="loc">{{ a.location or 'Location not stated' }}</span>
          </td>
          <td class="status">{{ status_badge(a.status)|safe }}</td>
          <td class="when">{{ a.updated_at|local }}</td>
          <td class="actions"><div class="row-actions">
            {% if a.status != 'submitted' and status_group(a.status) != 'skipped' %}
              <form method="post" action="/resume/{{ a.id }}">
                <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                <button class="small" title="{{ 'Start this application' if a.status in ('prepared', 'ready_to_apply') else 'Pick this application up where it stopped' }}">{{ 'Start' if a.status in ('prepared', 'ready_to_apply') else 'Resume' }}<span class="sr-only"> {{ a.company }}</span></button>
              </form>
            {% else %}
              <a class="button ghost small" href="/application/{{ a.id }}">Details<span class="sr-only"> for {{ a.company }}</span></a>
            {% endif %}
            <details class="menu">
              <summary aria-label="More actions for {{ a.company }}, {{ a.title }}">&#8943;</summary>
              <div class="menu-items">
                <a class="button ghost" href="/application/{{ a.id }}" style="justify-content:flex-start;border:0;background:transparent">Details</a>
                {% if a.status != 'submitted' %}
                  <form method="post" action="/application/{{ a.id }}/status" data-confirm="Mark {{ a.company }} as submitted?"
                        data-confirm-body="Only do this if you sent it yourself. The agent will not try it again." data-confirm-text="Mark submitted">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                    <input type="hidden" name="status" value="submitted">
                    <input type="hidden" name="next" value="/">
                    <button title="Record that you submitted this one yourself">Mark as submitted</button>
                  </form>
                  <form method="post" action="/stop-application/{{ a.id }}" data-confirm="Stop this run?" data-confirm-body="The agent stops and its browser closes. Nothing is submitted." data-confirm-text="Stop it" data-danger="1">
                    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                    <button title="Stop the agent working on this application">Stop its run</button>
                  </form>
                {% endif %}
                <hr>
                <form method="post" action="/delete/{{ a.id }}" data-confirm="Delete {{ a.company }}?"
                      data-confirm-body="{{ a.title[:80] }}. This removes it from your list and cannot be undone." data-confirm-text="Delete" data-danger="1">
                  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                  <button class="danger" title="Remove this application from the list">Delete</button>
                </form>
              </div>
            </details>
          </div></td>
        </tr>
        {% else %}
        <tr><td colspan="5"><div class="empty"><b>Nothing tracked yet</b>Paste a job link above or start a suggested job.</div></td></tr>
        {% endfor %}
        </tbody>
      </table>

      <div class="dash-empty" id="no-match" hidden style="border:0;border-radius:0"><b>No matches</b>Nothing fits that filter or search.</div>

      <div class="more">
        <span class="muted">Showing {{ shown }} of {{ total }}</span>
        {% if can_show_more %}
          <a class="button ghost small" href="/?show={{ more }}">Show {{ more - shown }} more</a>
        {% elif total > 10 %}
          <a class="button ghost small" href="/?show=10">Show fewer</a>
        {% endif %}
      </div>
    </div>
  </section>

  <section class="dash-section" aria-labelledby="mail-h">
    <header>
      <h2 id="mail-h">Employer emails <span class="count">{{ inbox | length }}</span></h2>
    </header>
    {% if inbox %}
      <table class="cards" id="inbox">
        <caption class="sr-only">Recent employer emails, newest first</caption>
        <thead><tr class="head"><th scope="col">Signal</th><th scope="col">Application</th><th scope="col" class="when">When</th></tr></thead>
        <tbody>
        {% for s in inbox %}
        <tr>
          <td><span class="pill">{{ s.label }}</span></td>
          <td class="role"><a href="/application/{{ s.application_id }}">{{ s.title }}</a><span class="loc">{{ company_label(s.company) }}</span></td>
          <td class="when">{{ s.created_at|local }}</td>
        </tr>
        {% endfor %}
        </tbody>
      </table>
    {% else %}
      <div class="dash-empty"><b>No employer emails recorded yet</b>When an employer replies to an application, the reply lands here.</div>
    {% endif %}
  </section>

  <section class="dash-section" aria-labelledby="jobs-h">
    <header>
      <div>
        <h2 id="jobs-h">Suggested jobs <span class="count">{{ discovered_jobs | length }}</span></h2>
        <p class="dash-sub">Roles found on employers' own job boards that fit your profile. Starting one prepares the application; you review it before anything is sent.</p>
        <p class="dash-sub" id="scan-status" role="status" aria-live="polite">{% if scanning %}Checking job boards{% if scan_started %} since {{ scan_started|local('%I:%M %p') }}{% endif %}. This takes a few minutes; reload this page to see the result.{% elif last_scan %}Updated {{ last_scan.at|local('%I:%M %p') }} &middot; {{ last_scan.boards }} board{{ '' if last_scan.boards == 1 else 's' }} checked{% else %}Not checked yet{% endif %}</p>
      </div>
      <form method="post" action="/api/discovered/scan" data-busy="Checking job boards&hellip;">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <button type="submit" class="ghost" title="Look for new postings on your job boards and on the employers you have already applied through">Find new jobs</button>
      </form>
    </header>
    <div class="feed-hint" id="feed-hint" hidden>
      <p class="dash-sub" style="margin:0">Tip: filter by country, company, score and workplace, and tick cards to queue or pass several at once.</p>
      <button type="button" class="linklike" id="feed-hint-ok">Got it</button>
    </div>
    <nav class="dash-chips" aria-label="How recently the job was posted" style="margin-bottom:10px">
      {% for key, w in job_windows.items() %}
        <a class="dash-chip" href="/?within={{ key }}{{ kept_params }}#jobs-h"{% if w.hours == job_window.hours %} aria-current="true"{% endif %}>{{ w.label }}</a>
      {% endfor %}
    </nav>
    <form method="get" action="/" id="job-filters" class="filter-row" aria-label="Filter suggested jobs">
      <input type="hidden" name="within" value="{{ within_key }}">
      <label class="filter-field">Role / Title
        <input type="search" name="title" value="{{ title }}" maxlength="64" aria-label="Job title contains" placeholder="e.g. Network Engineer">
      </label>
      <label class="filter-field">Company
        <input type="search" name="company" value="{{ company }}" maxlength="64" aria-label="Company name contains" placeholder="Any employer">
      </label>
      <label class="filter-field">Country
        <select name="country" aria-label="Country">
          <option value="">All countries</option>
          {% for code, name in country_options %}<option value="{{ code }}"{% if code == country_code %} selected{% endif %}>{{ name }}</option>{% endfor %}
        </select>
      </label>
      <label class="filter-field">Minimum score
        <select name="min_score" aria-label="Minimum match score">
          {% for floor in (60, 70, 80, 85) %}<option value="{{ floor }}"{% if floor == min_score %} selected{% endif %}>{{ floor }}% and up</option>{% endfor %}
        </select>
      </label>
      <label class="filter-field">Workplace
        <select name="workplace" aria-label="Workplace">
          <option value="all"{% if workplace == "all" %} selected{% endif %}>On-site or remote</option>
          <option value="remote"{% if workplace == "remote" %} selected{% endif %}>Remote only</option>
          <option value="onsite"{% if workplace == "onsite" %} selected{% endif %}>On-site only</option>
        </select>
      </label>
      <button type="submit" class="small">Apply filters</button>
    </form>
    {% if has_filters %}
    <p class="dash-sub">Matching {{ filter_summary }} &middot; posted {{ job_window.phrase }} &middot; <a href="/#jobs-h">Clear</a></p>
    {% endif %}
    <details class="how-list"><summary>How this list works</summary>
<p class="dash-sub">Newest first. Jobs you have already applied to, skipped or been turned down for are not listed. A job whose site gives no posting date is not listed either; a site that dates a job only by its day (Workday) appears from "Last 24 hours" on, never under "Last hour".</p></details>
    {% if discovered_jobs %}
      <div class="feed-tools">
      <div class="dash-chips" role="group" aria-label="Narrow the loaded jobs">
        <button type="button" class="dash-chip" data-job-filter="all" aria-pressed="true">All</button>
        <button type="button" class="dash-chip" data-job-filter="top" aria-pressed="false">Best matches</button>
        <button type="button" class="dash-chip" data-job-filter="remote" aria-pressed="false">Remote</button>
        <button type="button" class="dash-chip" data-job-filter="greenhouse" aria-pressed="false">Greenhouse</button>
        <button type="button" class="dash-chip" data-job-filter="lever" aria-pressed="false">Lever</button>
        <button type="button" class="dash-chip" data-job-filter="workday" aria-pressed="false">Workday</button>
        <button type="button" class="dash-chip" data-job-filter="ashby" aria-pressed="false">Ashby</button>
        <button type="button" class="dash-chip" data-job-filter="icims" aria-pressed="false">iCIMS</button>
      </div>
      <form method="post" action="/api/discovered/bulk" data-bulk id="bulk-form" class="bulk-bar">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <span class="muted" role="status" data-bulk-count></span>
        <button type="submit" class="small" name="action" value="queue">Queue selected</button>
        <button type="submit" class="linklike" name="action" value="dismiss">Pass selected</button>
      </form>
      </div>
      <div class="dash-jobs" id="discovered-feed">
        {% for dj in discovered_jobs %}
          {% set fit = dj.resume_score if dj.resume_score is not none else dj.match_score %}
          <article class="job-card" data-job-id="{{ dj.id }}" data-score="{{ fit }}" data-remote="{{ dj.is_remote }}" data-ats="{{ dj.ats_type }}">
            <div class="job-top">
              <label class="bulk-pick"><input type="checkbox" form="bulk-form" name="ids" value="{{ dj.id }}"
                data-bulk-check aria-label="Select {{ dj.job_title }} at {{ dj.company_name }}"></label>
              <div class="ring {% if fit >= 85 %}high{% elif fit >= 70 %}mid{% endif %}" style="--p:{{ fit }}"
                   role="img" aria-label="{{ 'Resume match' if dj.resume_score is not none else 'Title match' }} {{ fit }} percent"><b>{{ fit }}%</b></div>
              <div style="min-width:0;flex:1">
                <p class="job-company">{{ dj.company_name }}</p>
                <h3 class="job-title"><a href="{{ dj.url }}" target="_blank" rel="noopener">{{ dj.job_title }}<span class="sr-only"> (opens the posting in a new tab)</span></a></h3>
              </div>
            </div>
            {# meta row: location, remote tag, age, ATS — compact single line #}
            <div class="job-meta">
              {% if dj.place %}<span title="{{ dj.location }}">{{ dj.place }}{% if dj.more_places %}&thinsp;+{{ dj.more_places }} more{% endif %}</span>
              {% elif not dj.is_remote %}<span class="muted">Location TBC</span>{% endif %}
              {% if dj.is_remote %}<span class="tag remote">Remote</span>{% endif %}
              {% if dj.age %}<span class="muted" style="font-size:11.5px"{% if dj.age_title %} title="{{ dj.age_title }}"{% endif %}>{{ dj.age }}</span>{% endif %}
              {% if dj.other_openings %}<span class="muted" style="font-size:11.5px">+{{ dj.other_openings }} loc</span>{% endif %}
              {# Status indicator badge — minimalist dot-badge replaces verbose text #}
              {% if dj.is_new %}<span class="tag new">New</span>
              {% endif %}
              <span class="tag" style="margin-left:auto">{{ dj.ats_type | capitalize }}</span>
            </div>
            {# score row: resume match score badge + title match, condensed #}
            <div class="job-meta">
              {% if dj.resume_score is not none %}
                <span class="status-badge matched" title="Your resume compared with the skills this posting asks for">Resume {{ dj.resume_score }}%</span>
              {% endif %}
              <span class="status-badge pending" title="Scored from the job title, location and seniority">Title {{ dj.match_score }}%</span>
            </div>
            {# skills matched / gaps — one compact line each, max 3 items #}
            {% if dj.resume_score is not none %}
              {% if dj.resume_matched %}
                <div class="job-meta skill-bar">
                  <span class="muted" style="font-size:11.5px">Has:</span>
                  {% for m in dj.resume_matched[:3] %}<span class="tag">{{ m }}</span>{% endfor %}
                </div>
              {% endif %}
              {% if dj.resume_missing %}
                <div class="job-meta skill-bar">
                  <span class="muted" style="font-size:11.5px">Gap:</span>
                  {% for m in dj.resume_missing[:2] %}<span class="tag skill-gap">{{ m }}</span>{% endfor %}
                </div>
              {% endif %}
            {% else %}
              <div class="job-meta"><span class="muted">No resume match: the posting's text was not read.</span></div>
              {% if dj.matched_skills %}
                <div class="job-meta skill-bar">
                  {% for m in dj.matched_skills[:3] %}<span class="tag">{{ m }}</span>{% endfor %}
                </div>
              {% endif %}
            {% endif %}
            {# readiness line or check button #}
            {% if dj.readiness %}
              <p class="muted" style="font-size:12px;margin:0">{{ dj.readiness }}</p>
            {% elif dj.ats_type == 'greenhouse' %}
              <form method="post" action="/api/discovered/{{ dj.id }}/readiness" data-readiness style="margin:0">
                <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                <button type="submit" class="linklike" style="font-size:12px">Check what it asks</button>
              </form>
            {% endif %}
            {# foot: posting link (primary), start, dismiss — compact single line #}
            <div class="job-foot">
              <a class="button ghost small" href="{{ dj.url }}" target="_blank" rel="noopener"
                 style="padding:4px 10px;font-size:12.5px">Posting&thinsp;&nearr;<span class="sr-only"> (opens the posting in a new tab)</span></a>
              <div style="display:flex;gap:6px;align-items:center">
                <form method="post" action="/api/discovered/{{ dj.id }}/apply" data-apply-card style="margin:0">
                  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                  <button type="submit" class="small" style="padding:4px 11px;font-size:12.5px">Apply</button>
                </form>
                <form method="post" action="/api/discovered/{{ dj.id }}/dismiss" data-remove-card style="margin:0">
                  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                  <button type="submit" class="linklike" aria-label="Not interested in {{ dj.job_title }} at {{ dj.company_name }}" style="font-size:12px">Pass</button>
                </form>
              </div>
            </div>
          </article>
        {% endfor %}
      </div>
      <div class="dash-empty" id="jobs-none" hidden><b>No jobs match that filter</b>Try another one.</div>
    {% else %}
      <div class="dash-empty">{% if has_filters %}<b>No jobs match those filters</b>{% else %}<b>No jobs posted {{ job_window.phrase }}</b>{% endif %}{% if last_scan %}Try a longer time, or press <b style="display:inline">Find new jobs</b> to check again.{% else %}Press <b style="display:inline">Find new jobs</b> to check your job boards and the employers you have applied through.{% endif %}</div>
    {% endif %}
  </section>
</main>
""")


DETAIL_HTML = ui_shell.page("{{ a.company }} &middot; {{ a.title }}", """
<main class="wrap">
  <p class="crumbs"><a href="/">Applications</a> / {{ company_label(a.company) }}</p>
  <div class="page-head">
    <div style="min-width:0">
      <h1>{{ a.title }}</h1>
      <div class="head-facts">
        <strong style="color:var(--ink)">{{ company_label(a.company) }}</strong>
        {% if a.location %}<span>{{ a.location }}</span>{% endif %}
        {{ status_badge(a.status)|safe }}
        <a href="{{ a.url }}" target="_blank" rel="noopener">Open posting &rarr;</a>
        {% if run_log and artifact_ok(run_log) %}<a href="/log?path={{ run_log|aref|urlencode }}" target="_blank">View log</a>{% endif %}
      </div>
    </div>
    <details class="menu">
      <summary aria-label="More actions">&#8943;</summary>
      <div class="menu-items" style="padding:10px; min-width:260px">
    <form method="post" action="/application/{{ a.id }}/status" style="display:flex; flex-direction:column; align-items:stretch; gap:8px; margin:0">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <label for="status-select" class="muted" style="font-size:13px">Change status</label>
      <select id="status-select" name="status" style="width:auto; padding:7px 10px">
        <option value="needs_user_review" {% if a.status == 'needs_user_review' %}selected{% endif %}>Needs you</option>
        <option value="submitted" {% if a.status == 'submitted' %}selected{% endif %}>Submitted</option>
        <option value="ready_to_submit" {% if a.status == 'ready_to_submit' %}selected{% endif %}>Ready to submit</option>
        <option value="form_filled" {% if a.status == 'form_filled' %}selected{% endif %}>Filling in</option>
        <option value="prepared" {% if a.status == 'prepared' %}selected{% endif %}>Prepared</option>
        <option value="skipped" {% if a.status == 'skipped' %}selected{% endif %}>Skipped</option>
        <option value="job_not_found" {% if a.status == 'job_not_found' %}selected{% endif %}>Job doesn't exist</option>
        <option value="already_applied" {% if a.status == 'already_applied' %}selected{% endif %}>Already applied</option>
        <option value="interviewing" {% if a.status == 'interviewing' %}selected{% endif %}>Interviewing</option>
        <option value="rejected" {% if a.status == 'rejected' %}selected{% endif %}>Rejected</option>
        <option value="offer" {% if a.status == 'offer' %}selected{% endif %}>Offer</option>
      </select>
      <button type="submit" class="small">Update</button>
    </form>
      </div>
    </details>
  </div>

  <div class="card">
    <ol class="stepper" aria-label="Progress">
      {% for name in ['Prepared', 'Filling in', 'Ready to submit', 'Submitted'] %}
        <li class="{{ 'done' if loop.index <= progress.step }}{{ ' blocked' if progress.blocked and loop.index == progress.step + 1 }}">{{ name }}</li>
      {% endfor %}
    </ol>
    <p style="margin:0"><strong>Step {{ progress.step }} of {{ progress.of }}</strong>
       {% if progress.blocked %}<span class="pill needs_user_review" style="margin-left:6px">Needs you</span>{% endif %}</p>
    {% if a.notes %}<p class="muted" style="margin:6px 0 0">{{ a.notes }}</p>{% endif %}
    {% if handoff and handoff.category and status_group(a.status) not in ('done', 'skipped') %}
      <p class="section-title">What to do ({{ handoff.category.replace('_', ' ').title() }})</p>
      <ul class="plain">
        {% if handoff.required_action %}<li>{{ handoff.required_action }}</li>{% endif %}
        {% if handoff.resume_condition %}<li class="muted">The agent continues once: {{ handoff.resume_condition }}</li>{% endif %}
      </ul>
    {% endif %}
    {% if progress.missing or progress.errors or progress.attestations or progress.captcha %}
      <p class="section-title">Still to do</p>
      <ul class="plain">
        {% for m in progress.missing %}<li>Blank: {{ m }}</li>{% endfor %}
        {% for e in progress.errors %}<li>Error: {{ e }}</li>{% endfor %}
        {% for s in progress.attestations %}<li>Your signature or attestation: {{ s }}</li>{% endfor %}
        {% if progress.captcha %}<li>A CAPTCHA is showing &mdash; only you can complete it</li>{% endif %}
      </ul>
    {% else %}
      <p class="hint">Nothing outstanding on the last check.</p>
    {% endif %}
  </div>

  <div class="grid-2">
    <div>
      {% if latest_screenshot and artifact_ok(latest_screenshot) %}
      <h2 style="margin-top:8px">What the agent sees</h2>
      <a class="shot" href="/evidence?path={{ latest_screenshot|aref|urlencode }}" target="_blank" title="Open the full image">
        <img src="/evidence?path={{ latest_screenshot|aref|urlencode }}" alt="The latest page the agent captured">
      </a>
      {% endif %}
      <h2{% if not (latest_screenshot and artifact_ok(latest_screenshot)) %} style="margin-top:8px"{% endif %}>About</h2>
      <div class="card flush">
        <table>
          <tr><th class="label">Applied via</th><td><a class="truncate" style="display:block" href="{{ a.url }}" target="_blank" rel="noopener" title="{{ a.url }}">{{ a.url }}</a></td></tr>
          <tr><th class="label">Created</th><td class="when">{{ a.created_at|local('%d %b %Y %I:%M %p') }}</td></tr>
          <tr><th class="label">Updated</th><td class="when">{{ a.updated_at|local('%d %b %Y %I:%M %p') }}</td></tr>
        </table>
      </div>
    </div>
    <div>
      <h2 style="margin-top:8px">Documents</h2>
      <div class="card flush">
        <table>
          <thead><tr><th>File</th><th class="num">Size</th><th class="when">Stored</th></tr></thead>
          {% for d in docs %}
          <tr>
            <td><a href="/document/{{ d.id }}" target="_blank">{{ d.filename }}</a>
                <div class="hint">{{ d.kind.replace('_',' ') }} ·
                  {% if d.current %}<span class="pill submitted">In use</span>{% else %}<span class="pill skipped">Earlier attempt</span>{% endif %}</div></td>
            <td class="num muted">{{ '%.1f'|format(d.byte_size/1024) }} KB</td>
            <td class="when">{{ d.created_at|local }}</td>
          </tr>
          {% else %}
          <tr><td colspan="3" class="muted">No documents stored.</td></tr>
          {% endfor %}
        </table>
      </div>

      <h2>Verified auto-submit</h2>
      <div class="card">
        <p class="hint" style="margin-top:0">Setting: <strong>{{ 'on' if auto_submit_on else 'off' }}</strong>
           (AUTO_SUBMIT_VERIFIED_ONLY). The agent submits once every mandatory field is filled and the form shows no error.</p>
        {% if decision %}
          {% set dv = decision_view(decision, status_group(a.status)) %}
          <p><span class="pill {{ 'submitted' if dv.eligible else 'needs_user_review' }}">{{ dv.verdict }}</span>
             {% if dv.when %}<span class="muted">decided {{ dv.when|local('%d %b %Y %I:%M %p') }}</span>{% endif %}</p>
          <p class="hint">{{ dv.note }}</p>
          {% if dv.policy %}<p class="hint">{{ dv.policy }}</p>{% endif %}
          {% if dv.reasons %}
            <ul class="plain">{% for r in dv.reasons %}<li>{{ r }}</li>{% endfor %}</ul>
          {% elif dv.no_reasons %}<p class="hint">{{ dv.no_reasons }}</p>{% endif %}
          {% if decision.evidence_paths %}
            <p class="hint">Evidence:
              {% for name, path in decision.evidence_paths.items() %}
                {% if artifact_ok(path) %}<a href="/evidence?path={{ path|aref|urlencode }}" target="_blank">{{ name }}</a>{{ ", " if not loop.last }}{% endif %}
              {% endfor %}
            </p>
          {% endif %}
        {% else %}
          <p class="muted" style="margin:0">No decision recorded for this application yet.</p>
        {% endif %}
      </div>
    </div>
  </div>

  {% if decision and decision.field_comparisons %}
  <h2>Fields checked</h2>
  <div class="card flush">
    <table>
      <thead><tr><th>Field</th><th>On the form</th><th>Approved value</th><th class="label">Source</th><th>Match</th></tr></thead>
      {% for c in decision.field_comparisons %}
        <tr>
          <td>{{ c.label }}</td><td>{{ c.on_form }}</td><td>{{ c.approved }}</td>
          <td class="muted">{{ c.source }}</td>
          <td>{% if c.matches %}<span class="pill submitted">match</span>
              {% elif c.required %}<span class="pill needs_user_review">check</span>
              {% else %}<span class="muted">optional</span>{% endif %}</td>
        </tr>
      {% endfor %}
    </table>
  </div>
  {% endif %}

  <h2>Answers given</h2>
  <div class="card flush">
    <table>
      <thead><tr><th style="width:45%">Question</th><th>Answer</th><th class="label">By</th></tr></thead>
      {% for q in answers %}
      <tr><td>{{ q.question }}</td><td>{{ q.answer }}</td><td class="label">{{ answered_by(q.answered_by) }}</td></tr>
      {% else %}
      <tr><td colspan="3" class="muted">No answers recorded for this application.</td></tr>
      {% endfor %}
    </table>
  </div>

  <h2>History</h2>
  <div class="card flush">
    <table>
      <thead><tr><th class="when">When</th><th class="label">Kind</th><th>What happened</th><th class="actions">Evidence</th></tr></thead>
      {% for e in history_rows(events) %}
        <tr>
          <td class="when">{{ e.created_at|local }}</td>
          <td class="muted">{{ e.label }}</td>
          <td>{{ (e.message or '')[:160] }}{% if e.count > 1 %} <span class="muted">&times;{{ e.count }}</span>{% endif %}</td>
          <td class="actions">
            {%- if e.screenshot_path and artifact_ok(e.screenshot_path) %}<a href="/evidence?path={{ e.screenshot_path|aref|urlencode }}" target="_blank">screenshot</a>{% endif -%}
            {%- if e.screenshot_path and artifact_ok(e.screenshot_path) and e.html_path and artifact_ok(e.html_path) %} &middot; {% endif -%}
            {%- if e.html_path and artifact_ok(e.html_path) %}<a href="/evidence?path={{ e.html_path|aref|urlencode }}" target="_blank">html</a>{% endif -%}
          </td>
        </tr>
      {% else %}
        <tr><td colspan="4" class="muted">No events recorded yet.</td></tr>
      {% endfor %}
    </table>
  </div>
</main>
""")


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

