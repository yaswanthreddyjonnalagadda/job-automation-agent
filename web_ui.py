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
import os
import signal
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, abort, redirect, render_template_string, request, send_file, url_for

from db import get_tracker

BASE_DIR = Path(__file__).resolve().parent
app = Flask(__name__)

# Applications launched from this UI, so their progress can be shown. Keyed by
# the URL that started them.
_RUNS: dict[str, dict] = {}
_RUNS_LOCK = threading.Lock()


# ----------------------------------------------------------------------
# Launching an application
# ----------------------------------------------------------------------
def _run_apply(url: str) -> None:
    """Runs apply.py for one URL, capturing output for the UI to display."""
    log_path = BASE_DIR / "logs" / f"ui_run_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with _RUNS_LOCK:
        _RUNS[url] = {"state": "running", "log": str(log_path), "started": datetime.now(timezone.utc),
                      "proc": None, "stopping": False}

    try:
        with open(log_path, "w", encoding="utf-8") as fh:
            # Popen rather than run() so /stop can reach the process. On POSIX
            # it gets its own session so the whole group can be signalled.
            proc = subprocess.Popen(
                [sys.executable, "apply.py", url],
                cwd=str(BASE_DIR), stdout=fh, stderr=subprocess.STDOUT, text=True,
                start_new_session=(os.name != "nt"),
            )
            with _RUNS_LOCK:
                _RUNS[url]["proc"] = proc
            proc.wait()
        state = "finished" if proc.returncode == 0 else f"failed (exit {proc.returncode})"
    except Exception as exc:
        state = f"failed ({exc})"

    with _RUNS_LOCK:
        if _RUNS[url]["stopping"]:
            state = "stopped by you"
        _RUNS[url]["state"] = state
        _RUNS[url]["proc"] = None


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
        if not run or run["state"] != "running" or run["proc"] is None:
            return redirect(url_for("index", error="That run is not running."))
        run["stopping"] = True
        proc = run["proc"]
    _kill_tree(proc)
    # Leftover signal files would otherwise sit in "Waiting for a decision".
    for leftover in (BASE_DIR / "data").glob("_signal_*.txt"):
        leftover.unlink(missing_ok=True)
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
    with _RUNS_LOCK:
        runs = dict(_RUNS)
    return render_template_string(
        INDEX_HTML, apps=apps, runs=runs, signals=signals, error=request.args.get("error"),
    )


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
    return render_template_string(DETAIL_HTML, a=record, docs=docs, answers=answers)


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
    app.run(host="127.0.0.1", port=5000, debug=False)
