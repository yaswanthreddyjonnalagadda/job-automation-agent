/**
 * Live Agent Cockpit & Answer Transparency Controller
 * Handles real-time polling/SSE, in-place DOM updates, and handoff actions.
 */

(function() {
  let isPolling = false;
  let activeFilter = 'all';
  let activeTab = 'timeline';
  let currentHandoff = null;
  let startTime = null;

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) return meta.getAttribute('content');
    const input = document.querySelector('input[name="csrf_token"]');
    return input ? input.value : (window.CSRF_TOKEN || '');
  }

  async function postJson(url, data = {}) {
    const csrf = getCsrfToken();
    const formData = new FormData();
    formData.append('csrf_token', csrf);
    for (const [k, v] of Object.entries(data)) {
      formData.append(k, typeof v === 'object' ? JSON.stringify(v) : v);
    }
    const res = await fetch(url, {
      method: 'POST',
      body: formData
    });
    return res.json();
  }

  function formatDuration(seconds) {
    if (!seconds || seconds < 0) return '00:00';
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  }

  function formatTime(isoStr) {
    if (!isoStr) return '';
    try {
      const d = new Date(isoStr);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch {
      return isoStr.slice(11, 19);
    }
  }

  function getProvenanceClass(source) {
    const s = (source || '').toUpperCase();
    if (s.includes('PROFILE')) return 'badge-profile';
    if (s.includes('RESUME')) return 'badge-resume';
    if (s.includes('AI') || s.includes('INFERENCE')) return 'badge-ai';
    if (s.includes('USER') || s.includes('OVERRIDE')) return 'badge-user';
    return 'badge-deterministic';
  }

  function updateStepper(stepNumber, isStalled, isHandoff) {
    const steps = [
      { num: 1, label: 'Start' },
      { num: 2, label: 'Auth' },
      { num: 3, label: 'Form Fill' },
      { num: 4, label: 'Review' },
      { num: 5, label: 'Submit' }
    ];

    const container = document.getElementById('cockpit-stepper');
    if (!container) return;

    container.innerHTML = steps.map((s, idx) => {
      let stateClass = '';
      if (s.num < stepNumber) stateClass = 'is-completed';
      else if (s.num === stepNumber) {
        stateClass = isStalled ? 'is-warning' : (isHandoff ? 'is-warning' : 'is-active');
      }

      const connector = idx < steps.length - 1 ? 
        `<div class="step-connector ${s.num < stepNumber ? 'is-completed' : ''}"></div>` : '';

      return `
        <div class="step-item ${stateClass}">
          <div class="step-circle">${s.num < stepNumber ? '✓' : s.num}</div>
          <div class="step-label">${s.label}</div>
        </div>
        ${connector}
      `;
    }).join('');
  }

  function renderChallengeBanner(handoff) {
    const banner = document.getElementById('cockpit-challenge-banner');
    if (!banner) return;

    if (!handoff || handoff.status === 'RESOLVED') {
      banner.style.display = 'none';
      return;
    }

    banner.style.display = 'flex';
    const isCaptcha = handoff.category === 'CAPTCHA';
    banner.className = `challenge-banner ${isCaptcha ? 'is-captcha' : ''}`;

    const title = isCaptcha ? 'CAPTCHA Challenge Detected' :
      (handoff.category.includes('MFA') ? 'Multi-Factor Authentication Required' :
      (handoff.category === 'VERIFY_EMAIL' ? 'Email Verification Needed' : 'Action Required'));

    banner.innerHTML = `
      <div class="challenge-content">
        <h4 class="challenge-title">⚠️ ${title}</h4>
        <p class="challenge-desc">
          <strong>${handoff.employer || 'Employer'}</strong>: ${handoff.reason || 'Human intervention required.'}
          <br><small style="color:var(--muted)">Resume condition: ${handoff.resume_condition || 'Owner resolves challenge'}</small>
        </p>
      </div>
      <div class="challenge-actions">
        <button type="button" class="btn-solve" onclick="window.Cockpit.handleSolveHandoff('${handoff.handoff_id}')">
          ${isCaptcha ? "I've Solved It — Check & Resume" : "Continue"}
        </button>
        <button type="button" class="btn-control" onclick="window.Cockpit.raiseBrowser()">
          Raise Browser
        </button>
        <button type="button" class="btn-control danger" onclick="window.Cockpit.stopRun()">
          Skip Application
        </button>
      </div>
    `;
  }

  function renderTimeline(events) {
    const list = document.getElementById('cockpit-timeline-list');
    if (!list) return;

    if (!events || events.length === 0) {
      list.innerHTML = '<li class="timeline-item"><span class="muted">No events recorded yet.</span></li>';
      return;
    }

    const filtered = events.filter(e => {
      if (activeFilter === 'all') return true;
      if (activeFilter === 'verified') return e.is_verified;
      if (activeFilter === 'challenges') return e.event.includes('CAPTCHA') || e.event.includes('MFA') || e.event.includes('HANDOFF');
      if (activeFilter === 'errors') return e.reason_code || e.event.includes('WARNING') || e.event.includes('LOOP');
      return true;
    });

    list.innerHTML = filtered.map(e => {
      const verifiedTag = e.is_verified ?
        `<span class="verified-badge">✓ Verified</span>` : '';
      const evidenceSnippet = e.evidence ?
        `<br><small class="muted">Evidence: ${e.evidence}</small>` : '';

      return `
        <li class="timeline-item">
          <span class="timeline-time">${formatTime(e.timestamp)}</span>
          <div style="flex:1">
            <span class="timeline-event-name">${e.event}</span>
            <div class="timeline-msg">${e.display_message || ''}</div>
            ${verifiedTag}
            ${evidenceSnippet}
          </div>
        </li>
      `;
    }).join('');
  }

  function renderLedger(answers) {
    const tableBody = document.getElementById('cockpit-ledger-body');
    if (!tableBody) return;

    if (!answers || answers.length === 0) {
      tableBody.innerHTML = '<tr><td colspan="5" class="muted" style="text-align:center;padding:16px;">No answers recorded for this form yet.</td></tr>';
      return;
    }

    tableBody.innerHTML = answers.map(a => {
      const provClass = getProvenanceClass(a.source);
      const correctionNote = a.previous_value ?
        `<div class="ledger-correction-note">Changed from "${a.previous_value}" (${a.correction_reason || 'correction'})</div>` : '';

      return `
        <tr>
          <td><strong>${a.question_text || ''}</strong>${correctionNote}</td>
          <td><code>${a.final_answer || ''}</code></td>
          <td><span class="provenance-badge ${provClass}">${a.source || 'RULE'}</span></td>
          <td><span class="verified-badge">✓ Accepted</span></td>
          <td>
            <button type="button" class="btn-control" style="padding:2px 8px;font-size:11px;"
              onclick="window.Cockpit.editAnswer(${a.id}, '${escapeQuotes(a.question_text)}', '${escapeQuotes(a.final_answer)}')">
              Edit
            </button>
          </td>
        </tr>
      `;
    }).join('');
  }

  function escapeQuotes(str) {
    return (str || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
  }

  async function fetchState() {
    try {
      const res = await fetch('/api/cockpit-state');
      if (!res.ok) return;
      const data = await res.json();
      updateCockpit(data);
    } catch (err) {
      console.debug('Cockpit poll error:', err);
    }
  }

  function updateCockpit(data) {
    const card = document.getElementById('live-cockpit-card');
    if (!card) return;

    if (!data.is_running && !data.active_handoff) {
      const dot = document.getElementById('cockpit-dot');
      if (dot) dot.className = 'cockpit-status-dot';
      card.classList.remove('is-active', 'is-stalled', 'is-handoff');
      document.getElementById('cockpit-status-text').textContent = 'Idle';
      return;
    }

    card.classList.add('is-active');

    // Title and Employer
    const titleEl = document.getElementById('cockpit-job-title');
    if (titleEl) titleEl.textContent = data.job_title || 'Application in Progress';
    const subEl = document.getElementById('cockpit-employer');
    if (subEl) subEl.textContent = `${data.employer || 'Employer'} • ${data.portal || 'Direct ATS'}`;

    // Status Dot & Badges
    const dot = document.getElementById('cockpit-dot');
    const isStalled = data.loop_status === 'stalled';
    const isWarning = data.loop_status === 'warning';
    const hasHandoff = !!data.active_handoff;
    const isWaiting = !!data.is_waiting;
    const statusText = document.getElementById('cockpit-status-text');
    if (statusText) statusText.textContent = isWaiting ? 'Waiting for you' : (data.stage || 'Working');

    if (dot) {
      if (isStalled) dot.className = 'cockpit-status-dot danger';
      else if (isWarning || hasHandoff || isWaiting) dot.className = 'cockpit-status-dot warning';
      else dot.className = 'cockpit-status-dot pulsing';
    }

    const stallBadge = document.getElementById('cockpit-stall-badge');
    if (stallBadge) {
      if (isWaiting) {
        stallBadge.className = 'badge badge-stall-warning';
        stallBadge.textContent = 'Waiting for you';
      } else if (isStalled) {
        stallBadge.className = 'badge badge-stall-danger';
        stallBadge.textContent = 'Stalled (Loop Guard)';
      } else if (isWarning) {
        stallBadge.className = 'badge badge-stall-warning';
        stallBadge.textContent = 'Retry Warning';
      } else {
        stallBadge.className = 'badge badge-stall-normal';
        stallBadge.textContent = 'Progressing Normal';
      }
    }

    const timerBadge = document.getElementById('cockpit-timer');
    if (timerBadge && data.elapsed_seconds !== undefined) {
      timerBadge.textContent = formatDuration(data.elapsed_seconds);
    }

    // Live Actions Card
    const currentActionEl = document.getElementById('cockpit-current-action');
    if (currentActionEl) currentActionEl.textContent = data.current_action || 'Processing page elements...';

    const lastVerifiedEl = document.getElementById('cockpit-last-verified');
    if (lastVerifiedEl) {
      lastVerifiedEl.textContent = data.last_verified_action || 'None recorded';
    }

    // Stepper
    updateStepper(data.stage_step || 1, isStalled, hasHandoff || isWaiting);

    // Handoff Banner
    renderChallengeBanner(data.active_handoff);

    // Timeline & Ledger
    renderTimeline(data.recent_events || []);
    renderLedger(data.recent_answers || []);
  }

  // Public API methods for UI buttons
  window.Cockpit = {
    startPolling: function() {
      if (isPolling) return;
      isPolling = true;
      fetchState();
      setInterval(fetchState, 2000);
    },

    setFilter: function(filterName) {
      activeFilter = filterName;
      document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
      const activeBtn = document.getElementById(`filter-${filterName}`);
      if (activeBtn) activeBtn.classList.add('active');
      fetchState();
    },

    setTab: function(tabName) {
      activeTab = tabName;
      document.querySelectorAll('.cockpit-tab-btn').forEach(b => b.classList.remove('active'));
      const timelineContent = document.getElementById('tab-content-timeline');
      const ledgerContent = document.getElementById('tab-content-ledger');

      if (tabName === 'timeline') {
        document.getElementById('tab-btn-timeline')?.classList.add('active');
        if (timelineContent) timelineContent.style.display = 'block';
        if (ledgerContent) ledgerContent.style.display = 'none';
      } else {
        document.getElementById('tab-btn-ledger')?.classList.add('active');
        if (timelineContent) timelineContent.style.display = 'none';
        if (ledgerContent) ledgerContent.style.display = 'block';
      }
    },

    pauseRun: async function() {
      await postJson('/api/cockpit/pause');
      fetchState();
    },

    resumeRun: async function() {
      await postJson('/api/cockpit/resume');
      fetchState();
    },

    stopRun: async function() {
      if (confirm('Cancel and stop this application run?')) {
        await postJson('/api/cockpit/stop');
        fetchState();
      }
    },

    raiseBrowser: async function() {
      await postJson('/api/cockpit/raise-browser');
    },

    handleSolveHandoff: async function(handoffId) {
      const res = await postJson('/api/handoff/resolve', {
        handoff_id: handoffId,
        action: 'check_and_resume'
      });

      if (!res.ok) {
        alert(res.error || 'Challenge still appears active on the page. Please complete it in the browser window.');
      } else {
        fetchState();
      }
    },

    editAnswer: function(id, q, curr) {
      const val = prompt(`Edit answer for:\n"${q}"`, curr);
      if (val !== null && val !== curr) {
        postJson('/api/answers/update', { answer_id: id, new_value: val }).then(() => fetchState());
      }
    }
  };

  // Start polling when DOM is ready
  document.addEventListener('DOMContentLoaded', () => {
    window.Cockpit.startPolling();
  });
})();
