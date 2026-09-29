"""The dashboard's Progress page: the funnel from started to interview, by site, and why runs stop."""
from __future__ import annotations

from flask import Blueprint, render_template_string

import progress

progress_pages = Blueprint("progress_pages", __name__)


@progress_pages.get("/progress")
def progress_page():
    import web_ui
    tracker = web_ui.get_tracker()
    records = tracker.list_all()
    reached = set()
    if hasattr(tracker, "events"):
        for record in records:
            if record.status == "needs_user_review":
                try:
                    if any(e.get("kind") == "auto_submit" for e in tracker.events(record.dedup_key, limit=50)):
                        reached.add(record.dedup_key)
                except Exception:
                    continue
    return render_template_string(PROGRESS_HTML, css=web_ui.BASE_CSS, f=progress.funnel(records, reached))


PROGRESS_HTML = """
<!doctype html><meta charset="utf-8"><title>Progress</title>
<style>{{ css|safe }}
.funnel { display:grid; grid-template-columns:repeat(auto-fit, minmax(130px, 1fr)); gap:12px; margin:14px 0 20px; }
.step { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px; }
.step b { display:block; font-size:28px; }
.step span { color:var(--muted); font-size:13px; }
table { width:100%; border-collapse:collapse; }
td, th { text-align:left; padding:7px 6px; border-bottom:1px solid var(--line); }
.hint { color:var(--muted); font-size:13px; }
</style>
<div class="wrap">
  <p><a href="/">&larr; Back to applications</a></p>
  <h1>Progress</h1>
  <p class="sub">How far applications get. Mark what happened after you submit (Interviewing, Rejected, Offer) on
    each application's page, and the last steps fill in.</p>
  <div class="funnel">
    <div class="step"><b>{{ f.started }}</b><span>started</span></div>
    <div class="step"><b>{{ f.reached_review }}</b><span>reached Review ({{ f.rate(f.reached_review, f.started) }})</span></div>
    <div class="step"><b>{{ f.submitted }}</b><span>submitted ({{ f.rate(f.submitted, f.started) }})</span></div>
    <div class="step"><b>{{ f.replied }}</b><span>replied ({{ f.rate(f.replied, f.submitted) }} of submitted)</span></div>
    <div class="step"><b>{{ f.interviews }}</b><span>interviews</span></div>
  </div>
  <p class="hint">{{ f.skipped }} skipped (for example, the job does not sponsor); {{ f.stuck }} stopped before Review.</p>

  <h2>By job site</h2>
  <table>
    <tr><th>Site</th><th>Started</th><th>Reached Review</th><th>Submitted</th></tr>
    {% for site, n in f.by_site.items() %}
    <tr><td>{{ site }}</td><td>{{ n.started }}</td>
        <td>{{ n.reached_review }} ({{ f.rate(n.reached_review, n.started) }})</td><td>{{ n.submitted }}</td></tr>
    {% endfor %}
  </table>

  <h2>Why runs stopped before Review</h2>
  {% if f.stop_reasons %}
  <table>
    <tr><th>Reason</th><th>Times</th></tr>
    {% for reason, n in f.stop_reasons %}<tr><td>{{ reason }}</td><td>{{ n }}</td></tr>{% endfor %}
  </table>
  {% else %}<p class="hint">None.</p>{% endif %}
</div>
"""
