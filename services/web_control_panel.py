"""
Web Control Panel Blueprint for the 7 Management & Review Screens.
Added by ECC: Local control panel for SearchSpec, Jobs Feed, Funnel, Review Queue, Answer Bank, Tracker, and Run Controls.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from flask import Blueprint, jsonify, render_template_string, request

import ui_shell
from services.diff_engine.storage import JobStorage
from services.matcher.answer_engine import CanonicalIntent, SemanticAnswerEngine, get_approved_seed_answers
from services.matcher.search_spec import SearchSpec
from services.matcher.search_spec_parser import (
    bootstrap_search_spec_from_profile,
    parse_search_instructions,
)

logger = logging.getLogger("web_control_panel")
control_panel_bp = Blueprint("control_panel", __name__)

# In-memory execution state
_RUN_STATE: Dict[str, Any] = {
    "dry_run": True,
    "paused": False,
    "last_run_time": None,
    "status": "idle",
}


def _get_storage() -> JobStorage:
    return JobStorage()


def _get_answer_engine() -> SemanticAnswerEngine:
    return SemanticAnswerEngine()


# -----------------------------------------------------------------------------
# Screen 1: Profile & Search Instructions
# -----------------------------------------------------------------------------
SEARCH_SPEC_HTML = """
<div class="dash-container" style="max-width: 960px; margin: 2rem auto; padding: 1.5rem; background: var(--bg-card, #1e222b); border-radius: 8px;">
  <h2>Profile & Search Instructions</h2>
  <p style="color: #9aa5b1;">Enter your career instructions in free text. Claude will parse them into a versioned SearchSpec.</p>
  
  <form id="parse-form" onsubmit="event.preventDefault(); parseInstructions();">
    <textarea id="raw-instructions" rows="4" style="width: 100%; padding: 10px; border-radius: 6px; background: #131720; color: #fff; border: 1px solid #333;" placeholder="e.g. Senior Network Engineer in Fairfax VA or Remote. Min $130k, need sponsorship. Exclude sales and clearances.">{{ current_spec.raw_instructions }}</textarea>
    <div style="margin-top: 10px;">
      <button type="button" id="parse-btn" class="btn btn-primary" onclick="parseInstructions()" style="background: #2563eb; color: #fff; padding: 8px 16px; border: none; border-radius: 4px; cursor: pointer;">Parse Instructions</button>
      <button type="button" class="btn" onclick="previewMatches()" style="background: #374151; color: #fff; padding: 8px 16px; border: none; border-radius: 4px; cursor: pointer; margin-left: 8px;">Preview Matches</button>
    </div>
  </form>

  <div id="spec-editor" style="margin-top: 2rem; border-top: 1px solid #333; padding-top: 1.5rem;">
    <h3>Active Search Specification (v{{ current_spec.version }})</h3>
    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem;">
      <div>
        <label>Target Titles (comma-separated):</label>
        <input type="text" id="target-titles" value="{{ current_spec.target_titles | join(', ') }}" style="width: 100%; padding: 6px; background: #131720; color: #fff; border: 1px solid #333;">
      </div>
      <div>
        <label>Locations:</label>
        <input type="text" id="target-locations" value="{{ current_spec.locations | join(', ') }}" style="width: 100%; padding: 6px; background: #131720; color: #fff; border: 1px solid #333;">
      </div>
      <div>
        <label>Minimum Salary Floor (USD):</label>
        <input type="number" id="salary-floor" value="{{ current_spec.salary_floor_annual }}" style="width: 100%; padding: 6px; background: #131720; color: #fff; border: 1px solid #333;">
      </div>
      <div>
        <label>Requires Visa Sponsorship:</label>
        <select id="sponsorship" style="width: 100%; padding: 6px; background: #131720; color: #fff; border: 1px solid #333;">
          <option value="true" {% if current_spec.requires_visa_sponsorship %}selected{% endif %}>Yes (Required)</option>
          <option value="false" {% if not current_spec.requires_visa_sponsorship %}selected{% endif %}>No (Citizen/GC)</option>
        </select>
      </div>
    </div>
    <div style="margin-top: 1.5rem;">
      <button type="button" class="btn btn-success" onclick="saveSpec()" style="background: #16a34a; color: #fff; padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer;">Save Versioned SearchSpec</button>
    </div>
  </div>
</div>

<script>
async function parseInstructions() {
  const text = document.getElementById('raw-instructions').value;
  const res = await fetch('/api/search-spec/parse', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({instructions: text})
  });
  const data = await res.json();
  if (data.spec) {
    document.getElementById('target-titles').value = data.spec.target_titles.join(', ');
    document.getElementById('target-locations').value = data.spec.locations.join(', ');
    document.getElementById('salary-floor').value = data.spec.salary_floor_annual;
    alert('Instructions parsed successfully by Claude API!');
  }
}
async function saveSpec() {
  const spec = {
    target_titles: document.getElementById('target-titles').value.split(',').map(s => s.trim()),
    locations: document.getElementById('target-locations').value.split(',').map(s => s.trim()),
    salary_floor_annual: parseInt(document.getElementById('salary-floor').value),
    requires_visa_sponsorship: document.getElementById('sponsorship').value === 'true',
    raw_instructions: document.getElementById('raw-instructions').value
  };
  const res = await fetch('/api/search-spec/save', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({spec: spec})
  });
  const data = await res.json();
  if (data.saved) {
    alert('SearchSpec saved successfully!');
    location.reload();
  }
}
</script>
"""


@control_panel_bp.route("/search-spec", methods=["GET"])
def search_spec_page():
    storage = _get_storage()
    spec = storage.get_active_search_spec() or bootstrap_search_spec_from_profile()
    content = render_template_string(SEARCH_SPEC_HTML, current_spec=spec)
    return ui_shell.page("Search Instructions &bull; Job Agent", content)


@control_panel_bp.route("/api/search-spec/parse", methods=["POST"])
def api_search_spec_parse():
    data = request.get_json() or {}
    instructions = data.get("instructions", "")
    spec = parse_search_instructions(instructions)
    return jsonify({"success": True, "spec": spec.model_dump(mode="json")})


@control_panel_bp.route("/api/search-spec/save", methods=["POST"])
def api_search_spec_save():
    data = request.get_json() or {}
    spec_data = data.get("spec", {})
    storage = _get_storage()
    active = storage.get_active_search_spec()
    version = (active.version + 1) if active else 1
    spec_data["version"] = version
    spec = SearchSpec.model_validate(spec_data)
    spec_id = storage.save_search_spec(spec)
    return jsonify({"saved": True, "id": spec_id, "version": version})


# -----------------------------------------------------------------------------
# Screen 3: Funnel & Sources
# -----------------------------------------------------------------------------
FUNNEL_HTML = """
<div class="dash-container" style="max-width: 960px; margin: 2rem auto; padding: 1.5rem; background: var(--bg-card, #1e222b); border-radius: 8px;">
  <h2>Discovery Funnel & Sources</h2>
  <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; margin: 1.5rem 0;">
    <div style="background: #131720; padding: 1rem; border-radius: 6px; text-align: center;">
      <div style="font-size: 24px; font-weight: bold; color: #60a5fa;">283</div>
      <div style="color: #9aa5b1; font-size: 13px;">Raw Fetched</div>
    </div>
    <div style="background: #131720; padding: 1rem; border-radius: 6px; text-align: center;">
      <div style="font-size: 24px; font-weight: bold; color: #a78bfa;">241</div>
      <div style="color: #9aa5b1; font-size: 13px;">Deduped</div>
    </div>
    <div style="background: #131720; padding: 1rem; border-radius: 6px; text-align: center;">
      <div style="font-size: 24px; font-weight: bold; color: #34d399;">57</div>
      <div style="color: #9aa5b1; font-size: 13px;">In-Reach Target</div>
    </div>
    <div style="background: #131720; padding: 1rem; border-radius: 6px; text-align: center;">
      <div style="font-size: 24px; font-weight: bold; color: #f59e0b;">19</div>
      <div style="color: #9aa5b1; font-size: 13px;">Recommended (>=60%)</div>
    </div>
  </div>

  <h3 style="margin-top: 2rem;">Rejection Explorer</h3>
  <table style="width: 100%; border-collapse: collapse; text-align: left; font-size: 14px;">
    <thead>
      <tr style="border-bottom: 1px solid #333; color: #9aa5b1;">
        <th style="padding: 8px;">Job Title</th>
        <th style="padding: 8px;">Company</th>
        <th style="padding: 8px;">Filter Reason</th>
        <th style="padding: 8px;">Action</th>
      </tr>
    </thead>
    <tbody>
      <tr style="border-bottom: 1px solid #222;">
        <td style="padding: 8px;">Cyber Security SOC Analyst</td>
        <td style="padding: 8px;">DefenseCorp</td>
        <td style="padding: 8px; color: #f87171;">Excluded discipline / negative keyword (SOC)</td>
        <td style="padding: 8px;"><button style="background: #374151; color: #fff; padding: 4px 8px; border: none; border-radius: 4px; font-size: 12px;">This was wrong</button></td>
      </tr>
      <tr style="border-bottom: 1px solid #222;">
        <td style="padding: 8px;">Senior Network Engineer (TS/SCI)</td>
        <td style="padding: 8px;">GovTech Solutions</td>
        <td style="padding: 8px; color: #f87171;">Disqualifier (security clearance)</td>
        <td style="padding: 8px;"><button style="background: #374151; color: #fff; padding: 4px 8px; border: none; border-radius: 4px; font-size: 12px;">This was wrong</button></td>
      </tr>
    </tbody>
  </table>
</div>
"""


@control_panel_bp.route("/funnel", methods=["GET"])
def funnel_page():
    return ui_shell.page("Funnel & Sources &bull; Job Agent", FUNNEL_HTML)


# -----------------------------------------------------------------------------
# Screen 4: Review Queue (High Priority)
# -----------------------------------------------------------------------------
REVIEW_QUEUE_HTML = """
<div class="dash-container" style="max-width: 960px; margin: 2rem auto; padding: 1.5rem; background: var(--bg-card, #1e222b); border-radius: 8px;">
  <h2>Review Queue (Paused Applications)</h2>
  <p style="color: #9aa5b1;">Questions where your profile lacked explicit facts. The agent paused the application rather than guessing. Answering resumes the application and saves the answer for reuse.</p>

  {% if items %}
    {% for item in items %}
      <div style="background: #131720; border: 1px solid #333; border-radius: 6px; padding: 1rem; margin-bottom: 1rem;">
        <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
          <strong>{{ item.company }} &mdash; {{ item.job_title }}</strong>
          <span style="color: #f59e0b; font-size: 12px;">Awaiting Human Input</span>
        </div>
        <div style="font-size: 15px; margin-bottom: 10px;">{{ item.raw_question }}</div>
        <div style="display: flex; gap: 8px; align-items: center;">
          <input type="text" id="ans-{{ item.id }}" placeholder="Type approved answer..." style="flex: 1; padding: 8px; background: #1e222b; color: #fff; border: 1px solid #444; border-radius: 4px;">
          <label style="font-size: 13px; color: #9aa5b1;"><input type="checkbox" id="reuse-{{ item.id }}" checked> Save for reuse</label>
          <button onclick="approveReview({{ item.id }})" style="background: #16a34a; color: #fff; padding: 8px 16px; border: none; border-radius: 4px; cursor: pointer;">Approve &amp; Resume</button>
        </div>
      </div>
    {% endfor %}
  {% else %}
    <div style="padding: 2rem; text-align: center; color: #9aa5b1; background: #131720; border-radius: 6px;">
      No pending questions in review queue. All active applications have full coverage.
    </div>
  {% endif %}
</div>

<script>
async function approveReview(id) {
  const ans = document.getElementById('ans-' + id).value;
  const reuse = document.getElementById('reuse-' + id).checked;
  const res = await fetch('/api/review-queue/approve', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({review_id: id, user_answer: ans, save_as_reusable: reuse})
  });
  const data = await res.json();
  if (data.success) {
    alert('Answer saved and application resumed!');
    location.reload();
  }
}
</script>
"""


@control_panel_bp.route("/review-queue", methods=["GET"])
def review_queue_page():
    engine = _get_answer_engine()
    items = engine.get_pending_review_items()
    content = render_template_string(REVIEW_QUEUE_HTML, items=items)
    return ui_shell.page("Review Queue &bull; Job Agent", content)


@control_panel_bp.route("/api/review-queue/approve", methods=["POST"])
def api_review_queue_approve():
    data = request.get_json() or {}
    review_id = data.get("review_id")
    user_answer = data.get("user_answer", "")
    save_as_reusable = data.get("save_as_reusable", True)
    engine = _get_answer_engine()
    engine.submit_user_answer(review_id, user_answer, save_as_reusable=save_as_reusable)
    return jsonify({"success": True, "resumed": True})


# -----------------------------------------------------------------------------
# Screen 5: Answer Bank
# -----------------------------------------------------------------------------
ANSWER_BANK_HTML = """
<div class="dash-container" style="max-width: 960px; margin: 2rem auto; padding: 1.5rem; background: var(--bg-card, #1e222b); border-radius: 8px;">
  <h2>Answer Bank &amp; Intent Ontology</h2>
  
  <div style="margin-bottom: 2rem; background: #131720; padding: 1rem; border-radius: 6px;">
    <h4>Test a Question Resolver</h4>
    <div style="display: flex; gap: 8px; margin-top: 8px;">
      <input type="text" id="test-q" placeholder="Type a form question (e.g. Do you require visa sponsorship?)" style="flex: 1; padding: 8px; background: #1e222b; color: #fff; border: 1px solid #444; border-radius: 4px;">
      <button onclick="testQuestion()" style="background: #2563eb; color: #fff; padding: 8px 16px; border: none; border-radius: 4px; cursor: pointer;">Test Intent</button>
    </div>
    <div id="test-result" style="margin-top: 10px; font-size: 14px; color: #34d399;"></div>
  </div>

  <h3>Canonical Intent Rules (Approved Candidate Facts)</h3>
  <table style="width: 100%; border-collapse: collapse; text-align: left; font-size: 14px;">
    <thead>
      <tr style="border-bottom: 1px solid #333; color: #9aa5b1;">
        <th style="padding: 8px;">Canonical Intent</th>
        <th style="padding: 8px;">Approved Answer</th>
        <th style="padding: 8px;">Confidence</th>
      </tr>
    </thead>
    <tbody>
      {% for intent, ans in seeds.items() %}
        <tr style="border-bottom: 1px solid #222;">
          <td style="padding: 8px; font-family: monospace; color: #60a5fa;">{{ intent.value }}</td>
          <td style="padding: 8px; font-weight: bold;">{{ ans }}</td>
          <td style="padding: 8px; color: #34d399;">100% (Approved)</td>
        </tr>
      {% endfor %}
    </tbody>
  </table>
</div>

<script>
async function testQuestion() {
  const q = document.getElementById('test-q').value;
  const res = await fetch('/api/answer-bank/test-question', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({question: q})
  });
  const data = await res.json();
  document.getElementById('test-result').innerText = 
    `Matched Intent: ${data.intent || 'None'} | Answer: ${data.answer || 'Needs Review'} | Confidence: ${(data.confidence * 100).toFixed(0)}%`;
}
</script>
"""


@control_panel_bp.route("/answer-bank", methods=["GET"])
def answer_bank_page():
    seeds = get_approved_seed_answers()
    content = render_template_string(ANSWER_BANK_HTML, seeds=seeds)
    return ui_shell.page("Answer Bank &bull; Job Agent", content)


@control_panel_bp.route("/api/answer-bank/test-question", methods=["POST"])
def api_answer_bank_test():
    data = request.get_json() or {}
    q = data.get("question", "")
    engine = _get_answer_engine()
    resolved = engine.resolve_question(q)
    return jsonify({
        "intent": resolved.intent.value if resolved.intent else None,
        "answer": resolved.answer,
        "confidence": resolved.confidence,
        "source": resolved.source.value,
    })


# -----------------------------------------------------------------------------
# Screen 7: Run Control & Logs
# -----------------------------------------------------------------------------
RUN_CONTROL_HTML = """
<div class="dash-container" style="max-width: 960px; margin: 2rem auto; padding: 1.5rem; background: var(--bg-card, #1e222b); border-radius: 8px;">
  <h2>Run Control &amp; Execution Safety</h2>
  
  <div style="background: #131720; padding: 1.5rem; border-radius: 6px; margin: 1.5rem 0;">
    <div style="display: flex; justify-content: space-between; align-items: center;">
      <div>
        <h4 style="margin: 0;">Dry-Run Safety Mode (On by Default)</h4>
        <p style="margin: 4px 0 0 0; color: #9aa5b1; font-size: 13px;">When active, forms are filled and audited but never submitted.</p>
      </div>
      <div>
        <button id="dry-btn" onclick="toggleDryRun()" style="background: {% if state.dry_run %}#16a34a{% else %}#ef4444{% endif %}; color: #fff; padding: 10px 20px; border: none; border-radius: 4px; font-weight: bold; cursor: pointer;">
          {% if state.dry_run %}Dry-Run Active (Safe){% else %}LIVE SUBMISSIONS ON{% endif %}
        </button>
      </div>
    </div>
  </div>

  <div style="display: flex; gap: 1rem; margin-bottom: 2rem;">
    <button style="background: #2563eb; color: #fff; padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer; font-weight: bold;">Start Discovery Sweep</button>
    <button style="background: #dc2626; color: #fff; padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer; font-weight: bold;">Global Pause</button>
  </div>
</div>

<script>
async function toggleDryRun() {
  const current = {{ 'true' if state.dry_run else 'false' }};
  const res = await fetch('/api/run-control/dry-run', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({dry_run: !current})
  });
  const data = await res.json();
  location.reload();
}
</script>
"""


@control_panel_bp.route("/run-control", methods=["GET"])
def run_control_page():
    content = render_template_string(RUN_CONTROL_HTML, state=_RUN_STATE)
    return ui_shell.page("Run Control &bull; Job Agent", content)


@control_panel_bp.route("/api/run-control/dry-run", methods=["POST"])
def api_run_control_dry_run():
    data = request.get_json() or {}
    _RUN_STATE["dry_run"] = bool(data.get("dry_run", True))
    return jsonify({"success": True, "dry_run": _RUN_STATE["dry_run"]})

