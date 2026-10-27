"""The dashboard's three HTML pages -- Settings, the application list and one application's page.

Pure markup, moved out of web_ui.py (code-quality audit 26 October 2026) so that file holds the routes and the run
state. web_ui re-exports the three names, so `web_ui.INDEX_HTML` and the routes' `render_template_string` calls are unchanged.
"""
from __future__ import annotations

import ui_shell

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
  <header class="dash-head">
    <div>
      <h1>Applications</h1>
      <p class="dash-summary" id="dash-summary">
        {% if total %}<strong>{{ total }}</strong> tracked &middot; <strong>{{ groups.done }}</strong> submitted
          &middot; <strong>{{ groups.skipped }}</strong> skipped{% else %}Nothing tracked yet{% endif %}
      </p>
    </div>
    <form class="dash-start" method="post" action="/apply" aria-label="Start an application">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <label for="job-url" class="sr-only">Link to the job posting</label>
      <input id="job-url" type="url" name="url" required inputmode="url" autocomplete="off"
             placeholder="Paste a job link from the employer's own site" aria-describedby="job-url-hint">
      <button type="submit">Start application</button>
    </form>
  </header>
  <p class="dash-start-hint" id="job-url-hint">Workday, Greenhouse, Lever, iCIMS and other employer sites. Job boards and staffing agencies are refused.</p>

  {% if error %}<div class="err" role="alert" style="margin-top:16px">{{ error }}</div>{% endif %}

  {% if total %}
  <div class="dash-bar" role="img"
       aria-label="{{ groups.done }} submitted, {{ groups.needs }} need you, {{ groups.working }} in progress, {{ groups.skipped }} skipped">
    <i class="b-done" style="flex-grow:{{ groups.done }}"></i><i class="b-needs" style="flex-grow:{{ groups.needs }}"></i><i class="b-working" style="flex-grow:{{ groups.working }}"></i><i class="b-skipped" style="flex-grow:{{ groups.skipped }}"></i>
  </div>
  <ul class="dash-legend">
    <li style="--c:var(--ok)"><b>{{ groups.done }}</b> submitted</li>
    <li style="--c:var(--warn)"><b>{{ groups.needs }}</b> need you</li>
    <li style="--c:var(--accent)"><b>{{ groups.working }}</b> in progress</li>
    <li style="--c:var(--muted)"><b>{{ groups.skipped }}</b> skipped</li>
  </ul>
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
          <small>{{ a.company }} &middot; <span class="pill {{ status_class(a.status) }}">{{ status_label(a.status) }}</span></small>
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
          {% if r.log %}&middot; <a href="/log?path={{ r.log }}" target="_blank" rel="noopener">View log</a>{% endif %}
          {% if r.screenshot %}&middot; <a href="/evidence?path={{ r.screenshot }}" target="_blank" rel="noopener">Last screenshot</a>{% endif %}
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

  <section class="dash-section" aria-labelledby="jobs-h">
    <header>
      <div>
        <h2 id="jobs-h">Suggested jobs <span class="count">{{ discovered_jobs | length }}</span></h2>
        <p class="dash-sub">Roles found on employers' own job boards that fit your profile. Starting one prepares the application; you review it before anything is sent.</p>
      </div>
      <form method="post" action="/api/discovered/scan" data-busy="Checking job boards&hellip;">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <button type="submit" class="ghost" title="Look for new postings on the configured Greenhouse and Lever boards">Find new jobs</button>
      </form>
    </header>
    {% if discovered_jobs %}
      <div class="dash-chips" role="group" aria-label="Filter suggested jobs" style="margin-bottom:14px">
        <button type="button" class="dash-chip" data-job-filter="all" aria-pressed="true">All</button>
        <button type="button" class="dash-chip" data-job-filter="top" aria-pressed="false">Best matches</button>
        <button type="button" class="dash-chip" data-job-filter="remote" aria-pressed="false">Remote</button>
        <button type="button" class="dash-chip" data-job-filter="greenhouse" aria-pressed="false">Greenhouse</button>
        <button type="button" class="dash-chip" data-job-filter="lever" aria-pressed="false">Lever</button>
      </div>
      <div class="dash-jobs" id="discovered-feed">
        {% for dj in discovered_jobs %}
          <article class="job-card" data-score="{{ dj.match_score }}" data-remote="{{ dj.is_remote }}" data-ats="{{ dj.ats_type }}">
            <div class="job-top">
              <div class="ring {% if dj.match_score >= 85 %}high{% elif dj.match_score >= 70 %}mid{% endif %}" style="--p:{{ dj.match_score }}"
                   role="img" aria-label="{{ dj.match_score }} percent match"><b>{{ dj.match_score }}%</b></div>
              <div style="min-width:0">
                <p class="job-company">{{ dj.company_name }}</p>
                <h3 class="job-title"><a href="{{ dj.url }}" target="_blank" rel="noopener">{{ dj.job_title }}<span class="sr-only"> (opens the posting in a new tab)</span></a></h3>
              </div>
            </div>
            <div class="job-meta">
              <span>{{ dj.location or 'Location not stated' }}</span>
              {% if dj.is_remote %}<span class="tag remote">Remote</span>{% endif %}
              <span class="tag">{{ dj.ats_type | capitalize }}</span>
            </div>
            {% if dj.matched_skills %}<div class="job-meta">{% for m in dj.matched_skills[:3] %}<span class="tag">{{ m }}</span>{% endfor %}</div>{% endif %}
            <div class="job-foot">
              <form method="post" action="/api/discovered/{{ dj.id }}/apply">
                <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                <button type="submit" class="small">Start application</button>
              </form>
              <form method="post" action="/api/discovered/{{ dj.id }}/dismiss">
                <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                <button type="submit" class="ghost small" aria-label="Not interested in {{ dj.job_title }} at {{ dj.company_name }}">Not interested</button>
              </form>
            </div>
          </article>
        {% endfor %}
      </div>
      <div class="dash-empty" id="jobs-none" hidden><b>No jobs match that filter</b>Try another one.</div>
    {% else %}
      <div class="dash-empty"><b>No suggestions yet</b>Press <b style="display:inline">Find new jobs</b> to check your configured job boards.</div>
    {% endif %}
  </section>

  <section class="dash-section" aria-labelledby="hist-h">
    <header>
      <h2 id="hist-h">Your applications <span class="count">{{ total }}</span></h2>
    </header>
    <div class="dash-history">
      {% if apps %}
        <div class="toolbar">
          <div class="dash-chips" role="group" aria-label="Filter by status">
            <button type="button" class="dash-chip" data-filter="all" aria-pressed="true">All</button>
            <button type="button" class="dash-chip" data-filter="needs" aria-pressed="false">Needs you<span class="n">{{ groups.needs }}</span></button>
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
          <td class="company"><strong>{{ a.company }}</strong></td>
          <td class="role">
            <a href="/application/{{ a.id }}">{{ a.title }}</a>
            <span class="loc">{{ a.location or 'Location not stated' }}</span>
          </td>
          <td class="status"><span class="pill {{ status_class(a.status) }}" title="{{ a.notes[:200] if a.notes else status_label(a.status) }}">{{ status_label(a.status) }}</span></td>
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
</main>
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
        <option value="job_not_found" {% if a.status == 'job_not_found' %}selected{% endif %}>Job doesn't exist</option>
        <option value="already_applied" {% if a.status == 'already_applied' %}selected{% endif %}>Already applied</option>
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
           (AUTO_SUBMIT_VERIFIED_ONLY). The agent submits once every mandatory field is filled and the form shows no error.</p>
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
