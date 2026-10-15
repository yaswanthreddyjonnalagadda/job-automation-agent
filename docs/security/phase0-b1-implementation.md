# Phase 0-B1 — Durable Submission Effects

## Scope and base

- Approved base: `6fb53e6c7a9bce394e67107c48fa5e47d1041e9b`.
- Scope: preserve the submission firewall and add the required durable local
  irreversible-effect state and safe handoff behavior.
- No live employer submission was performed.
- No P0-B2 or later-phase implementation is included.

## Files changed for P0-B1

- `jd_analyzer.py`
- `job_tracker.py`
- `db.py`
- `submission_guard.py`
- `browser_automation.py`
- `interaction.py`
- `page_agent.py`
- `apply_flow.py`
- `BEHAVIOUR.md`
- `tests/test_submission_effect_state.py`
- `tests/test_submission_firewall.py`
- `tests/test_auto_submit.py`
- `tests/test_page_agent.py` (inherited local changes preserved)
- `docs/security/phase0-b1-implementation.md`

`safety.py`, configuration, CI, dependencies, and ATS adapter implementations
were not changed. The worktree's existing edits to `tests/test_page_agent.py`
were preserved; that file currently differs substantially from the pinned
base and is relevant to the collection and full-suite results below.

## Required remediation report

- **FILES CHANGED:** `jd_analyzer.py`, `job_tracker.py`, `db.py`, `submission_guard.py`,
  `browser_automation.py`, `interaction.py`, `page_agent.py`, `apply_flow.py`,
  `BEHAVIOUR.md`, `tests/test_submission_effect_state.py`,
  `tests/test_submission_firewall.py`, `tests/test_auto_submit.py`,
  `tests/test_page_agent.py` (inherited local changes preserved), and this
  implementation record.
- **DURABLE STORE USED:** Existing local SQLite `JobTracker` store at the
  configured database path; the production flow uses this canonical effect
  store even when application metadata uses PostgreSQL.
- **STATE MODEL:** Absent row is `NOT_ATTEMPTED`; persisted transitions are
  `AUTHORIZED -> DISPATCHED -> CONFIRMED` or `DISPATCHED -> UNCERTAIN`.
- **DISPATCH COMMIT ORDER:** Revalidate authorization/application, acquire
  `BEGIN IMMEDIATE`, write authorization state/event and dispatched state/event,
  commit, then authorize and invoke the single browser click.
- **STORAGE FAILURE BEHAVIOR:** Fail closed; no submit effect after lock,
  write, validation, conflict, or persistence failure.
- **RESTART/REPLAY BEHAVIOR:** Durable terminal/in-flight states block
  duplicates across tracker objects, process invocations, and browser
  contexts; stale `DISPATCHED` becomes `UNCERTAIN`.
- **UNCERTAIN OUTCOME BEHAVIOR:** Ambiguous post-dispatch outcomes persist
  `UNCERTAIN`; only explicit reconciliation may confirm it.
- **HANDOFF QUIESCENCE:** Shared execution lock drains in-flight PageAgent,
  vision, and gateway work; handoff event is persisted before parking and
  guard relaxation. Failure leaves automation parked with the guard armed.
- **SAFETY EVENTS:** Structured append-only SQLite events include the required
  authorization, dispatch, confirmed, uncertain, blocked, replay-blocked,
  storage-failure, and manual-handoff kinds.
- **NEW TESTS:** Durable dispatch ordering/failure/replay/restart/concurrency,
  post-effect uncertainty/confirmation, read-only probe, structural
  submitters, denial fallbacks, and handoff drain/parking are tested with local
  synthetic fixtures.
- **TARGETED TEST RESULT (Pass 1):** `91 passed`.
- **FULL TEST RESULT (Pass 1):** `1928 passed, 3 skipped, 19 failed, 1 error`
  in 841.81 seconds; the exact observed failures and collection error are
  recorded below.
- **KNOWN BASELINE FAILURES:** The previously recorded clean Windows baseline
  had one known stale-tracker-double failure,
  `tests/test_safety_rules.py::test_a_previously_submitted_job_is_refused`.
  The current dirty Windows/Python 3.14.5 run produced different PageAgent and
  missing-test-reference failures; equivalence to that baseline failure is
  not established.
- **LIMITATIONS:** Local SQLite and URL-derived identity only; bounded submit
  classification; no general network firewall or distributed replay system.
- **P0-B2 STARTED:** NO.

## Architectural seam

`SubmissionGuardV0` installs a context-wide browser guard and blocks
recognized submit-capable controls before page handlers run, including normal
and forced clicks, dispatched and JavaScript clicks, Enter, native
`form.submit()`, and `requestSubmit()`. A denied attempt terminates that path;
resilient helpers do not try another click primitive after denial.

`JobApplicationAssistant.click_verified_submit()` is the only automated final
click gateway. It requires a valid structured authorization, checks the
persisted application record and current preconditions, and commits durable
dispatch state before authorizing and clicking the target. PageAgent planning,
step labels/counters, retry loops, and vision-selected controls do not grant
submit authority. Ordinary Next/Continue navigation remains available when
the control is not classified as a final submit.

### Durable state and store

- Durable store: local SQLite, using the existing `JobTracker` storage
  architecture and configured database path. Application metadata may use
  PostgreSQL, but the production flow supplies the canonical local SQLite
  store for effect history.
- Identity: the existing URL-derived application `dedup_key`; no identity is
  invented when it is unavailable.
- The absent state row maps to `NOT_ATTEMPTED`; persisted states use the frozen
  names `AUTHORIZED`, `DISPATCHED`, `CONFIRMED`, and `UNCERTAIN`.
- `begin_submission_dispatch()` obtains a SQLite `BEGIN IMMEDIATE`
  transaction, checks the existing state, appends `SUBMISSION_AUTHORIZED`,
  writes `AUTHORIZED` when needed, writes `DISPATCHED`, appends
  `SUBMISSION_DISPATCHED`, then commits. The gateway does not authorize or
  click until this method returns successfully.
- A locked, unavailable, invalid, conflicting, or failing store blocks the
  browser effect. The gateway logs the failure and records
  `SUBMISSION_STORAGE_FAILURE` when storage is available to do so.
- `DISPATCHED`, `CONFIRMED`, and `UNCERTAIN` cannot be automatically reset or
  dispatched again. A later invocation converts stale `DISPATCHED` to
  `UNCERTAIN` and blocks replay. A persisted `AUTHORIZED` state is eligible
  only after current authorization and application preconditions are
  revalidated.
- After the effect, independent confirmation evidence permits
  `DISPATCHED -> CONFIRMED`; an unproven or ambiguous result becomes
  `DISPATCHED -> UNCERTAIN`. `UNCERTAIN -> CONFIRMED` is available only with
  explicit reconciliation. Unknown outcomes never return automatically to
  `NOT_ATTEMPTED`.
- `SubmissionProbe.inspect()` exposes a read-only reconciliation view of the
  state, application status, URL/title, recognized confirmation text,
  application ID, and safety events. It has no submission or interaction
  method.

Safety events are structured and append-only in SQLite (update/delete
triggers). The state transaction includes the authorization and dispatch
events. Events cover authorization, dispatch, confirmation, uncertainty,
blocked attempts, replay blocks, storage failures, and manual handoff; their
payloads omit credentials and profile answers.

### Human handoff

PageAgent runs/progression, vision actions, and the verified gateway share the
assistant's execution lock. `human_handoff()` takes that lock, marks execution
as parking, persists `MANUAL_HANDOFF_STARTED`, marks automation parked, and
only then deactivates the browser guard. It therefore waits for in-flight
locked automation work to drain; subsequent progression and gateway attempts
stop while parked. If handoff persistence fails, the assistant remains parked
and the guard remains armed. `resume_automation()` re-arms the guard before
returning execution to the active state.

## Submission primitives and classification boundary

Local synthetic-browser characterization covers normal/forced clicks,
`dispatch_event("click")`, JavaScript `element.click()`, Enter, form
`submit()`/`requestSubmit()`, PageAgent progression, vision selection,
fallback denial, and structural submitters (`button[type=submit]`,
`input[type=submit]`, form-associated submitters, and accessible-name
vocabulary). A form submit with no explicit submitter is blocked when the form
has a recognized final submitter. Structural and label detection are
deterministic and do not use LLM inference.

The final-submit vocabulary remains bounded by the centralized
`safety.SUBMIT_LABEL_RE` and the structural cases above. This is not a
universal semantic or network-effect firewall; arbitrary site scripts,
unrecognized wording/localization, and site-side effects that bypass the
guarded browser primitives remain outside this enforcement boundary.

## Interception coverage

| Primitive | Containment |
|---|---|
| Normal and force locator clicks | Recognized final submitters are captured and prevented; resilient helpers stop after denial |
| `dispatch_event("click")` and `element.click()` | Captured and prevented for recognized submitters |
| Enter and implicit form submission | Prevented when structural or vocabulary-based classification detects a final submitter |
| `HTMLFormElement.submit()` / `requestSubmit()` | Guarded; no submit effect without the authorized one-use dispatch |
| PageAgent and vision-selected actions | Guard remains at the browser boundary; an intermediate classification or model choice cannot bypass it |
| Retry/fallback after denial | Stops; does not attempt another primitive |
| Verified auto-submit | One normal click through the gateway, only after the durable dispatch commit |
| Human submission after handoff | Permitted only after automation is parked and the guard has been disarmed |
| Ordinary Next/Continue navigation | Preserved when it is not classified as final submission |
| Neutral/icon-only/localized controls | Structural submit types are covered; unrecognized non-structural labels remain a limitation |

## Tests

All characterization uses local synthetic pages; no live employer
submission was performed.

- Durable-state, firewall, auto-submit, and tracker focused suites:
  `91 passed` after the handoff-drain regression test was added.
- Full command `python -m pytest -q -rs -n auto` on Windows/Python 3.14.5:
  `1928 passed, 3 skipped, 19 failed, 1 error` in 841.81 seconds. This run
  preceded the final handoff-drain test-only addition; the affected targeted
  firewall tests passed afterward.
- The collection error is
  `tests/test_ant_select.py`: it imports `_somewhere_profile` from the
  current `tests/test_page_agent.py`, where that helper is absent.
- Six `tests/test_failures_catalogue.py` cases report failure references to
  test functions absent from the current `tests/test_page_agent.py`. The other
  13 failures are PageAgent behavior assertions in that same locally modified
  test file. These failures are recorded as observed; they are not attributed
  to the durable-state changes.
- The 3 skips were the opt-in visible-browser test and two PostgreSQL tests
  skipped because the database was unavailable.
- Final collection after the handoff test addition: `1951 tests collected,
  1 error`. Against the clean pinned-base manifest (`1941` node IDs), there
  are 53 current-only node IDs, 43 baseline-only node IDs, and a net increase
  of 10. Baseline-only nodes are 28 from `tests/test_page_agent.py` and 15
  from `tests/test_ant_select.py`; current-only nodes include 31 from
  `tests/test_submission_firewall.py`, 17 from
  `tests/test_submission_effect_state.py`, 3 source-control-character checks,
  and 2 PageAgent tests. The exact before/after node-ID manifests are retained
  as session artifacts (`collection-before-ids.txt` and
  `collection-after-final-ids.txt`).
- The approved Phase 0-A CI-equivalent baseline remains
  `1837 passed, 104 skipped, 0 failed` on Ubuntu/Python 3.13/PostgreSQL. The
  current run used Windows/Python 3.14.5 and a locally modified test tree, so
  it is not a direct environment-equivalent regression comparison.

## Known limitations

- State is local to the configured SQLite file and the existing URL-derived
  application identity; no distributed or cross-device replay coordination is
  provided.
- Classification remains bounded by existing submit vocabulary and
  deterministic structural submit semantics; it does not block arbitrary
  site-owned requests or unrecognized browser/network effects.
- The reconciliation probe is read-only; this work does not add a dashboard
  reconciliation workflow or distributed `SubmissionExecutor`.
- `SecretProvider`, `FactBroker`, the Question Ontology, distributed
  capabilities, PostgreSQL migration, and multi-user infrastructure remain
  outside this remediation.
- No commit was created. P0-B2 was not started.

## Remediation Pass 2

### HIGH — same posting could receive a new effect key from URL variation
*(partially remediated; redirect aliases remain unverified)*

- **Review finding:** URL-derived replay identity had not established stability
  across harmless URL variations.
- **Root cause:** the effect key hashed a lowercased raw URL, while the
  repository's existing duplicate matching already treats tracking parameters
  and trailing slashes as non-identity variation.
- **Files changed:** `jd_analyzer.py`, `apply_flow.py`, `browser_automation.py`,
  `submission_guard.py`, `job_tracker.py`, `db.py`, and
  `tests/test_submission_effect_state.py`, plus the runtime behavior update in
  `BEHAVIOUR.md`.
- **Correction:** add a separate canonical effect identity that removes only
  known tracking parameters, normalizes host/scheme, path trailing slash,
  fragments, and query ordering, while retaining non-tracking query parameters
  and the posting path. Keep the existing application-row key unchanged.
  Discover prior application-row keys for the same canonical URL, and check
  both the canonical effect key and those keys (including the legacy raw URL
  hash) before dispatch. An alias with any persisted state fails closed.
- **Invariant now enforced:** known tracking/query-order/trailing-slash/HTTP
  versus HTTPS variants of the same URL map to the same new effect key; an
  already persisted legacy effect under a discovered alias prevents another
  click. A URL with no absolute HTTP(S) host or no posting path/non-tracking
  query is rejected before irreversible submission.
- **Tests proving it:** effect-key normalization and distinct-posting tests,
  invalid/insufficient URL tests, normalized prior application-key lookup,
  and a synthetic browser test that seeds the legacy effect key and verifies a
  URL-variant attempt does not click.
- **Remaining HIGH limitation:** redirects to a materially different
  host/path, or ATS-specific equivalent URLs not represented by the existing
  application record, cannot be correlated without an ATS-stable
  requisition/application ID. No generic identifier is inferred from page
  content. The common tested variations are fixed, but the review's broader
  URL-identity finding is not fully closed pending representative ATS
  redirect/variant evidence or a stable identifier already available to the
  runtime.

### MEDIUM — safety event metadata API accepted arbitrary payloads

- **Review finding:** event persistence was append-only, but its public method
  accepted arbitrary kinds and JSON-serializable payloads.
- **Root cause:** `_submission_event()` serialized caller-supplied dictionaries
  without validating event names, payload keys, or values.
- **Files changed:** `job_tracker.py` and
  `tests/test_submission_effect_state.py`.
- **Correction:** enforce an explicit event-kind/field/value schema in the
  storage layer, including required fields, bounded enumerated text, booleans,
  and the optional supported evidence label. Unknown event kinds, extra
  payload keys, and arbitrary/nested objects are rejected before insert.
- **Invariant now enforced:** state/event transactions remain atomic and
  append-only; safety metadata persisted through the store is allowlisted and
  cannot carry credentials or candidate-profile payloads through arbitrary
  fields.
- **Tests proving it:** reject unrecognized event kinds and reject extra
  candidate-profile metadata on a blocked event; existing durable dispatch
  tests verify allowed state/event metadata still commits.
- **Remaining limitation:** local filesystem/database administrators can
  still access the SQLite file; this does not add encryption or a distributed
  audit service.

### Regression and manifest disposition

- The previous independent review did not classify any of the 13 PageAgent
  behavior failures as `CAUSED_BY_P0-B1`; it classified them
  `UNRESOLVED`. The six catalogue failures and the `test_ant_select.py`
  collection error were attributed to inherited local test-tree drift. No
  unrelated edits to `tests/test_page_agent.py` were made in this pass.
- **BLOCKER — baseline test coverage is absent from the dirty current test
  tree; not remediated in this pass.** The cause is the inherited local
  replacement/truncation of `tests/test_page_agent.py`: 28 baseline PageAgent
  node IDs are missing, and `tests/test_ant_select.py` cannot collect its 15
  baseline node IDs because it imports the absent `_somewhere_profile` helper.
  No files were changed to address this because the user explicitly prohibited
  modifying unrelated dirty-worktree tests. The invariant that all original
  regression coverage remains executable is therefore not restored here.
  The manifest comparison and full-suite run prove the missing-node/error
  state; a clean test tree is still required before regression certification.
- The exact pinned-baseline manifest remains unchanged. The final current
  manifest after Pass 2 has 1,956 collected node IDs and one collection error,
  versus 1,941 baseline IDs: 58 current-only and 43 baseline-only (net +15).
  Current-only comprise 31 `test_submission_firewall.py`, 22
  `test_submission_effect_state.py`, 3 source-control-character checks, and 2
  PageAgent tests. Five new current-only state tests in this pass cover
  canonical identity, invalid identity, prior application-key lookup, legacy
  alias refusal, and event allowlisting. The other current-only nodes were
  already present after Pass 1.
- Baseline-only nodes remain 28 in the inherited locally modified
  `tests/test_page_agent.py` and 15 in `tests/test_ant_select.py`, whose import
  fails because `_somewhere_profile` is absent from that modified module. No
  node match establishes a rename. These existing tests were not restored or
  otherwise modified because they are unrelated dirty-worktree edits. Exact
  lists remain in session artifacts `collection-before-ids.txt` and
  `collection-after-final-ids.txt`.
- The related PageAgent + `test_finding_the_way.py` run was `120 passed, 12
  failed`. The full Pass 2 command
  `python -m pytest -q -rs -n auto` produced `1934 passed, 3 skipped, 19
  failed, 1 error` in 852.44 seconds. The error remains the missing
  `_somewhere_profile` collection error; six failure-catalogue references and
  thirteen PageAgent behavior tests remain the same unresolved dirty-tree
  failures recorded in the independent review. This Windows/Python 3.14.5 run
  is not an environment-equivalent reproduction of the approved
  Ubuntu/Python 3.13/PostgreSQL baseline.
- Pass 2 focused suites (`test_submission_effect_state.py`,
  `test_submission_firewall.py`, `test_auto_submit.py`,
  `test_tracker_without_a_server.py`) passed: `96 passed`.

## Controlled Clean-Tree Attribution

- **CONTROL SHA:** `6fb53e6c7a9bce394e67107c48fa5e47d1041e9b`
- **CANDIDATE BASE SHA:** `6fb53e6c7a9bce394e67107c48fa5e47d1041e9b`
- **CONTROL TREE CLEAN:** YES. The detached control began at the exact base SHA with a clean tracked tree.
- **CANDIDATE CONTAINS ONLY P0-B1 CHANGES:** YES. It began from the same pristine base and received only the isolated P0-B1 patch. No `tests/test_page_agent.py` changes or unrelated dirty-tree files were applied.
- **ENVIRONMENT IDENTICAL:** YES. Both runs used Python 3.14.5 at `C:\Users\jonna\job-automation-agent\.venv\Scripts\python.exe`; their `pip freeze` artifacts had the same SHA256 (`7EA31F864EE81ECB32C9D554ED45B57ED60EBC42BB0C4DD031F1CEC1D6559E58`). PostgreSQL was unavailable in both runs, and the same environment variables and services were used.
- **CONTROL COLLECTION:** `1941` node IDs; collection succeeded.
- **CANDIDATE COLLECTION:** `1997` node IDs; collection succeeded.
- **COLLECTION DELTA:** `1941` common; `56` candidate-only; `0` control-only. Candidate-only nodes are 31 in `tests/test_submission_firewall.py`, 22 in `tests/test_submission_effect_state.py`, and 3 source-control-character parameter cases. No baseline tests disappeared; no renames were identified.
- **CONTROL FULL RESULT:** `1836 passed, 104 skipped, 1 failed` in 900.30 seconds. The sole failure was `tests/test_safety_rules.py::test_a_previously_submitted_job_is_refused`.
- **CANDIDATE FULL RESULT:** `1891 passed, 104 skipped, 2 failed` in 933.42 seconds. The shared failure was `tests/test_safety_rules.py::test_a_previously_submitted_job_is_refused`; the additional failure was `tests/test_repeated_entries.py::test_the_ai_is_asked_about_each_degree_by_name` (its assertion checks for the old direct `repeated_entries.describe(entry...)` call in `PageAgent.run`).
- **CONTROL PAGEAGENT RESULT:** `73 passed, 85 skipped` for `tests/test_page_agent.py tests/test_finding_the_way.py`.
- **CANDIDATE PAGEAGENT RESULT:** `73 passed, 85 skipped` for the same command.
- **13 PAGEAGENT FAILURE ATTRIBUTION:** `12 NO_LONGER_REPRODUCIBLE`: the corresponding baseline tests are present in both clean manifests and pass in both control and candidate. `1 DIRTY_WORKTREE_ARTIFACT`: `test_a_submit_button_on_an_intermediate_step_is_held_for_human_review` was added only in the contaminated local `tests/test_page_agent.py` and is absent from both clean trees. No failure was demonstrated as introduced by P0-B1 among those 13. The retained clean-subset logs provide aggregate results; the earlier dirty-run per-node failure list was not retained in the available artifacts.
- **DISAPPEARING BASELINE TESTS:** None in the controlled candidate. The 43 baseline-only nodes previously observed against the contaminated tree (28 PageAgent nodes and 15 `test_ant_select.py` nodes inaccessible because of the dirty-tree import error) are all present in the pristine control and candidate manifests.
- **CROSS-HOST/PATH REPLAY CLASSIFICATION:** `HIGH — REACHABLE UNDER SPECIFIC CONDITIONS`. A dispatch that becomes `UNCERTAIN` is recorded under the initial URL-derived key. A later invocation of the same real application under a different ATS host/path can derive a different key; the URL canonicalization does not join these identities, so the new key can reach authorization and dispatch if existing application deduplication does not match it. A second external effect is therefore reachable.
- **STABLE IDENTITY AVAILABLE:** The strongest existing identity is the local application row and its URL-derived `dedup_key`; the row's numeric ID is stable only while that same row is found. No existing ATS requisition/application identifier was found that reliably joins the same application across different hosts or paths. Company/title data is not a unique cross-host identity.
- **NEW P0-B1 REGRESSIONS:** One direct control-versus-candidate regression: `tests/test_repeated_entries.py::test_the_ai_is_asked_about_each_degree_by_name` passes in control and fails in candidate because P0-B1 wraps `PageAgent.run` through `_run_locked`, invalidating that source-inspection assertion. It remains unfixed as instructed. The common `test_a_previously_submitted_job_is_refused` failure is present in both trees and is not introduced by P0-B1.
- **REMAINING BLOCKERS:** The newly introduced `test_the_ai_is_asked_about_each_degree_by_name` failure requires disposition before regression certification.
- **REMAINING HIGH FINDINGS:** A same real application can obtain a new URL-derived effect key after a cross-host/path change and reach a second dispatch under the conditions above. Identity remediation was explicitly out of scope for this controlled-attribution task.

## Remediation Pass 3

- **DEGREE TEST ROOT CAUSE:** `tests/test_repeated_entries.py::test_the_ai_is_asked_about_each_degree_by_name`
  asserted `"repeated_entries.describe(entry" in inspect.getsource(page_agent.PageAgent.run)`. P0-B1's
  shared execution lock (Remediation Pass 1) made `PageAgent.run` a thin wrapper that acquires the
  lock and calls `_run_locked`, which holds the real page-processing loop. The question-building call
  the assertion was looking for still exists and still runs -- it is simply no longer textually
  inside `run`'s own source, which is all that assertion ever inspected.
- **DEGREE BEHAVIOR PRESERVED:** Confirmed, not assumed. The targeted per-question AI path in
  `_run_locked` (`page_agent.py`) still looks up the control's repeated entry in `self._entries`,
  builds its description with `repeated_entries.describe()`, and asks
  `"{question} (for: {description})"` when the control is inside a job or degree entry -- unchanged
  behavior, confirmed by reading the current code before changing anything.
- **FIX:** Extracted the three-line question-building block out of the inline per-control loop into
  a new method, `PageAgent._question_for(control)` (`page_agent.py`), called from the same site in
  `_run_locked` with no behavior change. `test_the_ai_is_asked_about_each_degree_by_name` was
  rewritten to call `agent._question_for(...)` directly for the page's two degree controls (`e1f`,
  `e2f`) and assert the two returned questions differ and match `repeated_entries.describe()` for
  each entry -- a real behavioral assertion against the exact function production code calls,
  replacing the brittle source-text inspection. The invariant was not weakened: the test still fails
  if the two degrees' questions collapse to the same text.
- **IDENTITY FIX:** Added a durable submission-identity-alias mechanism to `job_tracker.py`:
  table `submission_identity_aliases` (`alias_key` PRIMARY KEY, `dedup_key` as the root it belongs
  to), plus `record_submission_identity_alias(primary_key, alias_key)` and
  `submission_identity_group(key)`. `begin_submission_dispatch()` now folds the durable alias group
  into its own alias check inside the same `BEGIN IMMEDIATE` transaction (not only the aliases the
  caller happened to pass), and `apply_flow.refuse_submission_replay()` does the same for the
  pre-flight check. `apply_flow.remember_progress()` -- already called on every pass -- now also
  calls a small new helper, `_remember_submission_identity_alias()`, which records whatever host/path
  the browser is currently on as a durable alias of the run's one bound `submission_key`
  (`assistant.submission_key`, set once at run start from the original posting URL and never
  recomputed from the live page). A login page, an ATS redirect, and a review step visited in the
  same run are therefore all tied to the same effect identity as soon as the run visits them.
- **FAIL-CLOSED IDENTITY RULE:** Two identities are never merged on a guess. `record_submission_identity_alias()`
  refuses to record an alias that already has its own independent dispatch row in `submission_effects`
  (its own, separately-reached history is left untouched rather than folded into a different
  primary's group), and refuses to move an alias that is already recorded under a different root
  (first binding observed wins). Once any key in an identity's alias group has reached `DISPATCHED`,
  `CONFIRMED`, or `UNCERTAIN`, `begin_submission_dispatch()` and `refuse_submission_replay()` both
  find that state through the group and block or convert to `UNCERTAIN` -- regardless of which
  member of the group a particular invocation happens to compute from its own URL. A URL that has
  no recorded alias and no row of its own is not guessed into any existing identity; it is treated as
  its own, separate `NOT_ATTEMPTED` application, which is correct and required for genuinely new
  postings.
- **CROSS-HOST REPLAY RESULT:** Closed for the case the review flagged as reachable: a dispatch that
  becomes `UNCERTAIN` (or reaches `DISPATCHED`/`CONFIRMED`) under the original URL-derived key, where
  the run itself later visits -- or a prior run already visited -- a different ATS host/path for that
  same bound application. That host/path is now durably recorded as an alias, so a later invocation
  started from it finds the existing history and is blocked rather than dispatching again. The
  existing normalization and legacy-alias lookups (tracking parameters, fragments, query ordering,
  trailing slash, host case, HTTP/HTTPS) are unchanged and still checked first. The inherent limit
  acknowledged in Remediation Pass 2 is not fully removed and cannot be without fuzzy matching, which
  remains out of scope: a URL variant the system itself never actually visited or was never told about
  by a recorded alias still cannot be correlated, and is correctly left as its own identity rather than
  guessed into another one.
- **FILES CHANGED:** `job_tracker.py`, `apply_flow.py`, `page_agent.py`, `tests/test_repeated_entries.py`,
  `tests/test_submission_effect_state.py`, `BEHAVIOUR.md`, and this file.
- **NEW TESTS:** `tests/test_submission_effect_state.py` gained nine tests: recording an alias during
  a run from `apply_flow.remember_progress`; a redirect to a different host staying bound to the same
  effect history; a different path being unable to create a fresh authority after `DISPATCHED`; the
  same for `UNCERTAIN` and for `CONFIRMED`; an alias candidate with its own independent history never
  being merged into another identity; a pre-recorded alias not blocking the primary's own first
  dispatch; the `apply_flow`-level pre-flight check (`refuse_submission_replay`) finding a durable
  alias on its own; and same-host normalized URL matching continuing to work. The rewritten degree
  test in `tests/test_repeated_entries.py` replaces the one source-inspection assertion with a
  behavioral one. No live ATS calls; all local synthetic pages and a local SQLite file per test.
- **COLLECTION DELTA:** `1956` node IDs collected before this pass (Pass 2's final collection) to
  `1965` after it: `+9`, exactly the nine new tests above, with the degree test's node ID unchanged
  (rewritten in place, not added or removed). No baseline test disappeared. The one pre-existing
  collection error, `tests/test_ant_select.py` importing `_somewhere_profile` (absent from the
  inherited, locally modified `tests/test_page_agent.py`), is unchanged and unrelated to this pass --
  it was not touched, per the standing instruction not to modify unrelated dirty-worktree tests.
- **TARGETED RESULT:** `tests/test_repeated_entries.py tests/test_submission_effect_state.py tests/test_submission_firewall.py tests/test_auto_submit.py`:
  `99 passed` in 137.40s. `tests/test_page_agent.py tests/test_finding_the_way.py`: `120 passed, 12 failed`
  in 431.72s -- identical in count and in every failing test name to the Pass 2 baseline for this exact
  command; none of the 12 are new.
- **FULL RESULT:** `python -m pytest -q -rs -n auto`: `1944 passed, 3 skipped, 18 failed, 1 error` in
  826.23s. This reconciles exactly against Pass 2's `1934 passed, ..., 19 failed, 1 error`:
  `1934 + 1 (degree test fixed) + 9 (new tests, all passing) = 1944`, and `19 - 1 (degree test fixed) = 18`.
  Skip count (3) and the collection error (1) are unchanged.
- **NEW REGRESSIONS:** None. The 18 remaining failures are the same 12 PageAgent dirty-worktree
  failures and the same 6 `tests/test_failures_catalogue.py` reference failures recorded as
  `UNRESOLVED`/pre-existing throughout Pass 1 and Pass 2 -- verified here by exact test-name match
  against the isolated PageAgent/progression subset run (`120 passed, 12 failed`, identical names),
  not inferred from counts alone.
- **REMAINING HIGH FINDINGS:** Identity continuity across hosts/paths still depends on the system
  itself having observed and durably recorded the redirect, either earlier in the same run or in a
  prior run. A URL variant the system was never shown and never visited remains uncorrelated with any
  existing bound identity -- an inherent limit of the no-fuzzy-matching, no-generic-ATS-identity-service
  constraint given in this remediation's scope, not a newly discovered gap. The 28 baseline-only
  PageAgent node IDs and 15 baseline-only `tests/test_ant_select.py` node IDs recorded as missing from
  the inherited dirty `tests/test_page_agent.py` since Pass 1 remain missing; reconciling that dirty
  test file was out of scope for every pass to date, per the owner's standing instruction not to touch
  unrelated dirty-worktree tests.
- **P0-B2 STARTED:** NO.
