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
import os
import signal
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, abort, redirect, render_template_string, request, send_file, url_for

from config import get_app_config
from db import get_tracker

BASE_DIR = Path(__file__).resolve().parent
app = Flask(__name__)

# Applications launched from this UI, so their progress can be shown. Keyed by
# the URL that started them, and written to disk so restarting this server --
# or letting it reload after a code change -- doesn't lose track of a run that
# is still going in its own process.
_RUNS: dict[str, dict] = {}
_RUNS_LOCK = threading.Lock()
_RUNS_FILE = BASE_DIR / "data" / "_runs.json"


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
        with open(log_path, "w", encoding="utf-8") as fh:
            # Popen rather than run() so /stop can reach the process. On POSIX
            # it gets its own session so the whole group can be signalled.
            command = [sys.executable, "apply.py", url]
            if open_url:
                command += ["--open-url", open_url]
            proc = subprocess.Popen(
                command,
                cwd=str(BASE_DIR), stdout=fh, stderr=subprocess.STDOUT, text=True,
                start_new_session=(os.name != "nt"),
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
        return redirect(url_for("index", error="Nothing was running."))
    # Leftover signal files would otherwise sit in "Waiting for a decision".
    for leftover in (BASE_DIR / "data").glob("_signal_*.txt"):
        leftover.unlink(missing_ok=True)
    return redirect(url_for("index"))


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
    for leftover in (BASE_DIR / "data").glob("_signal_*.txt"):
        leftover.unlink(missing_ok=True)
    return len(pids)


@app.post("/resume/<int:app_id>")
def resume_application(app_id: int):
    """Picks an application back up where it stopped.

    Used after a run ended with an error, was stopped, or was left needing
    attention. It starts the flow again for the same posting: the tailored
    resume and cover letter already stored are reused, answers already on the
    employer's form are left alone, and a job that is already submitted is
    refused as a duplicate.
    """
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
    if not str(target).startswith(str((BASE_DIR / "data").resolve())):
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
    tracker = get_tracker()
    apps = tracker.list_all()
    signals = sorted(p.name for p in (BASE_DIR / "data").glob("_signal_*.txt"))
    runs = _current_runs()
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
        counts=counts, runs=latest_run(runs), signals=signals, error=request.args.get("error"),
    )


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
    by_url = {}
    for record in tracker.list_all():
        if record.url:
            by_url[record.url] = record.status
    with _RUNS_LOCK:
        for url, run in _RUNS.items():
            if run["state"] != "running" or _process_alive(run.get("pid")):
                continue
            status = by_url.get(url, "")
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
    with tracker._connect() as conn:
        docs = conn.execute(
            "SELECT id, kind, filename, byte_size, created_at FROM documents "
            "WHERE application_id = %s ORDER BY created_at DESC",
            (app_id,),
        ).fetchall()
        answers = conn.execute(
            "SELECT question, answer, host, answered_by FROM form_answers "
            "WHERE application_id = %s ORDER BY question",
            (app_id,),
        ).fetchall()
    events = tracker.events(record.dedup_key) if hasattr(tracker, "events") else []
    decision, validation = latest_decision(events), latest_validation(record)
    return render_template_string(
        DETAIL_HTML, a=record, docs=docs, answers=answers, events=events,
        decision=decision, validation=validation, progress=progress_of(record, validation),
        auto_submit_on=get_app_config().auto_submit_verified_only,
    )


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
    if not str(target).startswith(str(root)) or not target.is_file():
        abort(404)
    if target.suffix.lower() == ".png":
        return send_file(target, mimetype="image/png")
    return Response(target.read_text(encoding="utf-8", errors="replace"),
                    mimetype="text/plain" if target.suffix != ".html" else "text/html")


@app.get("/document/<int:doc_id>")
def document(doc_id: int):
    """Serves a stored document straight from the database."""
    tracker = get_tracker()
    with tracker._connect() as conn:
        row = conn.execute(
            "SELECT filename, content_type, content FROM documents WHERE id = %s", (doc_id,)
        ).fetchone()
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
    if not str(target).startswith(str((BASE_DIR / "logs").resolve())) or not target.is_file():
        abort(404)
    return Response(target.read_text(encoding="utf-8", errors="replace"), mimetype="text/plain")


# ----------------------------------------------------------------------
# Templates
# ----------------------------------------------------------------------
BASE_CSS = """
:root { --bg:#f4f6f9; --card:#fff; --ink:#151a23; --muted:#6b7280; --line:#e6e9ef;
        --accent:#1a3d6d; --accent-soft:#eaf1fb; --ok:#0f7b46; --shadow:0 1px 2px rgba(16,24,40,.06),
        0 1px 3px rgba(16,24,40,.04); }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font:14px/1.55 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
       -webkit-font-smoothing:antialiased; }
.wrap { max-width:1060px; margin:0 auto; padding:28px 18px 72px; }
h1 { font-size:22px; letter-spacing:-.01em; margin:0 0 4px; }
h2 { font-size:12px; margin:26px 0 10px; color:var(--muted); font-weight:600;
     text-transform:uppercase; letter-spacing:.06em; }
.sub { color:var(--muted); margin:0 0 18px; max-width:70ch; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px;
        padding:16px; margin-bottom:14px; box-shadow:var(--shadow); }
.card.flush { padding:4px 4px 0; }
.card h3 { margin:0 0 4px; font-size:15px; }
/* The counters across the top: what is done, what is waiting. */
.stats { display:flex; flex-wrap:wrap; gap:10px; margin-bottom:16px; }
.stat { background:var(--card); border:1px solid var(--line); border-radius:12px;
        padding:10px 14px; min-width:104px; box-shadow:var(--shadow); }
.stat b { display:block; font-size:20px; line-height:1.2; }
.stat span { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.05em; }
form.apply { display:flex; gap:8px; flex-wrap:wrap; }
input[type=url] { flex:1; min-width:280px; padding:11px 13px; border:1px solid var(--line);
                  border-radius:9px; font-size:14px; background:#fff; color:var(--ink); }
input[type=url]:focus { outline:2px solid var(--accent-soft); border-color:var(--accent); }
button { background:var(--accent); color:#fff; border:0; border-radius:9px;
         padding:10px 16px; font-size:14px; font-weight:600; cursor:pointer; }
button:hover { filter:brightness(1.08); }
button:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
button.ghost { background:#f1f4f8; color:var(--ink); border:1px solid var(--line);
               padding:6px 12px; font-weight:500; line-height:1.4; }
button.ghost:hover { background:#e6ebf3; }
table { width:100%; border-collapse:collapse; }
th,td { text-align:left; padding:11px 12px; border-bottom:1px solid var(--line);
        vertical-align:top; }
th { color:var(--muted); font-weight:600; font-size:11px; white-space:nowrap;
     text-transform:uppercase; letter-spacing:.06em; background:#fbfcfe; }
tbody tr:hover { background:#fafbfd; }
tr:last-child td { border-bottom:0; }
/* Dates broke across two lines in the middle of a row, so nothing lined up. */
td.when, th.when { white-space:nowrap; width:1%; color:var(--muted); }
td.status, th.status { width:1%; white-space:nowrap; }
/* The row's buttons wrapped, leaving Delete on a line of its own. */
td.actions, th.actions { width:1%; white-space:nowrap; text-align:right; }
.row-actions { display:flex; gap:6px; align-items:center; justify-content:flex-end; }
.row-actions form { margin:0; display:inline-flex; }
.row-actions a { margin-right:2px; }
/* The label column of a detail table wrapped "Applied via" onto two lines. */
td.label, th.label { width:130px; white-space:nowrap; color:var(--muted);
                     font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
td.num, th.num { text-align:right; white-space:nowrap; width:1%; }
/* The employer needs room: "Charles Schwab" was breaking across two lines. */
td.company, th.company { width:210px; }
td.company strong { display:block; }
td.role, th.role { min-width:190px; }
/* A note under a row spans the table instead of squeezing into the last column. */
tr.note-row td { border-bottom:1px solid var(--line); padding:0 12px 11px; }
tr.note-row + tr td { border-top:0; }
td.logcell { width:1%; white-space:nowrap; }
.run-url { word-break:break-all; color:var(--muted); font-size:13px; }
.links a { white-space:nowrap; }
.links a + a::before { content:" · "; color:var(--muted); }
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:underline; }
.pill { display:inline-block; padding:3px 10px; border-radius:99px; font-size:12px;
        font-weight:600; white-space:nowrap; }
.submitted { background:#e3f5ea; color:#0f7b46; }
.ready_to_submit { background:#dff0ff; color:#0b5394; }
.needs_user_review { background:#ffe9d6; color:#9a4a00; }
.form_filled { background:#fff3d6; color:#8a5a00; }
.skipped { background:#eceef1; color:#6b7280; }
.prepared { background:#e7effa; color:#1a3d6d; }
.err { background:#fde8e8; color:#9b1c1c; padding:11px 13px; border-radius:9px;
       margin-bottom:14px; border:1px solid #f7cdcd; }
.muted { color:var(--muted); }
.note { color:var(--muted); max-width:460px; margin-top:4px; }
code { background:#eef1f5; padding:1px 6px; border-radius:5px; font-size:12px; }
.live { display:inline-flex; align-items:center; gap:6px; font-weight:600; color:var(--ok); }
.live::before { content:""; width:8px; height:8px; border-radius:50%; background:var(--ok);
                box-shadow:0 0 0 3px rgba(15,123,70,.15); }
.more { display:flex; align-items:center; justify-content:space-between; gap:10px;
        padding:12px 12px 14px; }
@media (max-width:720px) {
  th.when, td.when { display:none; }
  .row-actions { flex-wrap:wrap; justify-content:flex-start; }
  td.actions, th.actions { text-align:left; }
}
"""


INDEX_HTML = """
<!doctype html><meta charset="utf-8"><title>Job Applications</title>
<style>""" + BASE_CSS + """</style>
<div class="wrap">
  <h1>Job Applications</h1>
  <p class="sub">Paste an employer's job link and the agent applies: it reads each page,
     answers from your profile, attaches your tailored resume and submits when every
     check passes. Job boards and staffing agencies are refused.</p>

  <div class="stats">
    <div class="stat"><b>{{ total }}</b><span>tracked</span></div>
    <div class="stat"><b>{{ counts.get('submitted', 0) }}</b><span>submitted</span></div>
    <div class="stat"><b>{{ counts.get('needs_user_review', 0) + counts.get('ready_to_submit', 0) }}</b><span>waiting for you</span></div>
    <div class="stat"><b>{{ counts.get('skipped', 0) }}</b><span>skipped</span></div>
  </div>

  {% if error %}<div class="err">{{ error }}</div>{% endif %}

  {% set waiting = apps | selectattr('status', 'in', ['ready_to_submit', 'needs_user_review']) | list %}
  {% if waiting %}
    <div class="card" style="border-left:4px solid #0b5394">
      <strong>Waiting for you</strong>
      <p class="muted" style="margin:6px 0 0">The agent stopped on these and said why &mdash; a CAPTCHA,
         a question your profile doesn't answer, or something the site refused. Deal with it in the
         browser window and press <strong>Continue</strong>; the agent carries on from there.</p>
      <ul style="margin:8px 0 0 18px; padding:0">
        {% for a in waiting[:5] %}
          <li><a href="/application/{{ a.id }}">{{ a.title }}</a> at {{ a.company }} &mdash;
              <span class="pill {{ a.status }}">{{ a.status.replace('_', ' ') }}</span>
              <span class="muted">{{ (a.notes or '')[:140] }}</span></li>
        {% endfor %}
        {% if waiting|length > 5 %}
          <li class="muted">and {{ waiting|length - 5 }} more below</li>
        {% endif %}
      </ul>
    </div>
  {% endif %}

  <div class="card">
    <form class="apply" method="post" action="/apply">
      <input type="url" name="url" required
             placeholder="https://company.wd1.myworkdayjobs.com/... or jobs.lever.co/... or job-boards.greenhouse.io/...">
      <button type="submit">Apply</button>
    </form>
  </div>

  {% if runs.values()|selectattr('state', 'equalto', 'running')|list %}
    <script>
      // A run is active: reload every 5s so its state and the application's
      // status stay current -- but never while a URL is being typed.
      setInterval(() => {
        const box = document.querySelector("input[name=url]");
        if (box && (box.value || document.activeElement === box)) return;
        location.reload();   // the address keeps ?show=, so the list stays where it was
      }, 5000);
    </script>
  {% endif %}

  {% if runs %}
    <h2>Current run</h2>
    <div class="card">
      <table>
        <tr><th>Job</th><th>State</th><th class="label">Log</th><th class="actions">Controls</th></tr>
        {% for url, r in runs.items() %}
        <tr>
          <td class="run-url">{{ url[:110] }}{% if url|length > 110 %}&hellip;{% endif %}</td>
          <td>{% if r.state == 'running' %}<span class="live">running</span>{% else %}{{ r.state }}{% endif %}
              {% if r.started %}<span class="muted"> &middot; started {{ r.started|local }}</span>{% endif %}</td>
          <td class="logcell">{% if r.log %}<a href="/log?path={{ r.log }}" target="_blank">view log</a>{% else %}<span class="muted">&mdash;</span>{% endif %}</td>
          <td class="actions"><div class="row-actions">
            {% if r.state == 'running' %}
            <form method="post" action="/reload-agent">
              <button class="ghost" title="Load edited agent code into this run without restarting it">Reload agent code</button>
            </form>
            <form method="post" action="/stop"
                  onsubmit="return confirm('Stop this application and close its browser? Nothing will be submitted, and you can start it again.')">
              <input type="hidden" name="url" value="{{ url }}">
              <button class="ghost">Stop</button>
            </form>
            {% endif %}
          </div></td>
        </tr>
        {% endfor %}
      </table>
    </div>
  {% endif %}

  {% if signals %}
    <h2>Waiting for a decision</h2>
    <div class="card">
      <p class="muted">The agent is waiting at a page. Press <strong>Continue</strong> and it reads
         the page again and carries on &mdash; after you have dealt with whatever it stopped for.</p>
      {% for s in signals %}
        <div style="margin-top:8px">
          <code>{{ s }}</code>
          <form method="post" action="/signal/{{ s }}" style="display:inline">
            <button name="decision" value="continue">Continue</button>
            <button class="ghost" name="decision" value="reload_code">Reload agent code</button>
            <button class="ghost" name="decision" value="skip">Skip</button>
            <button class="ghost" name="decision" value="close">Close browser</button>
          </form>
        </div>
      {% endfor %}
    </div>
  {% endif %}

  <h2>Applications</h2>
  <div class="card flush">
    <table>
      <tr><th class="company">Company</th><th class="role">Role</th><th class="status">Status</th>
          <th class="when">Updated</th><th class="actions">Controls</th></tr>
      {% for a in apps %}
      <tr>
        <td class="company"><strong>{{ a.company }}</strong><span class="muted">{{ a.location or '' }}</span></td>
        <td class="role">{{ a.title }}</td>
        <td class="status"><span class="pill {{ a.status }}">{{ a.status.replace('_',' ') }}</span></td>
        <td class="when">{{ a.updated_at|local }}</td>
        <td class="actions"><div class="row-actions">
          <a href="/application/{{ a.id }}">details</a>
          {% if a.status != 'submitted' %}
            <form method="post" action="/resume/{{ a.id }}">
              <button class="ghost" title="{{ 'Reopen the part-filled form at ' + a.last_page_url[:80] if a.last_page_url else 'Start this application again from the posting' }} -- the resume and answers already stored are reused">Resume</button>
            </form>
            <form method="post" action="/stop-application/{{ a.id }}">
              <button class="ghost" title="Stop the run working on this application and close its browser. Nothing is submitted and the application is kept.">Stop</button>
            </form>
          {% endif %}
          <form method="post" action="/delete/{{ a.id }}"
                onsubmit="return confirm('Delete {{ a.company }} -- {{ a.title[:60] }}?\n\nThis removes the application, its documents, its answers and its history. It cannot be undone.');">
            <button class="ghost" title="Remove this application and everything filed under it">Delete</button>
          </form>
        </div></td>
      </tr>
      {% if a.status in ('ready_to_submit', 'needs_user_review') and a.notes %}
        <tr class="note-row"><td colspan="5" class="note">{{ a.notes[:220] }}</td></tr>
      {% endif %}
      {% else %}
      <tr><td colspan="5" class="muted">Nothing tracked yet.</td></tr>
      {% endfor %}
    </table>
    <div class="more">
      <span class="muted">Showing {{ shown }} of {{ total }}</span>
      {% if can_show_more %}
        <a href="/?show={{ more }}"><button class="ghost" type="button">Show {{ more - shown }} more</button></a>
      {% elif total > 10 %}
        <a href="/?show=10"><button class="ghost" type="button">Show fewer</button></a>
      {% endif %}
    </div>
  </div>
</div>
"""

DETAIL_HTML = """
<!doctype html><meta charset="utf-8"><title>{{ a.company }} — {{ a.title }}</title>
<style>""" + BASE_CSS + """</style>
<div class="wrap">
  <p><a href="/">&larr; All applications</a></p>
  <h1>{{ a.title }}</h1>
  <p class="sub">{{ a.company }}{% if a.location %} — {{ a.location }}{% endif %}
     &nbsp;<span class="pill {{ a.status }}">{{ a.status.replace('_',' ') }}</span></p>

  <div class="card">
    <table>
      <tr><th class="label">Applied via</th><td><a href="{{ a.url }}" target="_blank">{{ a.url[:80] }}</a></td></tr>
      <tr><th class="label">Created</th><td class="when">{{ a.created_at|local('%d %b %Y %I:%M %p') }}</td></tr>
      <tr><th class="label">Updated</th><td class="when">{{ a.updated_at|local('%d %b %Y %I:%M %p') }}</td></tr>
      {% if a.notes %}<tr><th class="label">Notes</th><td>{{ a.notes }}</td></tr>{% endif %}
    </table>
  </div>

  <h2>Documents sent</h2>
  <div class="card">
    <table>
      <tr><th class="label">Kind</th><th>File</th><th class="num">Size</th>
          <th class="when">Stored</th></tr>
      {% for d in docs %}
      <tr>
        <td class="label">{{ d.kind.replace('_',' ') }}</td>
        <td><a href="/document/{{ d.id }}" target="_blank">{{ d.filename }}</a></td>
        <td class="num muted">{{ '%.1f'|format(d.byte_size/1024) }} KB</td>
        <td class="when">{{ d.created_at|local }}</td>
      </tr>
      {% else %}
      <tr><td colspan="4" class="muted">No documents stored.</td></tr>
      {% endfor %}
    </table>
  </div>

  <h2>Progress</h2>
  <div class="card">
    <p><strong>{{ a.status.replace('_', ' ') }}</strong>
       &mdash; step {{ progress.step }} of {{ progress.of }}
       {% if progress.blocked %}<span class="pill needs_user_review">needs you</span>{% endif %}</p>
    {% if a.notes %}<p class="muted">{{ a.notes }}</p>{% endif %}
    {% if progress.missing or progress.errors or progress.attestations or progress.captcha %}
      <p><strong>Still to do</strong></p>
      <ul style="margin:4px 0 0 18px">
        {% for m in progress.missing %}<li>blank: {{ m }}</li>{% endfor %}
        {% for e in progress.errors %}<li>error: {{ e }}</li>{% endfor %}
        {% for s in progress.attestations %}<li>your signature/attestation: {{ s }}</li>{% endfor %}
        {% if progress.captcha %}<li>a CAPTCHA is showing &mdash; only you can complete it</li>{% endif %}
      </ul>
    {% else %}
      <p class="muted">Nothing outstanding on the last check.</p>
    {% endif %}
  </div>

  <h2>Verified auto-submit</h2>
  <div class="card">
    <p class="muted">Setting: <strong>{{ 'on' if auto_submit_on else 'off' }}</strong>
       (AUTO_SUBMIT_VERIFIED_ONLY). The agent submits only when every check below passes.</p>
    {% if decision %}
      <p><strong>{{ 'Eligible' if decision.eligible else 'Not eligible' }}</strong>
         <span class="muted">decided {{ decision.decided_at }}</span></p>
      {% if decision.reasons %}
        <ul style="margin:4px 0 8px 18px">
          {% for r in decision.reasons %}<li>{{ r }}</li>{% endfor %}
        </ul>
      {% endif %}
      {% if decision.field_comparisons %}
        <table>
          <tr><th>Field</th><th>On the form</th><th>Approved value</th>
          <th class="label">Source</th><th class="status">Match</th></tr>
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
      {% endif %}
      {% if decision.evidence_paths %}
        <p class="muted" style="margin-top:8px">Evidence:
          {% for name, path in decision.evidence_paths.items() %}
            <a href="/evidence?path={{ path }}" target="_blank">{{ name }}</a>{{ ", " if not loop.last }}
          {% endfor %}
        </p>
      {% endif %}
    {% else %}
      <p class="muted">No decision recorded for this application yet.</p>
    {% endif %}
  </div>

  <h2>History</h2>
  <div class="card">
    <table>
      <tr><th class="when">When</th><th class="label">Kind</th><th>What happened</th>
          <th class="actions">Evidence</th></tr>
      {% for e in events %}
        <tr>
          <td class="when">{{ e.created_at|local }}</td>
          <td class="muted">{{ e.kind }}</td>
          <td>{{ (e.message or '')[:160] }}</td>
          <td class="actions"><span class="links">
            {%- if e.screenshot_path %}<a href="/evidence?path={{ e.screenshot_path }}" target="_blank">screenshot</a>{% endif -%}
            {%- if e.html_path %}<a href="/evidence?path={{ e.html_path }}" target="_blank">html</a>{% endif -%}
          </span></td>
        </tr>
      {% else %}
        <tr><td colspan="4" class="muted">No events recorded yet.</td></tr>
      {% endfor %}
    </table>
  </div>

  <h2>Answers given</h2>
  <div class="card">
    <table>
      <tr><th>Question</th><th>Answer</th><th class="label">By</th></tr>
      {% for q in answers %}
      <tr><td>{{ q.question }}</td><td>{{ q.answer }}</td><td class="label">{{ q.answered_by }}</td></tr>
      {% else %}
      <tr><td colspan="3" class="muted">No answers recorded for this application.</td></tr>
      {% endfor %}
    </table>
  </div>
</div>
"""


if __name__ == "__main__":
    print("Job application UI:  http://127.0.0.1:5000")
    # Loopback only, on purpose -- see the module docstring.
    #
    # use_reloader: editing the code restarts this server by itself, so a
    # change doesn't mean stopping and starting it by hand. Run state lives in
    # data/_runs.json, so a reload keeps track of a run that is still going,
    # and an application already in the browser is unaffected -- it runs in its
    # own process. (Set WEB_UI_NO_RELOAD=1 to switch it off.)
    app.run(host="127.0.0.1", port=5000, debug=False,
            use_reloader=os.getenv("WEB_UI_NO_RELOAD", "") not in {"1", "true", "yes"})

