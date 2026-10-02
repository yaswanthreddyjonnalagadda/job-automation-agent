"""The dashboard's Progress page: the funnel from started to interview, by site, and why runs stop."""
from __future__ import annotations

from flask import Blueprint, render_template_string

import progress
import run_metrics
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
    return render_template_string(PROGRESS_HTML, f=progress.funnel(records, reached), m=run_metrics.summary())


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

  <h2>How each run went</h2>
  <p class="sub">Counted run by run since 1 October: a run that reached Review without stopping for you is the agent doing
    the work. Shown by job site and by agent version, so a change that makes things worse shows up.</p>
  <div class="funnel">
    <div class="stat tone-info"><b>{{ m.all.runs }}</b><span>Runs</span></div>
    <div class="stat tone-ok"><b>{{ m.all.rate_without_you }}</b><span>Reached Review without you</span><small>{{ m.all.without_you }} of {{ m.all.runs }}</small></div>
    <div class="stat tone-info"><b>{{ m.all.stops_per_run }}</b><span>Stops for you per run</span></div>
    <div class="stat tone-info"><b>{{ m.all.minutes_per_run }}</b><span>Minutes per run</span><small>{{ m.all.ai_calls_per_run }} AI calls ({{ m.all.ai_failure_rate }} failed)</small></div>
    <div class="stat tone-violet"><b>{{ m.all.unknown_outcomes }}</b><span>Unknown outcomes</span><small>{{ m.all.resumes_on_the_application }} of {{ m.all.resumes }} resumes found the application</small></div>
  </div>
  <div class="grid-2">
    {% for title, groups in (("By job site", m.by_site), ("By agent version", m.by_code)) %}
    <div>
      <h2>{{ title }}</h2>
      <div class="card flush">
        <table>
          <thead><tr><th></th><th class="num">Runs</th><th class="num">Review</th><th class="num">Without you</th><th class="num">Stops</th><th class="num">Min</th></tr></thead>
          {% for name, g in groups.items() %}
          <tr><td>{{ name }}</td><td class="num">{{ g.runs }}</td><td class="num">{{ g.rate_review }}</td>
              <td class="num">{{ g.rate_without_you }}</td><td class="num">{{ g.stops_per_run }}</td><td class="num">{{ g.minutes_per_run }}</td></tr>
          {% else %}<tr><td colspan="6" class="muted">No runs counted yet.</td></tr>
          {% endfor %}
        </table>
      </div>
    </div>
    {% endfor %}
  </div>
  <h2>Why runs stopped for you</h2>
  <div class="card flush">
    <table>
      <thead><tr><th>Reason</th><th class="num">Times</th></tr></thead>
      {% for reason, n in m.stop_reasons %}<tr><td>{{ reason }}</td><td class="num">{{ n }}</td></tr>
      {% else %}<tr><td colspan="2" class="muted">None yet.</td></tr>{% endfor %}
    </table>
  </div>
</main>
""")
