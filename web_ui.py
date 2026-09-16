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
    with _RUNS_LOCK:
        if any(r["state"] == "running" for r in _RUNS.values()):
            return redirect(url_for("index", error="An application is already running."))
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
    with _RUNS_LOCK:
        if any(r["state"] == "running" for r in _RUNS.values()):
            return redirect(url_for("index", error="An application is already running."))
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
@app.get("/")
def index():
    tracker = get_tracker()
    apps = tracker.list_all()
    signals = sorted(p.name for p in (BASE_DIR / "data").glob("_signal_*.txt"))
    runs = _current_runs()
    return render_template_string(
        INDEX_HTML, apps=apps, runs=runs, signals=signals, error=request.args.get("error"),
    )


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
:root { --bg:#f6f7f9; --card:#fff; --ink:#1a1f2b; --muted:#6b7280; --line:#e5e7eb;
        --accent:#1a3d6d; --ok:#0f7b46; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif; }
.wrap { max-width:1000px; margin:0 auto; padding:24px 16px 60px; }
h1 { font-size:20px; margin:0 0 4px; }
h2 { font-size:15px; margin:28px 0 10px; color:var(--muted);
     text-transform:uppercase; letter-spacing:.04em; }
.sub { color:var(--muted); margin:0 0 20px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px;
        padding:16px; margin-bottom:14px; }
form.apply { display:flex; gap:8px; }
input[type=url] { flex:1; padding:10px 12px; border:1px solid var(--line);
                  border-radius:8px; font-size:14px; }
button { background:var(--accent); color:#fff; border:0; border-radius:8px;
         padding:10px 16px; font-size:14px; cursor:pointer; }
button.ghost { background:#eef1f5; color:var(--ink); }
table { width:100%; border-collapse:collapse; }
th,td { text-align:left; padding:9px 8px; border-bottom:1px solid var(--line);
        vertical-align:top; }
th { color:var(--muted); font-weight:600; font-size:12px;
     text-transform:uppercase; letter-spacing:.04em; }
a { color:var(--accent); }
.pill { display:inline-block; padding:2px 9px; border-radius:99px; font-size:12px;
        font-weight:600; }
.submitted { background:#e3f5ea; color:#0f7b46; }
.ready_to_submit { background:#dff0ff; color:#0b5394; }
.needs_user_review { background:#ffe9d6; color:#9a4a00; }
.form_filled { background:#fff3d6; color:#8a5a00; }
.skipped { background:#eceef1; color:#6b7280; }
.prepared { background:#e7effa; color:#1a3d6d; }
.err { background:#fde8e8; color:#9b1c1c; padding:10px 12px; border-radius:8px;
       margin-bottom:14px; }
.muted { color:var(--muted); }
code { background:#eef1f5; padding:1px 5px; border-radius:4px; font-size:12px; }
"""

INDEX_HTML = """
<!doctype html><meta charset="utf-8"><title>Job Applications</title>
<style>""" + BASE_CSS + """</style>
<div class="wrap">
  <h1>Job Applications</h1>
  <p class="sub">Paste an employer job URL. Workday, Greenhouse and Lever are read
     automatically; job boards and staffing agencies are rejected.</p>

  {% if error %}<div class="err">{{ error }}</div>{% endif %}

  {% set waiting = apps | selectattr('status', 'in', ['ready_to_submit', 'needs_user_review']) | list %}
  {% if waiting %}
    <div class="card" style="border-left:4px solid #0b5394">
      <strong>Waiting for you</strong>
      <p class="muted" style="margin:6px 0 0">The agent fills and checks the form, then stops.
         Review each one in the browser window and click Submit there.</p>
      <ul style="margin:8px 0 0 18px; padding:0">
        {% for a in waiting %}
          <li><a href="/application/{{ a.id }}">{{ a.title }}</a> at {{ a.company }} &mdash;
              <span class="pill {{ a.status }}">{{ a.status.replace('_', ' ') }}</span>
              <span class="muted">{{ (a.notes or '')[:140] }}</span></li>
        {% endfor %}
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
        location.reload();
      }, 5000);
    </script>
  {% endif %}

  {% if runs %}
    <h2>Runs this session</h2>
    <div class="card">
      <table>
        <tr><th>URL</th><th>State</th><th>Log</th><th></th></tr>
        {% for url, r in runs.items() %}
        <tr>
          <td style="word-break:break-all">{{ url[:90] }}</td>
          <td>{{ r.state }}<br><span class="muted">started {{ r.started|local }}</span></td>
          <td><a href="/log?path={{ r.log }}" target="_blank">view</a></td>
          <td>
            {% if r.state == 'running' %}
            <form method="post" action="/reload-agent" style="display:inline">
              <button class="ghost" title="Load edited agent code into this run without restarting it">Reload agent code</button>
            </form>
            <form method="post" action="/stop" style="display:inline"
                  onsubmit="return confirm('Stop this application and close its browser? Nothing will be submitted, and you can start it again.')">
              <input type="hidden" name="url" value="{{ url }}">
              <button class="ghost">Stop</button>
            </form>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </table>
    </div>
  {% endif %}

  {% if signals %}
    <h2>Waiting for a decision</h2>
    <div class="card">
      <p class="muted">A run is waiting at a form. Review it in the browser window and
         click <strong>Submit</strong> there yourself &mdash; the agent never submits.</p>
      {% for s in signals %}
        <div style="margin-top:8px">
          <code>{{ s }}</code>
          <form method="post" action="/signal/{{ s }}" style="display:inline">
            <button class="ghost" name="decision" value="refresh">Refresh</button>
            <button class="ghost" name="decision" value="skip">Skip</button>
            <button class="ghost" name="decision" value="close">Close browser</button>
          </form>
        </div>
      {% endfor %}
    </div>
  {% endif %}

  <h2>Tracked applications ({{ apps|length }})</h2>
  <div class="card">
    <table>
      <tr><th>Company</th><th>Role</th><th>Status</th><th>Updated</th><th></th></tr>
      {% for a in apps %}
      <tr>
        <td><strong>{{ a.company }}</strong><br><span class="muted">{{ a.location or '' }}</span></td>
        <td>{{ a.title }}</td>
        <td><span class="pill {{ a.status }}">{{ a.status.replace('_',' ') }}</span></td>
        <td class="muted">{{ a.updated_at|local }}</td>
        <td><a href="/application/{{ a.id }}">details</a>
          {% if a.status != 'submitted' %}
            <form method="post" action="/resume/{{ a.id }}" style="display:inline">
              <button class="ghost" title="{{ 'Reopen the part-filled form at ' + a.last_page_url[:80] if a.last_page_url else 'Start this application again from the posting' }} -- the resume and answers already stored are reused">Resume</button>
            </form>
            <form method="post" action="/stop-application/{{ a.id }}" style="display:inline">
              <button class="ghost" title="Stop the run working on this application and close its browser. Nothing is submitted and the application is kept.">Stop</button>
            </form>
          {% endif %}
          <form method="post" action="/delete/{{ a.id }}" style="display:inline"
                onsubmit="return confirm('Delete {{ a.company }} -- {{ a.title[:60] }}?\n\nThis removes the application, its documents, its answers and its history. It cannot be undone.');">
            <button class="ghost" title="Remove this application and everything filed under it">Delete</button>
          </form>
          {% if a.status in ('ready_to_submit', 'needs_user_review') %}
            <div class="muted" style="max-width:420px">{{ (a.notes or '')[:180] }}</div>
          {% endif %}
        </td>
      </tr>
      {% else %}
      <tr><td colspan="5" class="muted">Nothing tracked yet.</td></tr>
      {% endfor %}
    </table>
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
      <tr><th>Applied via</th><td><a href="{{ a.url }}" target="_blank">{{ a.url[:80] }}</a></td></tr>
      <tr><th>Created</th><td>{{ a.created_at|local('%d %b %Y %I:%M %p') }}</td></tr>
      <tr><th>Updated</th><td>{{ a.updated_at|local('%d %b %Y %I:%M %p') }}</td></tr>
      {% if a.notes %}<tr><th>Notes</th><td>{{ a.notes }}</td></tr>{% endif %}
    </table>
  </div>

  <h2>Documents sent</h2>
  <div class="card">
    <table>
      <tr><th>Kind</th><th>File</th><th>Size</th><th>Stored</th></tr>
      {% for d in docs %}
      <tr>
        <td>{{ d.kind.replace('_',' ') }}</td>
        <td><a href="/document/{{ d.id }}" target="_blank">{{ d.filename }}</a></td>
        <td class="muted">{{ '%.1f'|format(d.byte_size/1024) }} KB</td>
        <td class="muted">{{ d.created_at|local }}</td>
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
          <tr><th>Field</th><th>On the form</th><th>Approved value</th><th>Source</th><th></th></tr>
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
      <tr><th>When</th><th>Kind</th><th>What happened</th><th>Evidence</th></tr>
      {% for e in events %}
        <tr>
          <td class="muted">{{ e.created_at|local }}</td>
          <td>{{ e.kind }}</td>
          <td>{{ (e.message or '')[:160] }}</td>
          <td>
            {% if e.screenshot_path %}<a href="/evidence?path={{ e.screenshot_path }}" target="_blank">screenshot</a>{% endif %}
            {% if e.html_path %} <a href="/evidence?path={{ e.html_path }}" target="_blank">html</a>{% endif %}
          </td>
        </tr>
      {% else %}
        <tr><td colspan="4" class="muted">No events recorded yet.</td></tr>
      {% endfor %}
    </table>
  </div>

  <h2>Answers given</h2>
  <div class="card">
    <table>
      <tr><th>Question</th><th>Answer</th><th>By</th></tr>
      {% for q in answers %}
      <tr><td>{{ q.question }}</td><td>{{ q.answer }}</td><td class="muted">{{ q.answered_by }}</td></tr>
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

