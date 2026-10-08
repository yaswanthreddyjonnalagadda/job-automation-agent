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
import json
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

import application_status
import ui_shell
import visible_desktop
from config import get_app_config
from tracking import open_tracker as get_tracker

BASE_DIR = Path(__file__).resolve().parent
app = Flask(__name__)

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
    except Exception:
        pass


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
    except Exception:
        pass


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
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write('Application runtime: '+json.dumps(RUNTIME_INFO)+'\n')
            fh.flush()
            # Popen rather than run() so /stop can reach the process. On POSIX
            # it gets its own session so the whole group can be signalled.
            command = [sys.executable, "apply.py", url]
            if open_url:
                command += ["--open-url", open_url]
            proc = subprocess.Popen(
                command,
                cwd=str(BASE_DIR), stdout=fh, stderr=subprocess.STDOUT, text=True,
                start_new_session=(os.name != "nt"), env=run_environment(),
            )
            with _RUNS_LOCK:
                _RUNS[url]["proc"] = proc
                _RUNS[url]["pid"] = proc.pid
                _save_runs()
            proc.wait()
        state = "finished" if proc.returncode == 0 else f"failed (exit {proc.returncode})"
    except Exception as exc:
        state = f"failed ({exc})"

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
        except Exception:
            pass


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
        except ProcessLookupError:
            pass


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


@app.post("/application/<int:app_id>/status")
def change_application_status(app_id: int):
    """Updates an application's status directly from the UI (e.g. submitted, needs_user_review, etc.)."""
    new_status = (request.form.get("status") or "").strip()
    valid_statuses = {"prepared", "form_filled", "ready_to_submit", "needs_user_review", "submitted", "skipped",
                      "interviewing", "rejected", "offer"}   # what happened after submitting, for the Progress page
    if new_status not in valid_statuses:
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
    written = 0
    for signal_file in (BASE_DIR / "data").glob("_signal_*.txt"):
        signal_file.write_text("reload_code", encoding="utf-8")
        written += 1
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
    counts = {}
    for record in apps:
        counts[record.status] = counts.get(record.status, 0) + 1
    return render_template_string(
        INDEX_HTML, apps=apps[:show], total=len(apps), shown=min(show, len(apps)),
        more=min(show + PAGE_SIZE, MAX_SHOWN), can_show_more=show < min(len(apps), MAX_SHOWN),
        counts=counts, groups=ui_shell.group_counts(apps), runs=latest_run(runs), signals=signals, error=request.args.get("error"),
        working_url=working_url,
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
        except (OSError, ValueError):
            pass
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
            return json.loads(path.read_text(encoding="utf-8"))
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
    """Serves a screenshot/HTML/comparison file from this application's own
    output folder. Nothing outside output/ is readable."""
    target = Path(request.args.get("path", "")).resolve()
    root = (BASE_DIR / "output").resolve()
    if (target != root and root not in target.parents) or not target.is_file():
        abort(404)
    if target.suffix.lower() == ".png":
        return send_file(target, mimetype="image/png")
    return Response(target.read_text(encoding="utf-8", errors="replace"),
                    mimetype="text/plain" if target.suffix != ".html" else "text/html")


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
    if (target != root and root not in target.parents) or not target.is_file():
        abort(404)
    return Response(target.read_text(encoding="utf-8", errors="replace"), mimetype="text/plain")


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
<main class="wrap">
  <div class="page-head"><div>
    <h1>Applications</h1>
    <p class="sub">Paste a job link. The agent fills the form from your profile and saved answers, asks you only what
      it can't know, and stops at Review for you to submit.</p>
  </div></div>

  <div class="stats">
    <div class="stat tone-info"><b>{{ total }}</b><span>Tracked</span></div>
    <div class="stat tone-ok"><b>{{ groups.done }}</b><span>Submitted</span></div>
    <div class="stat tone-warn{% if groups.needs %} hot{% endif %}"><b>{{ groups.needs }}</b><span>Waiting for you</span></div>
    <div class="stat tone-violet"><b>{{ groups.talking }}</b><span>Interviews &amp; offers</span></div>
    <div class="stat tone-grey"><b>{{ groups.skipped }}</b><span>Skipped</span></div>
  </div>

  {% if error %}<div class="err" role="alert">{{ error }}</div>{% endif %}

  <div class="card apply-card">
    <form class="apply" method="post" action="/apply">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <input type="url" name="url" required aria-label="Job posting link"
             placeholder="Paste a job link: Workday, Greenhouse, Lever, Ashby, iCIMS...">
      <button type="submit">Apply</button>
    </form>
    <p class="hint">Only employers' own sites. Job boards and staffing agencies are refused.</p>
  </div>

  {% if runs.values()|selectattr('state', 'equalto', 'running')|list %}
    <script>
      // A run is active: reload every 5s so its state and the application's
      // status stay current -- but never while a URL is being typed or a menu is open.
      setInterval(() => {
        const box = document.querySelector("input[name=url]");
        if (box && (box.value || document.activeElement === box)) return;
        if (document.querySelector("details.menu[open]")) return;
        location.reload();   // the address keeps ?show=, so the list stays where it was
      }, 5000);
    </script>
  {% endif %}

  {% set waiting = apps | selectattr('status', 'in', ['ready_to_submit', 'needs_user_review', 'BLOCKED_VALIDATION_LOOP']) | rejectattr('url', 'equalto', working_url or '-') | list %}
  {% if working_url %}
    <div class="card" style="margin-bottom:12px"><strong>The agent is working</strong>
      <div class="hint">It is filling the form in its browser. When it stops for you, <em>Needs you</em> shows
        <strong>Continue</strong>, <strong>Resume</strong>, <strong>Skip</strong>, <strong>Close browser</strong>
        and <strong>Reload</strong>.</div></div>
  {% endif %}
  {% if signals or waiting %}
    <h2>Needs you</h2>
    <div class="card attention">
      {% if signals %}
        <p class="hint" style="margin:0 0 6px">The agent is waiting in its browser. Deal with what it stopped for,
          then press <strong>Continue</strong> and it reads the page again. <strong>Resume</strong> does the same
          with the agent's latest logic; <strong>Reload</strong> starts the application again from the posting.</p>
        {% for s in signals %}
          <div class="item">
            <div><strong>{{ s.label }}</strong><div class="hint">Paused in the browser</div></div>
            <div class="actions">
              <form method="post" action="/signal/{{ s.signal }}" class="actions">
                <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                <button name="decision" value="continue" title="You dealt with it: the agent reads the page again and carries on">Continue</button>
                <button class="ghost" name="decision" value="reload_code" title="Carry on from this page with the agent's latest logic -- the form stays as it is">Resume</button>
                <button class="ghost" name="decision" value="skip">Skip</button>
                <button class="ghost" name="decision" value="close">Close browser</button>
              </form>
              <form method="post" action="/restart" class="actions"
                    onsubmit="return confirm('Start this application again from the posting, with the latest logic? This browser closes; nothing is submitted, and what the site saved stays saved.')">
                <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                <button class="ghost" title="Close this run and start the application again from its posting, with the agent's latest logic">Reload</button>
              </form>
            </div>
          </div>
        {% endfor %}
      {% endif %}
      {% for a in waiting[:5] %}
        <div class="item">
          <div style="min-width:0">
            <a href="/application/{{ a.id }}"><strong>{{ a.title }}</strong></a>
            <span class="muted">at {{ a.company }}</span>
            <span class="pill {{ status_class(a.status) }}" style="margin-left:6px">{{ status_label(a.status) }}</span>
            {% if a.notes %}<div class="hint clamp-2">{{ a.notes[:220] }}</div>{% endif %}
          </div>
          <div class="actions">
            <a href="{{ a.last_page_url or a.url }}" target="_blank" rel="noopener"><button class="ghost" type="button">Open form &rarr;</button></a>
          </div>
        </div>
      {% endfor %}
      {% if waiting|length > 5 %}<p class="hint">and {{ waiting|length - 5 }} more in the list below</p>{% endif %}
    </div>
  {% endif %}

  {% if runs %}
    <h2>Current run</h2>
    {% for url, r in runs.items() %}
    <div class="card run">
      <div class="where">
        <div>
          {% if r.state == 'running' %}<span class="live">Running</span>{% else %}<span class="pill">{{ r.state }}</span>{% endif %}
          {% if r.title %}<strong style="margin-left:8px">{{ r.title }}</strong>{% endif %}
          {% if r.company %}<span class="muted"> at {{ r.company }}</span>{% endif %}
        </div>
        <a class="url truncate" href="{{ url }}" target="_blank" rel="noopener" title="{{ url }}">{{ url }}</a>
        {% if r.last_page_url and r.last_page_url != url %}
          <span class="url truncate" title="{{ r.last_page_url }}">Now at: {{ r.last_page_url }}</span>
        {% endif %}
        {% if r.started %}<div class="links"><span class="muted">Started {{ r.started|local }}</span></div>{% endif %}
      </div>
      <div class="row-actions">
        {% if r.log %}<a href="/log?path={{ r.log }}" target="_blank"><button class="ghost" type="button" title="What the agent did, step by step">View log</button></a>{% endif %}
        {% if r.screenshot %}<a href="/evidence?path={{ r.screenshot }}" target="_blank"><button class="ghost" type="button">View screen</button></a>{% endif %}
        {% if r.app_id %}<a href="/application/{{ r.app_id }}"><button class="ghost" type="button">Details</button></a>{% endif %}
        {% if r.state == 'running' %}
        <form method="post" action="/reload-agent">
          <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
          <button class="ghost" title="Load edited agent code into this run without restarting it">Reload agent code</button>
        </form>
        <form method="post" action="/stop"
              onsubmit="return confirm('Stop this application and close its browser? Nothing will be submitted, and you can start it again.')">
          <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
          <input type="hidden" name="url" value="{{ url }}">
          <button class="ghost danger">Stop</button>
        </form>
        {% endif %}
      </div>
    </div>
    {% endfor %}
  {% endif %}

  <h2>All applications <span class="count">{{ total }}</span></h2>
  <div class="card flush">
    {% if apps %}
    <div class="toolbar">
      <div class="chips" role="group" aria-label="Filter by status">
        <button type="button" class="chip" data-filter="all" aria-pressed="true">All</button>
        <button type="button" class="chip" data-filter="needs" aria-pressed="false">Needs you<span class="n">{{ groups.needs }}</span></button>
        <button type="button" class="chip" data-filter="working" aria-pressed="false">In progress<span class="n">{{ groups.working }}</span></button>
        <button type="button" class="chip" data-filter="done" aria-pressed="false">Submitted<span class="n">{{ groups.done }}</span></button>
        <button type="button" class="chip" data-filter="skipped" aria-pressed="false">Skipped<span class="n">{{ groups.skipped }}</span></button>
      </div>
      <input type="search" id="app-search" placeholder="Search company or role" aria-label="Search applications">
    </div>
    {% endif %}
    <table class="fixed cards" id="apps">
      <colgroup><col style="width:27%"><col><col style="width:160px"><col style="width:132px"><col style="width:168px"></colgroup>
      <thead><tr class="head"><th class="company">Company</th><th>Role</th><th>Status</th><th class="when">Updated</th><th class="actions">Actions</th></tr></thead>
      <tbody>
      {% for a in apps %}
      <tr class="app" data-group="{{ status_group(a.status) }}" data-text="{{ (a.company ~ ' ' ~ a.title ~ ' ' ~ (a.location or ''))|lower }}">
        <td class="company">
          <strong class="truncate" title="{{ a.company }}">{{ a.company }}</strong>
          <span class="loc truncate" title="{{ a.location or '' }}">{{ a.location or '—' }}</span>{# kept when empty: rows stay one height #}
        </td>
        <td class="role"><a href="/application/{{ a.id }}" class="clamp-2" title="{{ a.title }}" style="color:var(--ink)">{{ a.title }}</a></td>
        <td class="status"><span class="pill {{ status_class(a.status) }}" title="{{ a.status }}">{{ status_label(a.status) }}</span></td>
        <td class="when">{{ a.updated_at|local }}</td>
        <td class="actions"><div class="row-actions">
          {% if a.status != 'submitted' %}
            <form method="post" action="/resume/{{ a.id }}">
              <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
              <button class="ghost small" title="{{ 'Reopen the part-filled form at ' + a.last_page_url[:80] if a.last_page_url else 'Start this application again from the posting' }} -- the resume and answers already stored are reused">Resume</button>
            </form>
          {% else %}
            <a href="/application/{{ a.id }}"><button class="ghost small" type="button">Details</button></a>
          {% endif %}
          <details class="menu">
            <summary aria-label="More actions for {{ a.company }}" title="More actions">&#8943;</summary>
            <div class="menu-items">
              <a href="/application/{{ a.id }}"><button type="button">Details</button></a>
              {% if a.status != 'submitted' %}
                <form method="post" action="/application/{{ a.id }}/status">
                  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                  <input type="hidden" name="status" value="submitted">
                  <input type="hidden" name="next" value="/">
                  <button title="Mark this application as submitted">Mark submitted</button>
                </form>
                <form method="post" action="/stop-application/{{ a.id }}">
                  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                  <button title="Stop the run working on this application and close its browser. Nothing is submitted and the application is kept.">Stop its run</button>
                </form>
              {% endif %}
              <hr>
              <form method="post" action="/delete/{{ a.id }}"
                    onsubmit="return confirm('Delete {{ a.company }} -- {{ a.title[:60] }}?\\n\\nThis removes the application, its documents, its answers and its history. It cannot be undone.');">
                <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                <button class="danger" title="Remove this application and everything filed under it">Delete</button>
              </form>
            </div>
          </details>
        </div></td>
      </tr>
      {% else %}
      <tr><td colspan="5"><div class="empty"><b>Nothing tracked yet</b>Paste a job link above to start your first application.</div></td></tr>
      {% endfor %}
      </tbody>
    </table>
    <div class="empty" id="no-match" hidden><b>No matches</b>Nothing shown here fits that filter.</div>
    <div class="more">
      <span class="muted">Showing {{ shown }} of {{ total }}</span>
      {% if can_show_more %}
        <a href="/?show={{ more }}"><button class="ghost" type="button">Show {{ more - shown }} more</button></a>
      {% elif total > 10 %}
        <a href="/?show=10"><button class="ghost" type="button">Show fewer</button></a>
      {% endif %}
    </div>
  </div>
</main>
<script>
(() => {
  const rows = [...document.querySelectorAll("#apps tr.app")];
  const chips = [...document.querySelectorAll(".chip[data-filter]")];
  const search = document.getElementById("app-search");
  const none = document.getElementById("no-match");
  let group = "all";
  try { group = sessionStorage.getItem("apps-filter") || "all"; } catch (e) {}
  function apply() {
    const q = (search && search.value || "").trim().toLowerCase();
    let shown = 0;
    rows.forEach(r => {
      const ok = (group === "all" || r.dataset.group === group) && (!q || r.dataset.text.includes(q));
      r.hidden = !ok; if (ok) shown++;
    });
    chips.forEach(c => c.setAttribute("aria-pressed", String(c.dataset.filter === group)));
    if (none) none.hidden = shown > 0 || !rows.length;
  }
  chips.forEach(c => c.addEventListener("click", () => {
    group = c.dataset.filter;
    try { sessionStorage.setItem("apps-filter", group); } catch (e) {}
    apply();
  }));
  if (search) search.addEventListener("input", apply);
  // One row menu open at a time; a click elsewhere closes it.
  document.addEventListener("click", e => {
    document.querySelectorAll("details.menu[open]").forEach(d => { if (!d.contains(e.target)) d.open = false; });
  });
  apply();
})();
</script>
""")

DETAIL_HTML = ui_shell.page("{{ a.company }} &middot; {{ a.title }}", """
<main class="wrap">
  <p class="crumbs"><a href="/">Applications</a> / {{ a.company }}</p>
  <div class="page-head">
    <div style="min-width:0">
      <h1>{{ a.title }}</h1>
      <div class="head-facts">
        <strong style="color:var(--ink)">{{ a.company }}</strong>
        {% if a.location %}<span>{{ a.location }}</span>{% endif %}
        <span class="pill {{ status_class(a.status) }}">{{ status_label(a.status) }}</span>
        <a href="{{ a.url }}" target="_blank" rel="noopener">Open posting &rarr;</a>
        {% if run_log %}<a href="/log?path={{ run_log }}" target="_blank">View log</a>{% endif %}
      </div>
    </div>
    <form method="post" action="/application/{{ a.id }}/status" style="display:flex; align-items:center; gap:8px; margin:0">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <label for="status-select" class="muted" style="font-size:13px; white-space:nowrap">Change status</label>
      <select id="status-select" name="status" style="width:auto; padding:7px 10px">
        <option value="needs_user_review" {% if a.status == 'needs_user_review' %}selected{% endif %}>Needs you</option>
        <option value="submitted" {% if a.status == 'submitted' %}selected{% endif %}>Submitted</option>
        <option value="ready_to_submit" {% if a.status == 'ready_to_submit' %}selected{% endif %}>Ready to submit</option>
        <option value="form_filled" {% if a.status == 'form_filled' %}selected{% endif %}>Filling in</option>
        <option value="prepared" {% if a.status == 'prepared' %}selected{% endif %}>Prepared</option>
        <option value="skipped" {% if a.status == 'skipped' %}selected{% endif %}>Skipped</option>
        <option value="interviewing" {% if a.status == 'interviewing' %}selected{% endif %}>Interviewing</option>
        <option value="rejected" {% if a.status == 'rejected' %}selected{% endif %}>Rejected</option>
        <option value="offer" {% if a.status == 'offer' %}selected{% endif %}>Offer</option>
      </select>
      <button type="submit" class="small">Update</button>
    </form>
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
    {% if handoff and handoff.category %}
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
      {% if latest_screenshot %}
      <h2 style="margin-top:8px">What the agent sees</h2>
      <a class="shot" href="/evidence?path={{ latest_screenshot }}" target="_blank" title="Open the full image">
        <img src="/evidence?path={{ latest_screenshot }}" alt="The latest page the agent captured">
      </a>
      {% endif %}
      <h2{% if not latest_screenshot %} style="margin-top:8px"{% endif %}>About</h2>
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
           (AUTO_SUBMIT_VERIFIED_ONLY). The agent submits only when every check passes.</p>
        {% if decision %}
          <p><span class="pill {{ 'submitted' if decision.eligible else 'needs_user_review' }}">{{ 'Eligible' if decision.eligible else 'Not eligible' }}</span>
             <span class="muted">decided {{ decision.decided_at }}</span></p>
          {% if decision.reasons %}
            <ul class="plain">{% for r in decision.reasons %}<li>{{ r }}</li>{% endfor %}</ul>
          {% endif %}
          {% if decision.evidence_paths %}
            <p class="hint">Evidence:
              {% for name, path in decision.evidence_paths.items() %}
                <a href="/evidence?path={{ path }}" target="_blank">{{ name }}</a>{{ ", " if not loop.last }}
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
      <tr><td>{{ q.question }}</td><td>{{ q.answer }}</td><td class="label">{{ q.answered_by }}</td></tr>
      {% else %}
      <tr><td colspan="3" class="muted">No answers recorded for this application.</td></tr>
      {% endfor %}
    </table>
  </div>

  <h2>History</h2>
  <div class="card flush">
    <table>
      <thead><tr><th class="when">When</th><th class="label">Kind</th><th>What happened</th><th class="actions">Evidence</th></tr></thead>
      {% for e in events %}
        <tr>
          <td class="when">{{ e.created_at|local }}</td>
          <td class="muted">{{ e.kind }}</td>
          <td>{{ (e.message or '')[:160] }}</td>
          <td class="actions">
            {%- if e.screenshot_path %}<a href="/evidence?path={{ e.screenshot_path }}" target="_blank">screenshot</a>{% endif -%}
            {%- if e.screenshot_path and e.html_path %} &middot; {% endif -%}
            {%- if e.html_path %}<a href="/evidence?path={{ e.html_path }}" target="_blank">html</a>{% endif -%}
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

