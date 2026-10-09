"""The dashboard's Progress page: the funnel from started to interview, by site, and why runs stop."""
from __future__ import annotations

from flask import Blueprint, render_template_string

import progress
import ui_shell

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
    return render_template_string(PROGRESS_HTML, f=progress.funnel(records, reached))


PROGRESS_HTML = ui_shell.page("Progress", """
<style>
.funnel { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:12px; margin:0 0 12px; }
.funnel .stat small { display:block; color:var(--muted); font-size:12px; margin-top:2px; }
</style>
<main class="wrap">
  <div class="page-head"><div>
    <h1>Progress</h1>
    <p class="sub">How far applications get. Mark what happened after you submit (Interviewing, Rejected, Offer) on
      each application's page, and the last steps fill in.</p>
  </div></div>
  <div class="funnel">
    <div class="stat tone-info"><b>{{ f.started }}</b><span>Started</span></div>
    <div class="stat tone-info"><b>{{ f.reached_review }}</b><span>Reached Review</span><small>{{ f.rate(f.reached_review, f.started) }} of started</small></div>
    <div class="stat tone-ok"><b>{{ f.submitted }}</b><span>Submitted</span><small>{{ f.rate(f.submitted, f.started) }} of started</small></div>
    <div class="stat tone-violet"><b>{{ f.replied }}</b><span>Replied</span><small>{{ f.rate(f.replied, f.submitted) }} of submitted</small></div>
    <div class="stat tone-violet"><b>{{ f.interviews }}</b><span>Reached interviews</span></div>
  </div>
  <p class="hint">{{ f.skipped }} skipped (for example, the job does not sponsor); {{ f.stuck }} stopped before Review.</p>

  <div class="grid-2">
    <div>
      <h2>By job site</h2>
      <div class="card flush">
        <table>
          <thead><tr><th>Site</th><th class="num">Started</th><th class="num">Reached Review</th><th class="num">Submitted</th></tr></thead>
          {% for site, n in f.by_site.items() %}
          <tr><td>{{ site }}</td><td class="num">{{ n.started }}</td>
              <td class="num">{{ n.reached_review }} <span class="muted">({{ f.rate(n.reached_review, n.started) }})</span></td>
              <td class="num">{{ n.submitted }}</td></tr>
          {% else %}<tr><td colspan="4" class="muted">No applications yet.</td></tr>
          {% endfor %}
        </table>
      </div>
    </div>
    <div>
      <h2>Why runs stopped before Review</h2>
      <div class="card flush">
        <table>
          <thead><tr><th>Reason</th><th class="num">Times</th></tr></thead>
          {% for reason, n in f.stop_reasons %}<tr><td>{{ reason }}</td><td class="num">{{ n }}</td></tr>
          {% else %}<tr><td colspan="2" class="muted">None.</td></tr>{% endfor %}
        </table>
      </div>
    </div>
  </div>
</main>
""")
