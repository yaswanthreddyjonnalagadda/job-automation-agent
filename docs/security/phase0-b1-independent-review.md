# Phase 0-B1 — Independent Security and Implementation Review

## Review basis

- Approved base SHA: `6fb53e6c7a9bce394e67107c48fa5e47d1041e9b`.
- Review scope: the P0-B1 final-submission containment slice only.
- The current HEAD is the base SHA; the reviewed implementation is an
  uncommitted working-tree snapshot, not a committed branch range.
- The worktree contains unrelated pre-existing local changes. The review
  treated the implementation files and tests listed in
  `phase0-b1-implementation.md` as the P0-B1 scope and did not modify them.
- No live external submission was performed.
- P0-B2 was not started.

## Findings

### BLOCKER — no durable dispatch state or replay protection

P0-B1 has no durable `NOT_ATTEMPTED -> AUTHORIZED -> DISPATCHED ->
CONFIRMED/UNCERTAIN` state machine, and no durable `DISPATCHED` record is
persisted before the browser effect. The implementation record explicitly
states durable in-flight/ambiguous state and idempotency locking are not
implemented.

The gateway's one-use authorization is process/browser-context-local. It
does not establish durable one-use semantics across object recreation,
process restart, duplicate application invocation, browser tabs, or retry
after an uncertain outcome. Following a crash or timeout around the click,
the system cannot prove from durable state whether submission occurred or
reliably prevent a fresh invocation from attempting it again.

This finding follows the explicit review criterion that absence of durable
dispatch state is a P0-B1 blocker. It conflicts with the earlier
implementation-scope instruction that deferred the durable state machine.
That scope conflict does not remove the blocker under this review's stated
acceptance criterion; resolve it before treating P0-B1 as complete.

### LOW — submit classification is vocabulary-bounded

`SubmissionGuardV0` relies on the centralized `safety.SUBMIT_LABEL_RE`
vocabulary. Covered wording includes recognized submit labels, but the guard
cannot be claimed to recognize every semantically final control. In
particular, novel wording such as “Apply”, “Finalize”, “Confirm & Send”,
“Proceed”, “Done”, or localized text is not established as covered unless it
matches that policy. Icon-only controls and neutral-text
`button[type=submit]` / `input[type=submit]` controls are likewise not
established as final solely by their element type. Enter and form submission
are guarded when the final submitter is recognized; JavaScript handlers on
ordinary controls and arbitrary page-owned network effects are outside this
browser-primitive boundary.

This is a coverage limitation, not a demonstrated bypass for the recognized
control vocabulary. The current behavior documentation records the boundary.

### INFORMATIONAL — baseline-specific failing test

The full Windows/Python 3.13.13 run is recorded as `1859 passed, 104 skipped,
1 failed`. The one failure,
`tests/test_safety_rules.py::test_a_previously_submitted_job_is_refused`,
is the same backend-sensitive stale test-double failure recorded in the
Phase 0-A Windows baseline. The approved CI-equivalent Ubuntu/Python
3.13/PostgreSQL baseline was `1837 passed, 104 skipped, 0 failed`; the
P0-B1 full run was not replayed in that Linux/PostgreSQL environment.

### INFORMATIONAL — snapshot rather than committed diff

Since current HEAD equals the approved base SHA and the working tree is
modified, this review evaluates the local worktree snapshot against the base,
not a committed base-to-branch range. This is recorded to make the reviewed
artifact and its provenance explicit.

## Submission choke point and bypass review

The reviewed implementation establishes a deny-by-default browser guard for
recognized final-submit controls and a separate verified auto-submit path.
The relevant reviewed files are `submission_guard.py`,
`browser_automation.py`, `interaction.py`, `page_agent.py`, and
`apply_flow.py`.

| Path or capability | Review classification | Evidence / boundary |
|---|---|---|
| Normal locator click on recognized final control | GUARDED | Context capture guard blocks before page handler |
| Force-click fallback | GUARDED | Denial is surfaced; resilient helper does not continue with force |
| `dispatch_event("click")` | GUARDED | Guard handles recognized final-control click |
| JavaScript `element.click()` | GUARDED | Click capture handler applies |
| Enter-key implicit form submission | GUARDED when final submitter is recognized | Key/form-submit handlers prevent the recognized effect |
| `form.submit()` / `requestSubmit()` | GUARDED for recognized final form submission | Guard wraps/intercepts these browser APIs |
| PageAgent planner/step-counter designation | GUARDED for recognized final controls | PageAgent stops on denial; planner kind and step count do not authorize |
| Vision/model-selected click | GUARDED for recognized final controls | Click still traverses the browser guard |
| Retry/fallback after a recorded denial | GUARDED in reviewed helpers | Resilient click and PageAgent paths stop rather than retry |
| Verified auto-submit | GATEWAY ONLY | Requires structured eligible `AutoSubmitDecision`; one normal click, no forced/synthetic fallback |
| Unrecognized final-control wording or semantics | POSSIBLE BYPASS | Detection is bounded by the current centralized label vocabulary and browser API hooks |
| Arbitrary page-owned network/API submission outside intercepted APIs | NOT REACHABLE by this guard | This slice is not a general network or server-side firewall |

No direct bypass was reported for the recognized labels and the enumerated
browser primitives. The “possible bypass” classification is limited to
semantic controls the vocabulary/API hooks do not identify; labels alone are
not proof that a control is safe.

## One-use authority, durable state, and handoff

- Gateway authorization: one-use within the current in-memory guard/context
  execution. Classification: `IN_MEMORY_ONLY`; not durable or fully enforced
  across restart, recreation, tabs, or duplicate application invocations.
- Dispatch state: absent. Persistence-failure, locked-database, restart,
  crash-before/after-click, timeout-after-click, and unknown-confirmation
  cases have no durable dispatch/reconciliation state to enforce. Automatic
  replay safety is therefore not established.
- Handoff: the implementation disarms the browser guard to permit the human
  to submit manually. The review evidence establishes the disarm behavior,
  but does not establish a durable parked state or prove that every pending
  asynchronous callback/retry is drained before disarm. No concrete race was
  demonstrated in the reviewed tests; the human-only authority property is
  therefore not independently proven under concurrent/pending automation.
  The handoff remains a residual race concern and must not be represented as
  durable isolation.

## Model authority

For recognized final controls, model/planner output, step counters, retry
logic, and vision selection do not themselves authorize submission: the
browser guard denies them unless the verified gateway has granted its
one-use authorization. The gateway checks a structured safety decision
rather than a caller-supplied boolean. This conclusion is limited to
recognized controls and the intercepted browser paths; unrecognized
semantics remain subject to the vocabulary/API-boundary limitation above.

## Test quality and suite delta

The 21 tests in `tests/test_submission_firewall.py` use local synthetic HTML
and browser fixtures rather than live employer sites. The nine
pre-change characterization cases demonstrate unsafe primitive reachability
against those local fixtures. The enforced tests exercise the actual guard
and browser paths for recognized labels, click variants, Enter, planner and
vision attempts, verified gateway, handoff, and ordinary Continue navigation;
they are not solely mocks of the guard.

The reviewed tests do not establish durable persistence-failure, locked-DB,
crash/restart, duplicate-invocation, or uncertain-outcome replay behavior.
The current handoff tests do not establish the absence of a race with
already-pending asynchronous automation. Those gaps align with the blocker
and residual handoff concern above.

Reported full-suite comparison:

| Run | Result |
|---|---|
| Approved CI-equivalent Phase 0-A baseline | 1837 passed, 104 skipped, 0 failed |
| P0-B1 full run, Windows/Python 3.13.13 | 1859 passed, 104 skipped, 1 failed |

The P0-B1 result has 23 more completed test outcomes than the baseline
(`1859 + 104 + 1` versus `1837 + 104`). The implementation report identifies
21 added firewall tests and modified existing tests. It does not provide
before/after collection manifests, removed/renamed test inventory, or an
exact collected-test delta, so disappearance of tests cannot be ruled out
from these totals alone. Skip count is unchanged at 104; the failure identity
is the known Windows-only stale tracker-double failure. A clean
CI-equivalent Linux/PostgreSQL P0-B1 full run was unavailable.

## Scope and dependency boundary

The reviewed implementation adds the browser containment guard and gateway
for the P0-B1 slice. It does not implement SecretProvider, FactBroker,
durable submission state, idempotency locking, or later-phase distributed
components. `safety.py` was not changed. The implementation record lists no
ATS adapter changes. The review found no reported circular dependency or
generic helper granted privileged submission authority; privileged browser
submission remains routed through `JobApplicationAssistant`'s verified
gateway. The durable state omission is nevertheless a blocker under the
explicit review criterion above.

## Summary status

- **SUBMISSION CHOKE POINT:** Enforced for recognized submit-capable controls
  across reviewed browser primitives; not a universal network-effect
  firewall.
- **DURABLE DISPATCH STATE:** Absent; BLOCKER.
- **REPLAY PROTECTION:** In-memory one-use only; incomplete across process,
  browser-context, and invocation boundaries; BLOCKER.
- **HUMAN HANDOFF SAFETY:** Manual submission remains possible after guard
  disarm. No concrete race was demonstrated, but draining/parking pending
  automation before disarm was not established.
- **MODEL SUBMIT AUTHORITY:** Zero for recognized controls absent gateway
  authorization; vocabulary/API-boundary limitation remains.
- **TEST QUALITY:** Useful local synthetic-browser characterization and
  enforcement tests; durable-state and handoff-race cases are absent.
- **FULL-SUITE REGRESSION STATUS:** No new failure identified in the reported
  run; same known Windows failure. The P0-B1 Linux/PostgreSQL replay and exact
  before/after collection manifest are unavailable.
- **P0-B1 SCOPE STATUS:** Submission-firewall subset implemented. Under this
  review's explicit durable-state acceptance criterion, P0-B1 is not
  complete. The earlier deferral of the state machine conflicts with that
  criterion and requires owner resolution.
- **BLOCKERS:** Missing durable pre-dispatch state and replay protection.
- **FINAL RECOMMENDATION:** P0-B1 REQUIRES REMEDIATION

## Durable Remediation Re-Verification

This section supersedes the preceding point-in-time findings for the current
remediation snapshot; the earlier findings remain as the historical review
record.

### PRE-DISPATCH DURABILITY

**PASS for the verified automated final-submit path.** The gateway validates
the structured authorization and reads the persisted application record before
calling `begin_submission_dispatch()`. In `job_tracker.py`, that method uses
`BEGIN IMMEDIATE`, checks the durable state, writes `AUTHORIZED`, changes the
row to `DISPATCHED`, appends `SUBMISSION_DISPATCHED`, then returns only after
the SQLite context commits. `submission_guard.py` performs element
authorization and `locator.click()` only after that call returns. The browser
effect therefore cannot occur before a successful durable commit through this
gateway.

The state and `SUBMISSION_DISPATCHED` event share the transaction. A state
write, event write, or commit exception rolls back and prevents the click; a
database lock likewise fails before dispatch. If the browser effect throws
after commit, the gateway attempts to mark `UNCERTAIN`; if the process stops
before that write, durable `DISPATCHED` remains and the next invocation
reclassifies it as `UNCERTAIN` and blocks replay. This review did not find a
pre-commit browser submit effect in the verified gateway.

### STATE MACHINE

**PASS, with persisted absence representing `NOT_ATTEMPTED`.** A first
authorized attempt persists `AUTHORIZED`, then `DISPATCHED`. Terminal
transitions are `DISPATCHED -> CONFIRMED` or `DISPATCHED -> UNCERTAIN`.
`UNCERTAIN -> CONFIRMED` requires the explicit `reconciled=True` argument.
The tracker rejects other outcome states and invalid transitions; there is no
automatic transition from `DISPATCHED` to `NOT_ATTEMPTED` or from `UNCERTAIN`
to `AUTHORIZED`.

### REPLAY PROTECTION

**FULLY DURABLE for repeated use of the same configured SQLite store and
deduplication key.** The persistent state, not a guard-object flag, controls
dispatch. The new tests recreate `JobTracker`, guard, and browser context and
show that `DISPATCHED`, `CONFIRMED`, and `UNCERTAIN` each refuse another click.
`apply_flow.refuse_submission_replay()` also converts an interrupted
`DISPATCHED` state to `UNCERTAIN`; retrying the same application key remains
blocked. This includes new tabs/contexts and repeated invocation when they
resolve to that same store/key.

### AMBIGUOUS OUTCOME

**PASS for handled exceptions and restart-equivalent interruption.** A failed
click after dispatch is treated as uncertain; absent or ambiguous confirmation
does not authorize another click. A process interruption leaves `DISPATCHED`,
which is converted to `UNCERTAIN` on replay. `SubmissionProbe` exposes state,
application status, URL/title, confirmation text, application ID, and safety
events, but has no submit/click method. This is local read-only inspection,
not a broader reconciliation workflow.

### CONFIRMATION EVIDENCE

**External click success alone does not mark the application confirmed.**
`apply_flow.submit_verified()` waits for page/email evidence, runs
`safety.verification_status()`, and writes `CONFIRMED` only when the result is
`STATUS_SUBMITTED`; missing/unknown evidence writes `UNCERTAIN`.
Evidence is described as page or email confirmation. **Residual limitation:**
the confirmation phrase matching and email evidence were not independently
characterized against false-positive synthetic pages in the remediation tests.
The possibility of unrelated page text matching a confirmation phrase remains
unquantified; confirmation evidence should not be treated as universally
strong without that coverage.

### HANDOFF QUIESCENCE

**PASS for the reviewed shared-lock paths.** PageAgent execution/progression,
vision actions, and the verified submission gateway use the assistant's
execution lock. `human_handoff()` obtains that lock, parks execution, writes
`MANUAL_HANDOFF_STARTED`, then deactivates the guard. A failed event write
leaves the execution parked and guard armed. The regression test holds
PageAgent progression in flight and verifies handoff cannot finish until that
work drains; subsequent PageAgent/gateway attempts are refused. Resume activates
the guard before restoring the active state. I found no race in these covered
paths where handoff disarms the guard and pending locked work then clicks.
This does not prove quiescence for arbitrary code outside these serialized
assistant paths.

### APPLICATION IDENTITY

The durable row is keyed by the existing URL-derived `dedup_key`; same
normalized URL and configured SQLite path provide durable continuity across
restarts. The remediation does not use an ATS requisition/application ID as a
canonical durable key. The review did not establish that redirects, materially
different query strings, or other URL variants for the same real application
always normalize to the same key. **Residual risk: HIGH** until representative
ATS URL variants are shown to preserve identity or a stronger existing
application identity is used. A changed key would not find the prior terminal
row.

### SAFETY EVENT QUALITY

The SQLite event table has update/delete triggers, and dispatch state plus its
dispatch event commit atomically. The reviewed gateway call sites pass
non-sensitive reason/state/evidence-kind metadata and do not intentionally
serialize credentials or candidate profiles.

**Finding — metadata is not allowlisted.** `record_submission_safety_event()`
accepts an arbitrary dictionary and JSON-serializes it; the store does not
restrict event kinds to an enum or payload keys/values to a schema. Thus the
current call sites appear to avoid sensitive content, but the persistence API
does not enforce the required structured allowlist. This is a **MEDIUM**
remaining issue: a future caller could append sensitive payload data.

### DURABILITY TEST QUALITY

The tests exercise actual SQLite persistence: they begin dispatch, create a
new tracker against the same file, recreate a browser context and guard, and
assert that the click count remains zero for `DISPATCHED`, `CONFIRMED`, and
`UNCERTAIN`. Other tests inspect durable state/event visibility from inside
the click spy, inject write/DB-lock failures, exercise ambiguous outcomes,
probe read-only data, retry the same key, and test handoff draining. These are
not merely assertions against an in-memory flag.

The tests do not simulate an OS process kill at the precise post-commit /
pre-click boundary, independently inject a transaction commit failure, or
characterize false-positive confirmation evidence. Restart behavior is tested
using object/store recreation and a restart-equivalent stale-state transition,
not a separate subprocess. These gaps do not negate the transaction ordering,
but limit evidence for crash and confirmation edge cases.

### COLLECTION DELTA

The exact manifests contain 1,941 baseline node IDs and 1,951 current
collectable node IDs, with 53 current-only and 43 baseline-only IDs (net
increase 10). The current-only IDs comprise 31 in
`tests/test_submission_firewall.py`, 17 in
`tests/test_submission_effect_state.py`, 3 source-control-character checks,
and 2 PageAgent tests.

The 15 baseline-only `tests/test_ant_select.py` IDs are not evidence of deleted
test functions: that module now fails collection because its import of
`_somewhere_profile` cannot resolve from the current locally modified
`tests/test_page_agent.py`. The other 28 baseline-only IDs are PageAgent tests
absent from the current `tests/test_page_agent.py`; this is an actual
test-manifest drift in the pre-existing dirty test file, not a rename
established by the manifests. Several missing PageAgent functions are also
referenced by the failure catalogue, causing catalogue-integrity test
failures. The disappearance is explained by the current dirty test tree, but
the corresponding baseline test coverage is not present in this snapshot.
This prevents treating the complete suite as regression-clean.

### FULL-SUITE FAILURE ATTRIBUTION

The observed full result is `1,928 passed, 3 skipped, 19 failed, 1 error`.
The run occurred on Windows/Python 3.14.5 against the dirty worktree; it was
not a controlled same-environment rerun of the clean pinned-base suite.

| Failure category | Classification | Evidence and confidence |
|---|---|---|
| Collection error: `tests/test_ant_select.py` imports missing `_somewhere_profile` | CAUSED_BY_UNRELATED_DIRTY_WORKTREE | The import target is absent from the current locally modified `tests/test_page_agent.py`; collection of the baseline manifest included the module's 15 tests. This is test-tree integrity, not a production submission-state failure. |
| Six failure-catalogue checks referencing absent PageAgent tests | CAUSED_BY_UNRELATED_DIRTY_WORKTREE | The named regression functions are absent from the current locally modified PageAgent test file. These are test-manifest/catalogue failures, not evidence of a runtime submission regression. |
| Thirteen PageAgent behavior failures | UNRESOLVED | Failures include older auto-submit expectations and assertions concerning answers, button actions, retries, and owner handoff. The current dirty test file differs substantially from the baseline, and no same-environment run against the pristine baseline was available to separate changed expectations from P0-B1 locking/handoff behavior. They cannot be dismissed as pre-existing solely from the implementer's report. |

### NEW BLOCKERS

- **BLOCKER — baseline PageAgent coverage is missing from the current test
  tree.** The 28 baseline-only PageAgent node IDs are absent from the dirty
  current test file; 15 more baseline IDs are inaccessible because of the
  collection import error. This prevents a complete regression determination
  and must be reconciled before claiming full-suite readiness.
- **MEDIUM — safety-event payload API does not enforce an allowlist.**
- **HIGH — same-application identity stability across URL variants is not
  demonstrated.** Because replay protection is keyed by URL-derived identity,
  a variant that generates a distinct key may not see the prior effect record.
- The 13 PageAgent behavior failures remain **UNRESOLVED** pending controlled
  baseline comparison; any failure caused by P0-B1 behavior is a regression
  unless the expectation is explicitly superseded with justification.

### REMAINING LIMITATIONS

- Submission classification remains bounded by known labels and structural
  submit semantics; no universal network-effect firewall is claimed.
- Durable replay protection is local to the configured SQLite store and
  current URL-derived key, not distributed or cross-device.
- False-positive confirmation handling and process-kill crash windows lack
  direct synthetic/subprocess coverage.
- No FactBroker, Question Ontology, distributed worker/capability system,
  multi-user tenancy, or P0-B2 work was introduced.

**FINAL RECOMMENDATION:** P0-B1 REQUIRES FURTHER REMEDIATION

## Final Pass-3 Verification

Claims were not taken on trust. Each was re-derived from the current code and,
where a code read alone could not settle it, from a direct empirical trace
against a real SQLite-backed `JobTracker` (temp-file database, no mocks) --
reported inline below.

**DEGREE REGRESSION: RESOLVED.** `PageAgent._question_for(control)`
(`page_agent.py`) is the exact three-line block that used to sit inline in
`_run_locked`: look up `self._entries[control.ref]`, describe it with
`repeated_entries.describe()`, and format `"{question} (for: {description})"`
when inside an entry. It is a pure extraction -- the call site in
`_run_locked` passes the same `ctrl` and nothing else changed around it -- so
runtime behavior is unaltered, and it is not a test-only path: production
code calls the same method the test calls. The rewritten test
(`tests/test_repeated_entries.py::test_the_ai_is_asked_about_each_degree_by_name`)
calls `agent._question_for(boxes["e1f"])` and `agent._question_for(boxes["e2f"])`
and asserts the two results differ and equal `repeated_entries.describe(...)`
for each entry -- a genuine behavioral assertion against the function
production code actually calls, not source-text inspection. Two different
degrees cannot collapse into the same prompt: each goes through a distinct
`entry` keyed by its own `control.ref`.

**ALIAS DURABILITY: MOSTLY SOUND, ONE GAP.** `submission_identity_aliases` is
a plain SQLite table with no in-memory cache in `JobTracker`; every method
opens its own connection. Verified empirically that an alias recorded by one
`JobTracker` instance is visible, and still blocks dispatch, from a second and
third freshly constructed `JobTracker` against the same file (process-restart
equivalent) -- see the trace in this review's working notes; not yet backed by
a dedicated automated test (the existing recreation test,
`test_terminal_state_survives_tracker_and_browser_context_recreation`, covers
terminal effect-state recreation, not the alias table specifically). Alias
ownership is deterministic and duplicate/conflicting ownership fails closed
for two of the three cases: an `alias_key` with its own row in
`submission_effects` is correctly left untouched (`own_history` check), and an
`alias_key` already recorded under a *different* root is correctly left alone
(`already_aliased` check). The third case is not handled: see CONFLICTING
IDENTITY below. Alias lookup cannot grant dispatch authority by itself --
`record_submission_identity_alias()` only ever writes to
`submission_identity_aliases`, never to `submission_effects`; only the
verified gateway's `begin_submission_dispatch()` writes a dispatch row, and
alias membership is consulted there only to add a possible block, never to
remove one. `begin_submission_dispatch()`'s alias check runs inside the same
`BEGIN IMMEDIATE` transaction as the state read/write, so there is no
TOCTOU window between the alias-group read and the dispatch decision.

**CROSS-HOST/PATH REPLAY: RESOLVED for the scenario this pass targets.**
Traced directly against a real `JobTracker`: `AUTHORIZED -> DISPATCHED ->
UNCERTAIN` under an application's own key, then `record_submission_identity_alias`
for a second URL differing in host and path, then a simulated "later
invocation" calling `apply_flow.refuse_submission_replay()` on that second
URL's own key *with no explicit `aliases` passed* -- it found the durable
group on its own and returned `UNCERTAIN`, converting the stale `DISPATCHED`
state exactly as the single-key case already did. Repeated for `DISPATCHED`
and `CONFIRMED` via the suite's own
`test_a_different_path_after_dispatch_cannot_create_a_fresh_authority` and
`test_confirmed_then_alternate_url_blocks_a_second_dispatch` -- both pass, and
both raise `RuntimeError` rather than permitting a click. No reachable second
effect was found for this scenario in either `begin_submission_dispatch()` or
`refuse_submission_replay()`. Scheme/query/tracking-parameter/fragment
continuity is unchanged, pre-existing normalization (Remediation Pass 2),
re-confirmed still passing.

**CONFLICTING IDENTITY: PARTIALLY CORRECT -- one real gap found and
reproduced.** `record_submission_identity_alias()`'s guard against merging an
alias that "already has its own independent effect history" checks only the
`submission_effects` table (an actual dispatch row), not whether the
candidate `alias_key` is *already a root* in `submission_identity_aliases`
with dependents of its own but no dispatch yet. Reproduced directly: with
application B pre-recording its own alias (e.g. a login redirect) under its
own root, and B not yet dispatched, a *second, unrelated* application A can
call `record_submission_identity_alias(root_A, root_B)` successfully --
annexing B's root into A's group. B's own dependent alias is now orphaned
(its group still resolves correctly to B's root, but B's root's group now
resolves to A instead, so the two are no longer symmetric). When B
legitimately dispatches afterward, that write still lands correctly on
`root_B`'s own row -- but a *subsequent, completely unrelated* dispatch
attempt by A under `root_A` is then blocked by B's state, because A's group
now includes `root_B`. Reproduced end-to-end: B dispatches normally; A's own,
independent `begin_submission_dispatch(root_A)` then raises
`RuntimeError: ... blocked by durable state DISPATCHED` despite A never
having touched B's application. The reverse direction was checked and does
not occur: this cannot cause A to bypass a block B already holds, create a
dispatch row for A it would not otherwise have earned, or erase B's own
history -- B's row is never read from, written to, or deleted by A's claim.
The demonstrated effect is confined to **false replay-blocking of an
unrelated application** -- fail-closed, not fail-open. Per this review's own
instruction to classify over-binding that merely causes conservative parking
as a limitation rather than a blocker, this is reported as a HIGH finding to
fix, not a blocker.

**ALIAS GROUP COMPLETENESS: mostly sound for the intended, flat usage;
incomplete in the annexation case above and for one explicitly-named
redirect stage.** `record_submission_identity_alias()` always resolves
`primary_key` to its current root before inserting, so under normal,
single-application use the graph stays flat (one root, several direct
aliases) regardless of insertion order -- verified with a four-URL chain
(posting, ATS-application, review) recorded in sequence and a fresh
`submission_identity_group()` read correctly returning all of them together.
Two completeness problems were found, not in the grouping arithmetic itself
but in its edges: (1) the annexation scenario above leaves the group
asymmetric -- a sub-alias's own group lookup does not discover a parent that
was annexed into its root afterward; and (2) tracing the exact chain this
task names -- *job posting -> login host -> ATS host -> application path ->
review path* -- the **login-host hop was never recorded**. `apply_flow.py`'s
new alias-recording call is placed inside `remember_progress()`, gated by the
same `worth_returning_to(url)` check already used there for an unrelated
purpose (where to reopen the browser for a human); that function's exclusion
list includes any URL containing `/login`, `signin`, or `sign-in`. A
genuine, distinct login-host URL in a real redirect chain is therefore
silently skipped for alias purposes, not just for "worth resuming at."
Practical exposure is narrowed by the fact that `last_page_url` (what Resume
and any future invocation would actually be seeded from) is written through
the identical filter, so a later invocation is in practice never started
from a login URL either -- but the mechanism does not establish the
continuity this task named for that specific stage, and that should not be
assumed fixed without a dedicated check if the `last_page_url` seeding logic
ever changes. Store recreation does not lose any already-recorded group (the
alias-durability trace above), and no transitive/indirect alias chain is
intentionally created by the normal path.

**HOSTILE/UNRELATED NAVIGATION EFFECT: safe direction confirmed; one
availability gap.** `record_submission_identity_alias()` never deletes or
updates an existing `submission_effects` or `submission_identity_aliases`
row -- it only ever inserts a brand-new alias row, and only when both guards
pass. An unrelated or adversarial URL visited mid-run cannot erase prior
submission history (nothing deletes those rows), cannot create fresh dispatch
authority (only `begin_submission_dispatch()` writes `submission_effects`,
and alias membership only adds blocking, never removes it), and cannot
rebind an *already-dispatched* application's durable history out from under
it (the `own_history` check correctly refuses to touch a key with its own
row). The one demonstrated over-binding effect -- an unrelated, not-yet-
dispatched application's root being annexed -- causes conservative parking of
the *victim* of the annexation, never a second final submission; classified
as a LIMITATION per this review's own instruction, not a blocker.

**DURABLE STATE INVARIANTS: unchanged.** Line-by-line comparison of
`begin_submission_dispatch()` shows the only change is the `alias_keys`
computation now also folding in `self._identity_group(conn, dedup_key)`; the
state-machine body beneath it (`AUTHORIZED -> DISPATCHED`, blocking on
`DISPATCHED`/`CONFIRMED`/`UNCERTAIN`, raising on an invalid persisted state)
is untouched. The full pre-existing durable-state, firewall, and auto-submit
suites (99 tests: `test_submission_effect_state.py`,
`test_submission_firewall.py`, `test_auto_submit.py`,
`test_repeated_entries.py`) pass unmodified in their original assertions.
Storage-failure-prevents-effect, replay-state-blocks-submission, and
reconciliation-probe-has-no-submit-method were not re-opened without
regression evidence, per instruction, and none was found.

**HANDOFF INVARIANT: not touched by this pass.** `browser_automation.py`'s
`human_handoff()`/`resume_automation()` and `interaction.py` carry only
Remediation Pass 1/2 changes; Pass 3 did not edit either file. No regression
evidence exists for the handoff-drain property, so it is not re-opened here.

**NEW TEST QUALITY: genuine, against real durable storage; some gaps
identified above are not yet covered.** All nine new tests in
`tests/test_submission_effect_state.py`, plus the rewritten degree test,
construct or reuse a real `JobTracker` backed by a temp-file SQLite database
(via the file's existing `setup` fixture or a fresh `tmp_path`) -- none mocks
`record_submission_identity_alias`, `submission_identity_group`, or
`begin_submission_dispatch`. They cover: alias recording through
`apply_flow.remember_progress` during a simulated run; a cross-host redirect
staying bound to one effect history; `DISPATCHED`, `UNCERTAIN`, and
`CONFIRMED` each blocking a second dispatch reached only through a recorded
alias; an alias candidate with its own independent history *not* being
merged; a pre-recorded alias not blocking the primary's own first dispatch;
the `apply_flow`-level pre-flight check finding a durable alias with no
explicit `aliases` argument; and same-host normalization continuing to work.
Not covered by any test: alias-table survival across store/process
recreation specifically (verified manually in this review, not asserted in
the suite), the root-with-dependents annexation gap found in this review, and
the login-host chain gap found in this review.

**COLLECTION ACCOUNTING: confirmed.** Regenerated collection:
`1965` node IDs collected, `1` error -- the same pre-existing
`tests/test_ant_select.py` import of `_somewhere_profile` (absent from the
inherited, locally modified `tests/test_page_agent.py`), unrelated to this
pass and not modified by it. `1965 - 1956 = 9`, matching the nine new tests
exactly; the degree test's node ID is unchanged (rewritten in place). The
full-suite arithmetic reconciles exactly against the Pass 2 baseline:
`1934 passed + 1 (degree test now passing) + 9 (new tests) = 1944`; `19 failed
- 1 (degree test fixed) = 18`; skip count (3) and the collection error (1)
unchanged. The isolated PageAgent/progression subset
(`tests/test_page_agent.py tests/test_finding_the_way.py`) reproduced
`120 passed, 12 failed` with the identical 12 failing test names recorded in
Pass 2/the controlled attribution -- verified by name, not inferred from the
count alone.

**NEW REGRESSIONS: none in the existing suite.** No previously-passing test
now fails, and no baseline test disappeared. This review did, however, find
one defect in the *new* alias mechanism itself (the root-annexation gap
above) that has no existing test and was not caught by the full suite,
because no existing test constructs two independent, pre-dispatch
applications that collide on a URL. It is newly introduced by Pass 3 (the
table it affects did not exist before), but it is not a regression against
prior passing behavior, since no prior test exercised this path.

**BLOCKERS: none.** No path was found, in this review, from any of the
above findings to a second final-submission effect, an erased submission
history, or a bypass of an existing block. Every demonstrated failure mode
of the alias mechanism is conservative (over-blocking), which this review's
own instructions classify as a limitation rather than a blocker.

**HIGH FINDINGS:**
1. `record_submission_identity_alias()` does not check whether a candidate
   `alias_key` is already an established root with its own recorded aliases
   (only whether it has its own `submission_effects` row). A second,
   unrelated, not-yet-dispatched application whose run visits the same URL as
   another application's root can silently annex that root, leaving the
   annexed application's own, previously-recorded aliases pointing at it
   asymmetrically and exposing the annexing application to the annexed
   application's later replay-blocked state. Demonstrated to only ever cause
   false conservative blocking, never a second dispatch.
2. The login-host stage of the explicitly required redirect-continuity chain
   (posting -> login -> ATS -> application -> review) is not recorded as an
   alias, because the recording hook in `apply_flow.remember_progress()`
   reuses `worth_returning_to()`, whose exclusion list (built for the
   unrelated "where to reopen the browser" decision) also excludes `/login`
   and sign-in URLs. Practical exposure today is narrowed because
   `last_page_url`/Resume is seeded through the identical filter, so a later
   invocation is not currently started from a login URL either -- but the
   mechanism does not itself establish the continuity this task named for
   that stage.

**LIMITATIONS (not blockers):**
- Identity continuity depends on the system itself having observed and
  durably recorded the relevant URL, in this run or an earlier one; a URL
  variant never visited or recorded remains its own, uncorrelated identity --
  inherent to the no-fuzzy-matching, no-generic-ATS-identity-service scope
  given for this remediation, not a new gap.
- The alias graph is single-hop by construction and can become asymmetric
  under the annexation scenario in HIGH finding 1; the intended, single
  -application, directly-recorded-alias case remains flat and correct.
- Alias-table durability across process/store recreation was verified
  manually in this review but has no dedicated automated test.
- The 28 baseline-only PageAgent node IDs and 15 baseline-only
  `tests/test_ant_select.py` node IDs missing from the inherited dirty
  `tests/test_page_agent.py` since Pass 1 remain missing; reconciling that
  file remains out of scope per the owner's standing instruction.
- The 12 PageAgent and 6 failure-catalogue dirty-worktree failures remain
  unresolved and unaffected by Pass 3; they are outside this blocker decision
  per this task's own instruction, since Pass 3 did not change their
  behavior (verified by exact name match, not only by count).

**FINAL RECOMMENDATION:** P0-B1 READY FOR ADVERSARIAL REVIEW
