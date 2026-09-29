"""The dashboard pages for setting up a profile and keeping saved answers.

  /setup    first run: upload a resume; the agent drafts the profile from it
  /profile  check, complete and save the profile (also how it is changed later)
  /answers  the saved answers, and the questions the agent had to leave, to answer once

The logic is in profile_setup.py; this file only shows it. web_ui.py registers the blueprint and sends a person
without a profile here first.
"""
from __future__ import annotations

import logging
from pathlib import Path

from flask import Blueprint, redirect, render_template_string, request, url_for

import config
import profile_setup
import ui_shell

logger = logging.getLogger(__name__)

setup_pages = Blueprint("setup_pages", __name__)

RESUME_TYPES = (".pdf", ".docx")
MAX_RESUME_BYTES = 10 * 1024 * 1024


def _save_env(values: dict[str, str]) -> None:
    import web_ui
    web_ui._save_env_values(values)


def _drafting_ai():
    """The first AI the person has a key for, as ask(system, user, max_tokens), or None."""
    try:
        from apply_flow import _make_tailor_client
        cfg = config.get_app_config()
        providers = config.available_tailor_providers(cfg)
    except Exception as exc:
        logger.info("No AI for drafting the profile: %s", exc)
        return None
    for provider in providers:
        try:
            client = _make_tailor_client(provider, cfg)
        except Exception:
            continue

        def ask(system, user, tokens, client=client):
            return client._call(system=system, user_message=user, max_tokens=tokens)
        return ask
    return None


@setup_pages.route("/setup", methods=["GET", "POST"])
def setup():
    """Step 1: the resume. The draft it gives goes straight into the profile form."""
    if request.method == "GET":
        return render_template_string(SETUP_HTML, error=request.args.get("error"),
                                      has_profile=not profile_setup.needs_setup())
    upload = request.files.get("resume")
    if upload is None or not upload.filename:
        return redirect(url_for("setup_pages.setup", error="Choose your resume file first."))
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in RESUME_TYPES:
        return redirect(url_for("setup_pages.setup", error="The resume must be a .pdf or .docx file."))
    data = upload.read(MAX_RESUME_BYTES + 1)
    if len(data) > MAX_RESUME_BYTES:
        return redirect(url_for("setup_pages.setup", error="That file is over 10 MB."))
    target = config.DATA_DIR / f"resume{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    _save_env({"RESUME_PATH": str(target)})
    try:
        from resume_parser import extract_text
        text = extract_text(target)
    except Exception as exc:
        return redirect(url_for("setup_pages.setup", error=f"Could not read that resume: {exc}"))
    draft = profile_setup.draft_from_resume(text, _drafting_ai())
    values = {**profile_setup.current_values(), **draft}
    if not profile_setup.needs_setup():
        values = {**draft, **{k: v for k, v in profile_setup.current_values().items() if v not in ("", (), 0, False)}}
    return _profile_page(values, note=f"Read from {upload.filename}. Check everything, fill in the rest, and save.",
                         drafted=set(draft))


def _profile_page(values, note="", error="", drafted=(), saved=False):
    shown = {}
    for name, value in values.items():
        field = profile_setup.FIELDS.get(name)
        if field and field.kind == "list":
            value = ", ".join(value or ())
        elif field and field.kind == "lines":
            value = "\n".join(" | ".join(row) for row in (value or ()))
        elif field and field.kind == "number":
            value = value or ""
        shown[name] = value
    return render_template_string(PROFILE_HTML, sections=profile_setup.SECTIONS, values=shown,
                                  note=note, error=error, drafted=set(drafted), saved=saved,
                                  first_time=profile_setup.needs_setup())


@setup_pages.route("/profile", methods=["GET", "POST"])
def profile():
    if request.method == "GET":
        return _profile_page(profile_setup.current_values(), saved=request.args.get("saved") == "1")
    values = profile_setup.values_from_form(request.form)
    first_time = profile_setup.needs_setup()
    try:
        profile_setup.save_profile(values)
    except ValueError as exc:
        return _profile_page(values, error=str(exc))
    if first_time:
        return redirect(url_for("setup_pages.answers", welcome="1"))
    return redirect(url_for("setup_pages.profile", saved="1"))


@setup_pages.route("/answers", methods=["GET", "POST"])
def answers():
    if request.method == "GET":
        return render_template_string(ANSWERS_HTML, saved_rows=profile_setup.saved_answers(),
                                      waiting=profile_setup.waiting_questions(),
                                      saved=request.args.get("saved") == "1",
                                      welcome=request.args.get("welcome") == "1")
    changes, removed = {}, []
    for key in request.form.getlist("key"):
        changes[key] = request.form.get(f"answer::{key}", "")
        if request.form.get(f"remove::{key}"):
            removed.append(key)
    new_question = (request.form.get("new_question") or "").strip()
    if new_question:
        changes[new_question] = request.form.get("new_answer", "")
    profile_setup.save_answers(changes, tuple(removed))
    return redirect(url_for("setup_pages.answers", saved="1"))


SETUP_HTML = ui_shell.page("Set up your profile", """
<style>
.setup { max-width:720px; }
.steps { display:flex; gap:8px; flex-wrap:wrap; margin:0 0 18px; padding:0; list-style:none; }
.steps li { padding:4px 12px; border-radius:99px; background:var(--grey-soft); color:var(--muted); font-size:13px; font-weight:500; }
.steps li.now { background:var(--accent-soft); color:var(--accent); font-weight:600; }
.drop { display:block; border:2px dashed var(--line); border-radius:var(--radius); padding:22px; text-align:center;
        background:var(--surface-2); margin:0 0 10px; }
.drop input { margin-top:10px; }
</style>
<main class="wrap setup">
  <div class="page-head"><div>
    <h1>Set up your profile</h1>
    <p class="sub">The agent fills every application from your profile. Start with your resume: it is read on your
      computer, and it is also the resume the agent attaches when none can be tailored for a job.</p>
  </div></div>
  <ol class="steps"><li class="now">1. Resume</li><li>2. Check your profile</li><li>3. Your saved answers</li></ol>
  {% if error %}<div class="err" role="alert">{{ error }}</div>{% endif %}
  <div class="card">
    <form method="post" action="/setup" enctype="multipart/form-data">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <label class="drop" for="resume"><b>Your resume</b> <span class="muted">(.pdf or .docx)</span><br>
        <input id="resume" type="file" name="resume" accept=".pdf,.docx" required></label>
      <p class="hint">If an AI key is set in Settings, it is used once to copy your work history and education out
        of the resume. Nothing is guessed: whatever the resume does not say, you fill in next.</p>
      <p style="margin-bottom:0"><button type="submit">Read my resume</button></p>
    </form>
  </div>
  {% if has_profile %}<p class="hint">Or <a href="/profile">edit your profile</a> without a new resume.</p>{% endif %}
</main>
""")

PROFILE_HTML = ui_shell.page("Your profile", """
<style>
.profile { max-width:860px; }
.profile fieldset { border:1px solid var(--line); border-radius:var(--radius); padding:16px 20px 20px; margin:0 0 16px;
  background:var(--surface); box-shadow:var(--shadow); }
.profile legend { font-weight:700; padding:0 6px; font-size:15px; }
.profile .about { color:var(--muted); font-size:13px; margin:0 0 6px; }
.profile label { display:block; margin:14px 0 6px; font-weight:600; }
.profile .check label { display:inline; font-weight:600; margin-left:6px; }
.profile .check { margin:14px 0 0; }
.drafted { background:var(--accent-soft) !important; }
.req { color:var(--bad); }
.bar { position:sticky; bottom:0; background:var(--bg); padding:12px 0; border-top:1px solid var(--line);
       display:flex; gap:12px; align-items:center; }
</style>
<main class="wrap profile">
  <div class="page-head">
    <div>
      <h1>{% if first_time %}Check your profile{% else %}Your profile{% endif %}</h1>
      <p class="sub">Every application is filled from this, first. Leave a box blank and the agent asks the AI or you
        instead of guessing. It is saved only on this computer, in <code>data/profile.json</code>.</p>
    </div>
    {% if not first_time %}<a href="/setup"><button class="ghost" type="button">Read a new resume</button></a>{% endif %}
  </div>
  {% if note %}<div class="ok">{{ note }}{% if drafted %} Boxes filled from your resume are shaded.{% endif %}</div>{% endif %}
  {% if saved %}<div class="ok">Profile saved. The next application uses it.</div>{% endif %}
  {% if error %}<div class="err" role="alert">{{ error }}</div>{% endif %}
  <form method="post" action="/profile">
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
  {% for title, about, fields in sections %}
    <fieldset><legend>{{ title }}</legend>
      <p class="about">{{ about }}</p>
      {% for f in fields %}
        {% set v = values.get(f.name) %}
        {% if f.kind == "bool" %}
          <div class="check"><input type="checkbox" id="{{ f.name }}" name="{{ f.name }}" {% if v %}checked{% endif %}>
            <label for="{{ f.name }}">{{ f.label }}</label></div>
        {% else %}
          <label for="{{ f.name }}">{{ f.label }}{% if f.required %} <span class="req">*</span>{% endif %}</label>
          {% if f.kind == "choice" %}
            <select id="{{ f.name }}" name="{{ f.name }}" class="{% if f.name in drafted %}drafted{% endif %}">
              {% for c in f.choices %}<option value="{{ c }}" {% if c == v %}selected{% endif %}>{{ c or "Ask me each time" }}</option>{% endfor %}
            </select>
          {% elif f.kind == "lines" %}
            <textarea id="{{ f.name }}" name="{{ f.name }}" rows="3" class="{% if f.name in drafted %}drafted{% endif %}">{{ v or "" }}</textarea>
          {% else %}
            <input type="{{ 'email' if f.kind == 'email' else 'number' if f.kind == 'number' else 'text' }}"
                   id="{{ f.name }}" name="{{ f.name }}" value="{{ v if v is not none else '' }}"
                   {% if f.required %}required{% endif %} class="{% if f.name in drafted %}drafted{% endif %}">
          {% endif %}
        {% endif %}
        {% if f.hint %}<p class="hint">{{ f.hint }}</p>{% endif %}
      {% endfor %}
    </fieldset>
  {% endfor %}
  <div class="bar"><button type="submit">Save my profile</button><span class="hint">Saved on this computer only.</span></div>
  </form>
</main>
""")

ANSWERS_HTML = ui_shell.page("Your saved answers", """
<style>
.answers { max-width:960px; }
.answers td { vertical-align:top; }
.answers td input[type=text] { padding:8px 10px; }
.waiting td { background:var(--warn-soft); }
.add { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.add label { font-weight:600; display:block; }
.add label input { margin-top:6px; font-weight:400; }
@media (max-width:760px) { .add { grid-template-columns:1fr; } }
</style>
<main class="wrap answers">
  <div class="page-head"><div>
    <h1>Your saved answers</h1>
    <p class="sub">After your profile, the agent answers from these. Any question it had to leave for you shows up
      here: answer it once and it is used on every form that asks it. Saved only on this computer, in
      <code>data/profile_answers.json</code>.</p>
  </div></div>
  {% if welcome %}<div class="ok">Your profile is saved. Add answers to questions you expect, or start applying:
    <a href="/">go to applications</a>.</div>{% endif %}
  {% if saved %}<div class="ok">Saved.</div>{% endif %}
  <form method="post" action="/answers">
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
  {% if waiting %}
    <h2>Waiting for your answer <span class="count">{{ waiting|length }}</span></h2>
    <div class="card flush attention">
    <table>
      <thead><tr><th style="width:55%">Question</th><th>Your answer</th></tr></thead>
      {% for q in waiting %}
      <tr class="waiting"><td>{{ q.question }}<p class="hint">Asked {{ q.times }} time(s){% if q.companies %} by
          {{ q.companies|join(", ") }}{% endif %}{% if q.options %}. Choices: {{ q.options|join(" / ") }}{% endif %}</p></td>
        <td><input type="hidden" name="key" value="{{ q.key }}">
            <input type="text" name="answer::{{ q.key }}" placeholder="Leave blank to be asked again"></td></tr>
      {% endfor %}
    </table>
    </div>
  {% endif %}
  <h2>Saved <span class="count">{{ saved_rows|length }}</span></h2>
  <div class="card flush">
  {% if saved_rows %}
  <table>
    <thead><tr><th style="width:50%">Question</th><th>Answer</th><th class="num">Remove</th></tr></thead>
    {% for row in saved_rows %}
    <tr><td>{{ row.question }}</td>
        <td><input type="hidden" name="key" value="{{ row.key }}">
            <input type="text" name="answer::{{ row.key }}" value="{{ row.answer }}"></td>
        <td class="num"><input type="checkbox" name="remove::{{ row.key }}" aria-label="Remove"></td></tr>
    {% endfor %}
  </table>
  {% else %}<div class="empty"><b>None yet</b>Questions you answer during a run are saved here.</div>{% endif %}
  </div>
  <h2>Add one</h2>
  <div class="card">
    <div class="add">
      <label>Question, as forms ask it <input type="text" name="new_question" placeholder="e.g. Have you worked for a government agency?"></label>
      <label>Your answer <input type="text" name="new_answer"></label>
    </div>
    <p style="margin-bottom:0"><button type="submit">Save answers</button></p>
  </div>
  </form>
</main>
""")
