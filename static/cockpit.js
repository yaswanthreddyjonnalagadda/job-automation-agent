/**
 * The Applications dashboard: live status of the running application, its activity and answers, and the page's
 * filters and dialogs. Everything dynamic is built with the DOM (textContent), never as HTML strings: an employer's
 * page text reaches the activity feed and must not be able to run as markup.
 */
(function () {
  'use strict';

  const STEPS = ['Start', 'Sign in', 'Fill form', 'Your review', 'Submitted'];
  const FAST = 2000;      // while something is running
  const SLOW = 15000;     // while nothing is
  let activeFilter = 'all';
  let timer = null;
  let polling = false;
  let failures = 0;
  let lastState = null;

  const $ = (id) => document.getElementById(id);
  const make = (tag, props = {}, ...kids) => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k.startsWith('data-') || k.startsWith('aria-') || k === 'role' || k === 'type' || k === 'title') el.setAttribute(k, v);
      else el[k] = v;
    }
    kids.flat().forEach((c) => { if (c != null) el.append(c); });
    return el;
  };

  // ---- talking to the server ---------------------------------------------------------------------------------
  function csrf() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content) return meta.content;
    const input = document.querySelector('input[name="csrf_token"]');
    return input ? input.value : (window.CSRF_TOKEN || '');
  }

  async function postJson(url, data = {}) {
    const body = new FormData();
    body.append('csrf_token', csrf());
    for (const [k, v] of Object.entries(data)) body.append(k, typeof v === 'object' ? JSON.stringify(v) : v);
    try {
      const res = await fetch(url, { method: 'POST', body });
      let json = {};
      try { json = await res.json(); } catch (e) { /* a redirect or an empty reply */ }
      if (!res.ok && json.ok === undefined) json = { ok: false, error: `The server answered ${res.status}.` };
      return json;
    } catch (err) {
      return { ok: false, error: 'Could not reach the dashboard. Is it still running?' };
    }
  }

  // ---- a polite message that fades; also announced to screen readers ----------------------------------------------
  let toastTimer = null;
  function toast(message, bad = false) {
    let el = $('toast');
    if (!el) {
      el = make('div', { id: 'toast', class: 'toast', role: 'status', 'aria-live': 'polite' });
      document.body.append(el);
    }
    el.textContent = message;
    el.classList.toggle('bad', bad);
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), bad ? 6000 : 3200);
  }

  // ---- dialogs: one accessible dialog in place of confirm() and prompt() --------------------------------------------
  function ask({ title, body = '', confirmText = 'OK', cancelText = 'Cancel', danger = false, field = null }) {
    return new Promise((resolve) => {
      if (typeof HTMLDialogElement === 'undefined') {                       // a very old browser: the native ones
        if (field) { const v = window.prompt(title, field.value || ''); resolve(v === null ? null : v); return; }
        resolve(window.confirm(`${title}\n\n${body}`) ? true : null);
        return;
      }
      const input = field ? make('textarea', { id: 'dlg-field', rows: 3, value: field.value || '', 'aria-label': field.label || title }) : null;
      const ok = make('button', { type: 'submit', value: 'ok', class: danger ? 'danger' : '', text: confirmText });
      if (danger) ok.style.background = 'var(--bad)';
      const dlg = make('dialog', { 'aria-labelledby': 'dlg-title' },
        make('form', { method: 'dialog' },
          make('div', { class: 'dlg-body' },
            make('h2', { id: 'dlg-title', text: title }),
            body ? make('p', { text: body }) : null,
            input),
          make('div', { class: 'dlg-foot' },
            make('button', { type: 'submit', value: 'cancel', class: 'ghost', text: cancelText }), ok)));
      const opener = document.activeElement;
      dlg.addEventListener('close', () => {
        const accepted = dlg.returnValue === 'ok';
        const value = input ? input.value : true;
        dlg.remove();
        if (opener && opener.focus) opener.focus();
        resolve(accepted ? value : null);
      });
      document.body.append(dlg);
      dlg.showModal();
      (input || ok).focus();
    });
  }

  // ---- the live card -----------------------------------------------------------------------------------------------
  function formatDuration(seconds) {
    if (!seconds || seconds < 0) return '00:00';
    const m = Math.floor(seconds / 60), s = Math.floor(seconds % 60);
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }

  function formatTime(iso) {
    if (!iso) return '';
    try { return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }); }
    catch (e) { return String(iso).slice(11, 19); }
  }

  // "still working?" answered honestly: how long since the run last did anything, and why that can be long
  function quietNote(seconds) {
    if (typeof seconds !== 'number' || seconds < 45) return '';
    const ago = seconds < 120 ? `${Math.round(seconds)} s` : `${Math.round(seconds / 60)} min`;
    return ` · last step ${ago} ago${seconds >= 120 ? ' (waiting on the AI model, which can take a few minutes)' : ''}`;
  }

  function updateStepper(step, warn) {
    const list = $('cockpit-stepper');
    if (!list) return;
    list.replaceChildren(...STEPS.map((label, i) => {
      const n = i + 1;
      const cls = n < step ? 'is-completed' : (n === step ? (warn ? 'is-warning' : 'is-active') : '');
      const li = make('li', { class: `step-item ${cls}`.trim(), 'data-n': String(n), text: label });
      if (n === step) li.setAttribute('aria-current', 'step');
      if (n < step) li.append(make('span', { class: 'sr-only', text: ' (done)' }));
      return li;
    }));
  }

  function renderBanner(handoff) {
    const banner = $('cockpit-challenge-banner');
    if (!banner) return;
    if (!handoff || handoff.status === 'RESOLVED') { banner.hidden = true; banner.replaceChildren(); return; }
    const category = String(handoff.category || '');
    const isCaptcha = category === 'CAPTCHA';
    const title = isCaptcha ? 'A CAPTCHA needs you'
      : category.includes('MFA') ? 'A security code is needed'
      : category === 'VERIFY_EMAIL' ? 'Email verification is needed' : 'The agent needs you';
    banner.className = `challenge-banner ${isCaptcha ? 'is-captcha' : ''}`.trim();
    banner.replaceChildren(
      make('div', {},
        make('h4', { class: 'challenge-title', text: title }),
        make('p', { class: 'challenge-desc' },
          make('strong', { text: handoff.employer || 'The employer' }),
          `: ${handoff.reason || 'Please finish this step in the browser window.'}`,
          make('br'),
          make('small', { class: 'muted', text: `Then: ${handoff.resume_condition || 'press the button here'}` }))),
      make('div', { class: 'dash-actions' },
        make('button', { type: 'button', class: 'btn-solve', 'data-action': 'solve', 'data-handoff': handoff.handoff_id || '',
          text: isCaptcha ? "I've solved it – check and continue" : 'Continue' }),
        make('button', { type: 'button', class: 'btn-control', 'data-action': 'raise-browser', text: 'Show browser' }),
        make('button', { type: 'button', class: 'btn-control danger', 'data-action': 'stop', text: 'Skip this application' })));
    banner.hidden = false;
  }

  function renderTimeline(events) {
    const list = $('cockpit-timeline-list');
    if (!list) return;
    const shown = (events || []).filter((e) => {
      const name = String(e.event || '');
      if (activeFilter === 'verified') return !!e.is_verified;
      if (activeFilter === 'challenges') return /CAPTCHA|MFA|HANDOFF/.test(name);
      if (activeFilter === 'errors') return !!e.reason_code || /WARNING|LOOP/.test(name);
      return true;
    });
    if (!shown.length) {
      list.replaceChildren(make('li', { class: 'timeline-item' }, make('span', { class: 'muted', text: 'Nothing to show here yet.' })));
      return;
    }
    list.replaceChildren(...shown.map((e) => make('li', { class: 'timeline-item' },
      make('span', { class: 'timeline-time', text: formatTime(e.timestamp) }),
      make('div', { style: 'flex:1;min-width:0' },
        make('span', { class: 'timeline-event-name', text: String(e.event || '').replace(/_/g, ' ') }),
        make('div', { class: 'timeline-msg', text: e.display_message || '' }),
        e.is_verified ? make('span', { class: 'verified-badge', text: '✓ Confirmed' }) : null,
        e.evidence ? make('div', { class: 'muted', style: 'font-size:13px', text: `Evidence: ${e.evidence}` }) : null))));
  }

  const SOURCE_CLASS = (s) => {
    s = String(s || '').toUpperCase();
    return s.includes('PROFILE') ? 'badge-profile' : s.includes('RESUME') ? 'badge-resume'
      : (s.includes('AI') || s.includes('INFERENCE')) ? 'badge-ai' : (s.includes('USER') || s.includes('OVERRIDE')) ? 'badge-user' : '';
  };
  const ANSWER_STATE = { OVERRIDDEN: 'You changed it', CORRECTED: 'Corrected', ACCEPTED: 'Accepted' };

  function renderLedger(answers) {
    const body = $('cockpit-ledger-body');
    if (!body) return;
    if (!answers || !answers.length) {
      body.replaceChildren(make('tr', {}, make('td', { colSpan: 5, class: 'muted', style: 'text-align:center;padding:16px', text: 'No answers recorded yet.' })));
      return;
    }
    body.replaceChildren(...answers.map((a) => {
      const edit = make('button', { type: 'button', class: 'btn-control', 'data-action': 'edit-answer', 'data-id': String(a.id),
        'aria-label': `Edit the answer to: ${a.question_text || ''}`, text: 'Edit' });
      edit.dataset.question = a.question_text || '';
      edit.dataset.value = a.final_answer || '';
      return make('tr', {},
        make('td', {}, make('strong', { text: a.question_text || '' }),
          a.previous_value ? make('div', { class: 'ledger-correction-note', text: `Was "${a.previous_value}" (${a.correction_reason || 'corrected'})` }) : null),
        make('td', {}, make('code', { text: a.final_answer || '' })),
        make('td', {}, make('span', { class: `provenance-badge ${SOURCE_CLASS(a.source)}`, text: a.source || 'RULE' })),
        make('td', { text: ANSWER_STATE[String(a.status || 'ACCEPTED').toUpperCase()] || 'Accepted' }),
        make('td', {}, edit));
    }));
  }

  function setVisible(id, visible) { const el = $(id); if (el) el.hidden = !visible; }

  function updateCockpit(data) {
    lastState = data;
    const card = $('live-cockpit-card');
    if (!card) return;
    const running = !!data.is_running;
    const handoff = data.active_handoff || null;
    const status = $('cockpit-status-text');
    const dot = $('cockpit-dot');

    if (!running && !handoff) {
      card.classList.remove('is-active', 'is-stalled', 'is-handoff');
      if (dot) dot.className = 'cockpit-status-dot';
      if (status) status.textContent = 'Idle';
      ['btn-continue', 'btn-raise', 'btn-stop'].forEach((id) => setVisible(id, false));
      renderBanner(null);
      return;
    }
    card.classList.add('is-active');
    card.classList.toggle('is-handoff', !!handoff);

    const stalled = data.loop_status === 'stalled';
    const warning = data.loop_status === 'warning';
    const waiting = !!data.is_waiting;
    card.classList.toggle('is-stalled', stalled);
    if (status) status.textContent = waiting ? 'Waiting for you' : (data.stage || 'Working');
    if (dot) dot.className = stalled ? 'cockpit-status-dot danger'
      : (warning || handoff || waiting) ? 'cockpit-status-dot warning' : 'cockpit-status-dot pulsing';

    const title = $('cockpit-job-title');
    if (title) title.textContent = data.job_title || 'Application in progress';
    const employer = $('cockpit-employer');
    if (employer) employer.textContent = [data.employer, data.portal].filter(Boolean).join(' · ');

    const stall = $('cockpit-stall-badge');
    if (stall) {
      stall.className = waiting ? 'badge badge-stall-warning' : stalled ? 'badge badge-stall-danger'
        : warning ? 'badge badge-stall-warning' : 'badge badge-stall-normal';
      stall.textContent = waiting ? 'Waiting for you' : stalled ? 'Stuck in a loop' : warning ? 'Retrying' : 'Progressing Normal';
      // said once: "Waiting for you" is already the stage, and "Progressing Normal" needs no badge of its own
      stall.hidden = waiting || !(stalled || warning);
    }
    const clock = $('cockpit-timer');
    if (clock && data.elapsed_seconds !== undefined) clock.textContent = formatDuration(data.elapsed_seconds);
    const doing = $('cockpit-current-action');
    if (doing) doing.textContent = (data.current_action || 'Reading the page…') + quietNote(data.last_activity_seconds);
    const proved = $('cockpit-last-verified');
    if (proved) proved.textContent = data.last_verified_action && data.last_verified_action !== 'None recorded'
      ? data.last_verified_action : 'Nothing confirmed yet';

    updateStepper(data.stage_step || 1, stalled || !!handoff || waiting);
    renderBanner(handoff);
    renderTimeline(data.recent_events || []);
    renderLedger(data.recent_answers || []);

    // Only the buttons that do something right now.
    setVisible('btn-continue', waiting && !handoff);
    setVisible('btn-raise', running);
    setVisible('btn-stop', running || !!handoff);
  }

  // ---- polling: quick while working, slow when idle, silent when the tab is hidden --------------------------------------
  async function fetchState() {
    try {
      const res = await fetch('/api/cockpit-state', { headers: { Accept: 'application/json' } });
      if (!res.ok) throw new Error(String(res.status));
      updateCockpit(await res.json());
      failures = 0;
    } catch (err) {
      failures += 1;
    }
  }

  function schedule() {
    clearTimeout(timer);
    if (!polling) return;
    if (document.hidden) return;                                   // resumes on visibilitychange
    const busy = lastState && (lastState.is_running || lastState.active_handoff);
    const wait = Math.min((busy ? FAST : SLOW) * (1 + failures), 60000);
    timer = setTimeout(async () => { await fetchState(); schedule(); }, wait);
  }

  // ---- actions ------------------------------------------------------------------------------------------------------------
  async function resumeRun() {
    const res = await postJson('/api/cockpit/resume');
    toast(res.ok ? 'Told the agent to carry on.' : (res.error || 'That did not work.'), !res.ok);
    fetchState();
  }
  async function stopRun() {
    const yes = await ask({ title: 'Stop this application?', body: 'The agent stops and its browser closes. Nothing is submitted. You can resume it later from the list.',
      confirmText: 'Stop application', danger: true });
    if (!yes) return;
    const res = await postJson('/api/cockpit/stop');
    toast(res.ok === false ? (res.error || 'Could not stop it.') : 'Stopped.', res.ok === false);
    fetchState();
  }
  async function raiseBrowser() {
    const res = await postJson('/api/cockpit/raise-browser');
    toast(!res.ok ? (res.error || 'Could not reach the browser window.') : (res.raised ? 'Browser brought to the front.' : 'No agent browser window was found.'), !res.ok || !res.raised);
  }
  async function solveHandoff(id) {
    const res = await postJson('/api/handoff/resolve', { handoff_id: id, action: 'check_and_resume' });
    toast(res.ok ? 'Checked. The agent is carrying on.' : (res.error || 'It still shows on the page. Finish it in the browser window first.'), !res.ok);
    fetchState();
  }
  async function editAnswer(id, question, current) {
    const value = await ask({ title: 'Change this answer', body: question, confirmText: 'Save answer', field: { value: current, label: question } });
    if (value === null || value === current) return;
    const res = await postJson('/api/answers/update', { answer_id: id, new_value: value });
    toast(res.ok ? 'Saved. It will be used next time too.' : (res.error || 'Could not save that.'), !res.ok);
    fetchState();
  }

  function setTab(name) {
    document.querySelectorAll('.cockpit-tab-btn').forEach((b) => {
      const on = b.dataset.tab === name;
      b.setAttribute('aria-selected', String(on));
      b.tabIndex = on ? 0 : -1;
    });
    setVisible('tab-content-timeline', name === 'timeline');
    setVisible('tab-content-ledger', name === 'ledger');
  }
  function setFilter(name) {
    activeFilter = name;
    document.querySelectorAll('.filter-chip').forEach((c) => c.setAttribute('aria-pressed', String(c.dataset.filter === name)));
    if (lastState) renderTimeline(lastState.recent_events || []);
  }

  window.Cockpit = {
    startPolling() {
      if (polling) return;
      polling = true;
      fetchState().then(schedule);
    },
    setFilter, setTab, resumeRun, stopRun, raiseBrowser, handleSolveHandoff: solveHandoff, editAnswer,
  };

  // ---- one delegated listener for the card's buttons -------------------------------------------------------------------------
  document.addEventListener('click', (e) => {
    const t = e.target.closest('[data-action],[data-tab],.filter-chip[data-filter]');
    if (!t) return;
    if (t.dataset.tab) return setTab(t.dataset.tab);
    if (t.classList.contains('filter-chip')) return setFilter(t.dataset.filter);
    const act = t.dataset.action;
    if (act === 'continue') resumeRun();
    else if (act === 'stop') stopRun();
    else if (act === 'raise-browser') raiseBrowser();
    else if (act === 'solve') solveHandoff(t.dataset.handoff);
    else if (act === 'edit-answer') editAnswer(t.dataset.id, t.dataset.question, t.dataset.value);
  });

  // arrow keys move between the two tabs, as a tab list should
  document.addEventListener('keydown', (e) => {
    const tab = e.target.closest && e.target.closest('.cockpit-tab-btn');
    if (tab && (e.key === 'ArrowRight' || e.key === 'ArrowLeft')) {
      const other = [...document.querySelectorAll('.cockpit-tab-btn')].find((b) => b !== tab);
      if (other) { setTab(other.dataset.tab); other.focus(); e.preventDefault(); }
    }
    if (e.key === 'Escape') document.querySelectorAll('details.menu[open]').forEach((d) => { d.open = false; d.querySelector('summary')?.focus(); });
  });

  // ---- confirmation for forms that ask for one (data-confirm="...") -------------------------------------------------------
  document.addEventListener('submit', async (e) => {
    const form = e.target;
    const message = form.dataset && form.dataset.confirm;
    if (!message || form.dataset.confirmed === '1') return;
    e.preventDefault();
    const danger = form.dataset.danger === '1';
    const yes = await ask({ title: message, body: form.dataset.confirmBody || '', confirmText: form.dataset.confirmText || 'Yes', danger });
    if (!yes) return;
    form.dataset.confirmed = '1';
    if (form.requestSubmit) form.requestSubmit(e.submitter || undefined); else form.submit();
  });

  // a form that takes a while (finding new jobs) shows that it is working instead of looking dead
  document.addEventListener('submit', (e) => {
    const form = e.target;
    const busy = form.dataset && form.dataset.busy;
    if (!busy || e.defaultPrevented) return;
    setTimeout(() => {
      form.querySelectorAll('button').forEach((b) => { b.disabled = true; });
      const btn = form.querySelector('button[type=submit]');
      if (btn) btn.textContent = busy.replace(/&hellip;/g, '…');
    }, 0);
  });

  // ---- page behaviour: history filter and search, suggested-jobs filter, row menus -----------------------------------------------
  function pageBehaviour() {
    const rows = [...document.querySelectorAll('#apps tr.app')];
    const chips = [...document.querySelectorAll('.dash-chip[data-filter]')];
    const search = $('app-search');
    const none = $('no-match');
    let group = 'all';
    try { group = sessionStorage.getItem('apps-filter') || 'all'; } catch (e) { /* private mode */ }
    const apply = () => {
      const q = ((search && search.value) || '').trim().toLowerCase();
      let count = 0;
      rows.forEach((r) => {
        const ok = (group === 'all' || r.dataset.group === group) && (!q || r.dataset.text.includes(q));
        r.hidden = !ok;
        if (ok) count += 1;
      });
      chips.forEach((c) => c.setAttribute('aria-pressed', String(c.dataset.filter === group)));
      if (none) none.hidden = count > 0 || !rows.length;
      const live = $('apps-count');
      if (live) live.textContent = `${count} shown`;
    };
    chips.forEach((c) => c.addEventListener('click', () => {
      group = c.dataset.filter;
      try { sessionStorage.setItem('apps-filter', group); } catch (e) { /* ignore */ }
      apply();
    }));
    if (search) search.addEventListener('input', apply);
    if (rows.length) apply();

    const jobChips = [...document.querySelectorAll('.dash-chip[data-job-filter]')];
    const cards = [...document.querySelectorAll('.job-card')];
    const jobNone = $('jobs-none');
    jobChips.forEach((chip) => chip.addEventListener('click', () => {
      const f = chip.dataset.jobFilter;
      jobChips.forEach((c) => c.setAttribute('aria-pressed', String(c === chip)));
      let count = 0;
      cards.forEach((card) => {
        const ok = f === 'all' || (f === 'top' && Number(card.dataset.score || 0) >= 85)
          || (f === 'remote' && card.dataset.remote === '1') || (card.dataset.ats || '').toLowerCase() === f;
        card.hidden = !ok;
        if (ok) count += 1;
      });
      if (jobNone) jobNone.hidden = count > 0;
    }));

    document.addEventListener('click', (e) => {
      document.querySelectorAll('details.menu[open]').forEach((d) => { if (!d.contains(e.target)) d.open = false; });
    });
  }

  document.addEventListener('visibilitychange', () => { if (!document.hidden && polling) { fetchState().then(schedule); } });
  document.addEventListener('DOMContentLoaded', () => {
    pageBehaviour();
    if ($('live-cockpit-card')) window.Cockpit.startPolling();
  });
})();
