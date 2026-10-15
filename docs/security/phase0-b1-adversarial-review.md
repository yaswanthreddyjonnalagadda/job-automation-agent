# Phase 0-B1 Adversarial Red-Team Review

## Role and Scope
- **Role**: Adversarial Red-Team Reviewer for Phase 0-B1 (`job-automation-agent`).
- **Target**: Stress-test and actively attempt to violate the frozen safety invariants of P0-B1 using hostile sequences, races, redirects, crashes, retries, misleading UI, identity confusion, stale state, and adversarial control semantics.
- **Constraints**:
  - Zero modifications to production Python code.
  - Zero modifications to repository test suites.
  - No P0-B2 work initiated.
  - No live employer submissions performed; all dynamic attacks executed against local synthetic fixtures and headless Playwright instances.

---

## Adversarial Findings

### FINDING 1: Unrecognized Submit Semantics on Non-Submit Controls Bypass Guard and Submit Automatically

- **SEVERITY**: **BLOCKER**
- **ATTACK**: Autonomous final submission bypass via unrecognized submit control semantics on `button[type=button]`, custom `div`/`span` widgets, or localized controls.
- **PRECONDITIONS**:
  1. An ATS application step presents a final submission button implemented as `<button type="button">` (standard in modern React/Vue/Angular ATS portals such as Workday or custom enterprise portals) or a custom element.
  2. The button label uses semantic finality phrasing not covered by `safety.SUBMIT_LABEL_RE` (e.g., `"Confirm & Send"`, `"Review & Send"`, `"Finalize"`, `"Complete"`, `"Done"`, `"Save & Finish"`, `"Proceed"`, or non-English labels like `"Postuler"` / `"Bewerben"` / `"Enviar"`).
- **TRACE**:
  1. `PageAgent.run()` evaluates the page. The LLM planner outputs a `PagePlan` designating the control as `next_kind = "next_step"` (or `final_submit` on a non-review step).
  2. `PageAgent._press_next_locked()` computes `submit_word = safety.is_submit_label(label)`.
  3. Because `safety.SUBMIT_LABEL_RE` only matches a narrow regex (`submit`, `apply`, `send application`, `save and submit`, `finish`, `complete application`), `submit_word` evaluates to `False`.
  4. `final` evaluates to `False`. The code falls into the ordinary navigation branch: `logger.info("NEXT: pressing %r", label)`.
  5. `self.locate(page, control.ref).click(timeout=10_000)` executes directly without passing through `submit_gate` or `submit_verified`.
  6. In the browser, `SubmissionGuardV0`'s injected capture listener intercepts the click:
     - `finalControl(element)` checks `labels(candidate).some(label => submitLabel.test(label))` -> `False`.
     - It then checks `structuralSubmitter(candidate)`:
       ```javascript
       element instanceof HTMLButtonElement
         ? Boolean(element.form) && (element.type.toLowerCase() === "submit" || !element.hasAttribute("type"))
         : ...
       ```
     - Because `type === "button"`, `structuralSubmitter` evaluates to `False`.
     - `finalControl` returns `null`.
  7. The event is NOT cancelled. No denial is recorded (`guard.denials(page)` remains empty).
  8. The click natively triggers the button's `onclick` / event listener, executing the external application submission.
  9. No durable `DISPATCHED` state was ever committed in SQLite, and zero gateway authorization occurred.
- **RESULT**: **PROVEN BYPASS**. Ordinary automation executed an uncontained, irreversible final application submission.
- **INVARIANT IMPACT**: Violates the core invariant: *Zero LLM or heuristic authority at final Submit; all automated submissions must commit durable DISPATCHED before effect.*
- **EVIDENCE**: Empirically reproduced and verified via local Playwright fixture (`test_unlisted_label.py`). When `<button type="button" onclick="window.submitted = true">Confirm & Send</button>` was clicked by `PageAgent.press_next()`, output was:
  ```
  Outcome: moved
  Window.submitted: True
  Guard denials: []
  ```
- **RECOMMENDATION**:
  1. Expand `structuralSubmitter` and `finalControl` detection in `submission_guard.py` to evaluate form action boundaries and broader submission intent.
  2. Any control on an application step that triggers form submission or network dispatch without an explicit, proven "Next/Continue" navigation signature must default to blocked unless confirmed non-final.
  3. Expand `safety.SUBMIT_LABEL_RE` to include `"confirm"`, `"finalize"`, `"review & send"`, `"complete"`, `"done"`, and common localized equivalents.

---

### FINDING 2: Weaponized Alias Annexation Breaks Transitivity, Permitting Second Submission via Sub-Aliases

- **SEVERITY**: **BLOCKER**
- **ATTACK**: Multi-root alias collision weaponized to decouple sub-aliases from dispatch history, enabling duplicate application dispatch for the same application identity.
- **PRECONDITIONS**:
  1. Application session 1 begins at an intermediate portal / careers landing page `B` (e.g., `https://company.wd1.myworkdayjobs.com/Careers`), which navigates to application review form `B_alias` (`https://company.wd1.myworkdayjobs.com/Careers/job/123/apply`).
  2. `record_submission_identity_alias("B", "B_alias")` records `(alias_key='B_alias', dedup_key='B')`.
  3. Application session 2 starts at posting URL `A` (`https://jobs.lever.co/company/123`), which redirects to portal `B`.
  4. `record_submission_identity_alias("A", "B")` annexes `B` under `A`.
  5. Application `A` completes and dispatches: `begin_submission_dispatch("A")` -> `DISPATCHED`.
- **TRACE**:
  1. In `job_tracker.py`, `_identity_root(conn, key)` is defined as:
     ```python
     row = conn.execute(
         "SELECT dedup_key FROM submission_identity_aliases WHERE alias_key = ?", (key,)
     ).fetchone()
     return row["dedup_key"] if row else key
     ```
  2. This lookup is single-hop. It does NOT follow transitive alias chains (`B_alias -> B -> A`).
  3. When `A` dispatches, `submission_effects` records `(A, 'DISPATCHED')`.
  4. An invocation is subsequently initiated or resumed from `B_alias` (e.g. from `last_page_url` or direct browser link).
  5. `begin_submission_dispatch("B_alias")` queries `_identity_group(conn, "B_alias")`.
  6. `_identity_root(conn, "B_alias")` returns `B`.
  7. `_identity_group(conn, "B_alias")` returns `{B, B_alias}`.
  8. Neither `B` nor `B_alias` possesses a row in `submission_effects` (because the effect was recorded under `A`).
  9. `begin_submission_dispatch("B_alias")` finds no blocked state, writes `(B_alias, 'DISPATCHED')`, and returns `"DISPATCHED"`.
- **RESULT**: **PROVEN BYPASS**. The system permits a second final dispatch for the same real application workflow.
- **INVARIANT IMPACT**: Direct violation of the irreversible-effect invariant: *After DISPATCHED: NO automatic re-submit.*
- **EVIDENCE**: Empirically reproduced and verified via isolated SQLite test script (`test_annexation_bypass.py`):
  ```
  Group of B: ('B_alias', 'B')
  Group of B_alias: ('B_alias', 'B')
  Group of A: ('A', 'B')
  State of A: DISPATCHED
  CRITICAL: B_alias DISPATCHED! Second dispatch succeeded!
  ```
- **RECOMMENDATION**:
  1. `record_submission_identity_alias` must refuse annexation if the candidate `alias_key` is already a root in `submission_identity_aliases` (`SELECT 1 FROM submission_identity_aliases WHERE dedup_key = ?`).
  2. Implement transitive closure resolution (recursive CTE or while-loop in `_identity_root` / `_identity_group`) so that any alias connected in the graph resolves to the canonical terminal root and discovers all associated states bidirectionally.

---

### FINDING 3: SubmissionGuardV0 Defaults to Inactive on Newly Opened Tabs/Windows, Allowing Unguarded Clicks

- **SEVERITY**: **BLOCKER**
- **ATTACK**: Bypassing browser containment on multi-tab or popup ATS flows.
- **PRECONDITIONS**:
  1. An ATS portal opens an application form in a new tab or popup (e.g., via `window.open()` or `<a target="_blank">`), common across Taleo, BrassRing, and Workday external links.
  2. Interaction occurs on the new tab before `protect_submission` or `activate` is explicitly called on that tab.
- **TRACE**:
  1. In `submission_guard.py`:
     ```javascript
     const state = {active: false, authorization: null, denials: [], sequence: 0};
     ```
  2. In `SubmissionGuardV0.__init__`:
     `context.add_init_script(script=self._script)` injects `_guard_script` into all future pages.
     However, `state.active` is initialized to `false`.
  3. `SubmissionGuardV0` does NOT hook `context.on("page")` to activate new pages automatically.
  4. Pages are only activated when `guard.activate(page)` is explicitly called.
  5. When a new tab opens, its injected guard script remains in `state.active = false`.
  6. In `_guard_script`:
     ```javascript
     window.addEventListener("click", event => {
       if (!state.active) return;
       ...
     ```
  7. Any click on a submit button (even one labeled `"Submit Application"` or `<button type="submit">`) executes without interception.
- **RESULT**: **PROVEN BYPASS**. Clicks on newly opened tabs are completely unintercepted by the guard.
- **INVARIANT IMPACT**: Violates the context-wide containment invariant: *Blocks recognized submit-capable controls before page handlers run.*
- **EVIDENCE**: Empirically reproduced and verified via local Playwright test (`test_new_tab.py`):
  ```
  Page 2 window.submitted: True
  Page 2 guard denials: []
  ```
- **RECOMMENDATION**:
  1. In `_guard_script`, initialize `state.active = true` by default, requiring capability authorization to deactivate.
  2. In `SubmissionGuardV0.__init__`, attach a `context.on("page", ...)` listener that immediately calls `self.activate(page)` whenever any new tab or popup is spawned.

---

### FINDING 4: Omission of Non-Standard Tracking Parameters in URL Canonicalization Permits Duplicate Replay

- **SEVERITY**: **HIGH**
- **ATTACK**: Sponsoring a second application run by varying marketing/click tracking parameters not stripped by `submission_identity_url`.
- **PRECONDITIONS**:
  1. An application was previously dispatched/submitted via a URL containing a non-standard or platform-specific tracking parameter (e.g. `?trk=linkedin_jobs` from LinkedIn, `?fbclid=...`, `?gclid=...`, or ATS-specific campaign params).
  2. A subsequent run is initiated with the clean URL or a different tracking parameter.
- **TRACE**:
  1. In `jd_analyzer.py:submission_identity_url()`, lines 74–77:
     ```python
     retained_query = sorted(
         (key, item)
         for key, item in parse_qsl(parsed.query, keep_blank_values=True)
         if not key.startswith(("utm_", "gh_src"))
         and key not in {"src", "source", "ref", "referrer", "trackingid"}
     )
     ```
  2. Parameters such as `trk`, `fbclid`, `gclid`, `msclkid`, `twclid`, and `li_fat_id` are not in the stripped list.
  3. They are retained in `retained_query`.
  4. `submission_effect_key_for_url()` hashes the canonical URL string.
  5. URL 1 (`https://example.com/job/1?trk=linkedin`) and URL 2 (`https://example.com/job/1`) hash to two completely different SHA-256 keys.
  6. `find_submitted()` in `job_tracker.py` uses `_strip_tracking()`, which shares the exact same narrow parameter list, so it also fails to correlate the two URLs.
  7. If company or title normalization has slight discrepancies, the system treats URL 2 as `NOT_ATTEMPTED` and dispatches a second submission.
- **RESULT**: **REACHABLE SECOND EFFECT UNDER SPECIFIC CONDITIONS**.
- **INVARIANT IMPACT**: Bypasses replay protection across standard platform referral links.
- **EVIDENCE**: Verified via `python -c`:
  `submission_effect_key_for_url(".../job/123?trk=linkedin") != submission_effect_key_for_url(".../job/123")`.
- **RECOMMENDATION**:
  Expand stripped query parameters in `jd_analyzer.py` and `job_tracker.py` to cover `trk`, `fbclid`, `gclid`, `msclkid`, `twclid`, `li_fat_id`, `mc_cid`, `rb_clickid`, and `shared_id`.

---

### FINDING 5: Login-Host Skipping in Alias Recording Leaves Auth-Hop Re-entry Disconnected

- **SEVERITY**: **HIGH**
- **ATTACK**: Re-entering an application via a login URL creates a disconnected identity that bypasses terminal effect history.
- **PRECONDITIONS**:
  1. A run progresses through `posting -> login host -> ATS host -> application -> review -> DISPATCHED`.
  2. The intermediate login host URL was visited during navigation.
- **TRACE**:
  1. In `apply_flow.py`, `_remember_submission_identity_alias()` is invoked inside `remember_progress()`:
     ```python
     if not worth_returning_to(url):
         return
     ...
     _remember_submission_identity_alias(assistant, url)
     ```
  2. `worth_returning_to(url)` returns `False` for any URL containing `"/login"`, `"signin"`, `"sign-in"`, etc.
  3. As a result, the login host is never recorded as an alias in `submission_identity_aliases`.
  4. If a user or resume action starts from the login host URL, `submission_effect_key_for_url` computes a fresh, unaliased key.
  5. If deduplication by company/title does not match, the login host identity can initiate a new application flow without finding the existing `DISPATCHED`/`CONFIRMED` state.
- **RESULT**: **REACHABLE GAP IN IDENTITY CONTINUITY**.
- **INVARIANT IMPACT**: Breaks the required redirect-continuity chain: `posting -> login -> ATS -> application -> review`.
- **EVIDENCE**: Confirmed in `apply_flow.py:639-646` and `worth_returning_to()`.
- **RECOMMENDATION**:
  Decouple alias recording from `worth_returning_to()`. All visited URLs during a bound run should be recorded as aliases of `assistant.submission_key`, even if they are not suitable for `last_page_url` browser resume.

---

### FINDING 6: Broad Substring Matching in Confirmation Detection Produces False Positive Confirmations

- **SEVERITY**: **MEDIUM**
- **ATTACK**: Inducing a false `CONFIRMED` state using misleading or incidental page text.
- **PRECONDITIONS**:
  1. A verified submission click is executed.
  2. The post-submission page fails or redirects to an error/FAQ/support page that lacks a submit button.
  3. The page contains incidental phrases such as `"Application received after 5pm will be reviewed next day"` or `"Status inquiry for previously submitted application"`.
- **TRACE**:
  1. `browser_automation.py:submission_confirmed()` checks `self._CONFIRMATION_PHRASES`.
  2. Phrases like `"application received"` and `"application submitted"` are matched as simple substrings (`phrase in body`).
  3. If found and no submit button is visible, `submission_confirmed()` returns `True`.
  4. `apply_flow.py` calls `finish_submission_effect(..., "CONFIRMED")`.
- **RESULT**: **CONSERVATIVE FALSE BLOCKING (AVAILABILITY / DATA QUALITY)**.
  The application is marked `CONFIRMED` and terminal when it was not actually received by the employer.
- **INVARIANT IMPACT**: Does NOT permit a second submission (fails closed regarding submission effects), but corrupts audit data and falsely closes the application.
- **EVIDENCE**: Confirmed via inspection of `browser_automation.py:6147-6149` and `SubmissionProbe.inspect()`.
- **RECOMMENDATION**:
  Use bounded contextual regexes or structured heading/container checks (e.g. `^h1, h2, [role=alert]`) rather than loose substring checks across the entire page body.

---

### FINDING 7: Pre-Dispatch Ordering Within Verified Gateway Is Strictly Enforced

- **SEVERITY**: **INFORMATIONAL / VERIFIED**
- **ATTACK**: Attempting to trigger an external browser submit effect before durable `DISPATCHED` is committed in SQLite.
- **ANALYSIS**:
  In `SubmissionGuardV0.submit_verified()`:
  1. `tracker.begin_submission_dispatch(dedup_key, aliases)` executes first.
  2. It begins `BEGIN IMMEDIATE`, writes `AUTHORIZED` and `DISPATCHED` records, appends safety events, and commits.
  3. Only after this returns does Python set `dispatched = True` and invoke `locator.evaluate(authorize)` followed by `locator.click()`.
  4. Database lock, write exceptions, or rollback fail closed without invoking `locator.click()`.
- **RESULT**: **REACHABLE BUT BLOCKED**. Inside `submit_verified()`, no click can precede a committed `DISPATCHED` record.

---

### FINDING 8: Submission Safety Events Enforce Strict Schema Allowlist

- **SEVERITY**: **INFORMATIONAL / VERIFIED**
- **ATTACK**: Attempting to inject unexpected event types, unapproved payload fields, nested candidate profiles, or credentials into `submission_safety_events`.
- **ANALYSIS**:
  `_submission_event()` in `job_tracker.py` validates event kinds against `_SUBMISSION_EVENT_FIELDS` and checks that:
  - Required fields are present.
  - No extraneous keys exist.
  - Values are scalar strings, booleans, or nulls matching specific allowed value sets.
- **RESULT**: **REACHABLE BUT BLOCKED**. Malformed, unexpected, or arbitrary payloads are rejected fail-closed with `ValueError`.

---

## Red-Team Summary Matrix

| Vector | Classification | Mechanism & Boundary |
|---|---|---|
| **Ordinary click on recognized control** | REACHABLE BUT BLOCKED | `SubmissionGuardV0` click listener captures in capture phase and denies |
| **Force click / resilient click** | REACHABLE BUT BLOCKED | Resilient helpers check `click_was_denied()` and abort fallback |
| **JS click / dispatch_event** | REACHABLE BUT BLOCKED | Injected script captures and cancels `element.click()` and dispatched events |
| **Enter key form submission** | REACHABLE BUT BLOCKED | Injected `keydown` listener intercepts Enter on forms with recognized submitters |
| **form.submit() / requestSubmit()** | REACHABLE BUT BLOCKED | Prototypes wrapped and intercepted |
| **Unrecognized label on button[type=button]** | **PROVEN BYPASS** | `finalControl()` returns null; `PageAgent.press_next` clicks natively; **BLOCKER** |
| **Submission controls on new tabs/popups** | **PROVEN BYPASS** | New tabs spawn with `active: false`; clicks are completely uncontained; **BLOCKER** |
| **Alias graph annexation transitivity** | **PROVEN BYPASS** | Non-transitive `_identity_root` decouples sub-aliases, permitting duplicate dispatch; **BLOCKER** |
| **Tracking parameter replay** | REACHABLE GAP | Unlisted params (`trk`, etc.) hash to distinct effect keys; **HIGH** |
| **Login-host alias skipping** | REACHABLE GAP | Filter in `worth_returning_to()` drops login URLs from alias graph; **HIGH** |
| **Pre-dispatch SQLite commit order** | REACHABLE BUT BLOCKED | In `submit_verified()`, SQLite transaction commits before browser click |
| **Crash before click / after click** | REACHABLE BUT BLOCKED | `refuse_submission_replay()` converts stale `DISPATCHED` to `UNCERTAIN` and blocks |
| **Handoff quiescence & draining** | REACHABLE BUT BLOCKED | Shared `_execution_lock` prevents concurrent automation while parked |
| **Safety event boundary poisoning** | REACHABLE BUT BLOCKED | Storage layer allowlist strictly enforces schema and rejects invalid payloads |
| **False-positive confirmation evidence** | CONSERVATIVE LIMITATION | Broad body text substrings can mark failed runs `CONFIRMED`; **MEDIUM** |

---

## Final Red-Team Assessment

- **SECOND-SUBMISSION BYPASS**: **REACHABLE** (via non-transitive alias graph annexation on sub-aliases, and via unstripped tracking parameters).
- **PRE-DISPATCH BYPASS**: **REACHABLE** (unrecognized controls on `button[type=button]` and custom elements execute irreversible submission clicks with zero dispatch commit; within the verified gateway, pre-dispatch ordering is enforced).
- **RESTART/REPLAY BYPASS**: **REACHABLE** (via decoupled sub-aliases after annexation and non-canonical URL tracking parameters).
- **IDENTITY BYPASS**: **REACHABLE** (non-standard tracking parameters like `trk` generate distinct effect keys).
- **ALIAS-GRAPH RESULT**: **FAIL-OPEN VULNERABILITY CONFIRMED** (annexing a root with dependents decouples the dependents, allowing subsequent runs on sub-aliases to dispatch a second time).
- **LOGIN-HOST RESULT**: **REACHABLE GAP** (login URLs are excluded by `worth_returning_to()`, omitting auth hops from alias graph).
- **HANDOFF RACE**: **BLOCKED** (shared execution lock serializes PageAgent, vision, and gateway execution before guard disarm).
- **MODEL-AUTHORITY BYPASS**: **REACHABLE FOR UNRECOGNIZED CONTROLS** (LLM designating `"Confirm & Send"` as `next_step` bypasses all submission gates).
- **CONFIRMATION FALSE-POSITIVE RESULT**: **CONFIRMED** (loose substring matching can mark unsubmitted applications `CONFIRMED`; fails closed regarding re-submission).
- **EVENT-BOUNDARY RESULT**: **VERIFIED** (strict allowlist rejects arbitrary/nested/PII metadata).
- **NEW BLOCKERS**:
  1. Unrecognized submit semantics on `button[type=button]` and custom elements bypass both `submit_gate` and `SubmissionGuardV0`.
  2. Non-transitive alias graph allows duplicate submissions via annexed roots' sub-aliases.
  3. `SubmissionGuardV0` is inactive by default on newly spawned browser tabs and windows.
- **HIGH FINDINGS**:
  1. Unstripped tracking parameters (`trk`, `fbclid`, etc.) create distinct effect identities.
  2. Login-host skipping leaves multi-hop redirect re-entry disconnected.
- **RESIDUAL LIMITATIONS**:
  1. Broad substring matching in confirmation detection creates false positive `CONFIRMED` states.
  2. Replay protection remains local to the configured SQLite database.

---

## Final Recommendation

**P0-B1 REQUIRES REMEDIATION**

