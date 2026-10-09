# Phase 0-B3 — Durable Checkpoint & Resume Reconciliation

Base: `codex/architecture-reliability` at `2a6189e` (P0-B1 submission-replay/containment
hardening + P0-B2 account/authentication/verification hardening, both merged).
Branch: `fix/durable-checkpoint-recovery-hardening`.

See `docs/security/phase0-b3-discovery.md` for the full pre-implementation audit this phase
is built on. This document covers what was built, why, and what was deliberately left alone.

## 1. What discovery found, and what this phase reuses rather than rebuilds

Discovery (full detail in `phase0-b3-discovery.md`) found a resume mechanism today that is,
in its entirety, "reopen `last_page_url` cold, read the live DOM from scratch." Every
in-memory `PageAgent` attribute — `written`, `history`, `_entries`, `_created_at`, and the
rest — dies with the process; nothing resembling a checkpoint exists anywhere in code, only
as an aspiration in `docs/AGENT_ARCHITECTURE.md`. It also found that almost everything a
checkpoint would need to interoperate with *already exists and is already correct*:

- **P0-B1's submission-effect state machine** (`AUTHORIZED`/`DISPATCHED`/`CONFIRMED`/
  `UNCERTAIN`, `job_tracker.py`, `submission_guard.py`) is durable, fails closed on
  ambiguity, and is the sole authority on whether an application was submitted. **Unmodified
  by this phase.**
- **P0-B2's account-state machine** (`account_state.read_state()`) always re-derives from a
  fresh snapshot, never trusts a cached belief, and is the sole authority on sign-in/MFA/
  CAPTCHA state. **Unmodified by this phase.**
- **`login_guard.py`** already gives sign-in attempts, code reads, and password resets a
  durable, cross-restart, per-account rate limit and sticky hold. **Unmodified.**
- **Live-DOM re-checks** already make document re-upload and repeated-entry rebuilding safe
  across a restart (the DOM, not memory, is consulted first). **Unmodified.**
- The browser's **persistent profile directory** already carries session/cookie continuity
  across a restart. **Unmodified.**

The one genuine, confirmed gap: **account creation** has no durable guard at all. The
existing in-memory `_created_at` set stops a *second call on the same `PageAgent` object*
from pressing Create Account twice (`account_state.py`'s "the new-account form is still
showing after the account was created" path) — but it is empty again the moment the process
restarts, and nothing durable takes its place. This phase closes exactly that gap, plus adds
the general checkpoint/reconciliation infrastructure the task asked for, built so a second
non-idempotent action could be wired into the same lifecycle later without redesigning it.

## 2. The canonical checkpoint (`checkpoint.py`)

`ApplicationCheckpoint` — a frozen dataclass, `schema_version` 1:

```
application_key, dedup_key, employer, ats, checkpoint_id, created_at, code_version,
page_url, page_identity {host, step_indicator}, verified_stage, account_state,
completed_controls, uploaded_documents {kind: sha256}, last_verified_action,
pending_action, uncertain_actions, handoff_reason, submission_effect_state
```

No field is, or will ever by convention be, a secret: no password, OTP, magic-link token,
session cookie, or private browser storage. A guard-rail test
(`test_no_secret_shaped_field_exists_on_the_checkpoint`) asserts no field name contains
`password`/`otp`/`token`/`cookie`/`secret`/`credential`, so a future edit that adds one is
caught immediately rather than discovered later.

`checkpoint.parse(payload)` classifies any stored payload as `COMPATIBLE` /
`MIGRATION_REQUIRED` / `UNSUPPORTED` and **never raises** — a malformed or future-schema
payload is evidence that could not be used, not a reason to crash the workflow. `_migrate()`
exists as its own function, deliberately empty (schema 1 is the first schema; there is
nothing older to migrate from), so the first real migration has one obvious place to grow.

## 3. Checkpoint identity, tied to the P0-B1 replay identity, not a sixth scheme

Discovery found application identity fragmented across five independent schemes (see
`phase0-b3-discovery.md`'s "load-bearing findings"). Rather than add a sixth, the checkpoint
is filed under exactly the identity P0-B1's replay protection already uses: `JobTracker`'s
canonical-root resolution (`_resolve_root`, the same alias graph
`begin_submission_dispatch` walks). `write_checkpoint`/`read_checkpoint` both resolve through
it, so a checkpoint written from any alias of an application (a login/SSO-redirect host
discovered mid-flow) is read back from every other alias too —
`test_checkpoint_identity_is_shared_with_the_submission_effect_identity_group` proves this
with the real alias graph. `PageAgent._application_key()` prefers `assistant.submission_key`
(the P0-B1 identity), falling back to the application-row key only when no submission key was
ever bound.

## 4. Durable storage (`job_tracker.py`, `db.py`)

One new table, `application_checkpoints` (`application_key` PK, `dedup_key`,
`schema_version`, `code_version`, `payload`, `created_at`, `updated_at`), single current row
per identity group — a checkpoint is the *latest* evidence, not a history (the append-only
`submission_safety_events`/`application_events` tables remain the audit trail for history).
`write_checkpoint` replaces the row in one `INSERT ... ON CONFLICT DO UPDATE` inside one
`BEGIN IMMEDIATE` transaction: a reader can never observe a half-written payload.
`read_checkpoint` treats a corrupt/unparseable payload the same as "no checkpoint" — logged,
never raised.

`PostgresTracker.write_checkpoint`/`read_checkpoint` delegate to a local `JobTracker`, via the
same `_local_submission_tracker()` P0-B1's submission-effect methods already use. This is the
same residual assumption P0-B1 already made and documented (checkpoint and submission-effect
durability both always live in one local SQLite file, never in Postgres, whichever tracker
backend is configured) — not a new gap introduced here, and recorded honestly rather than
silently. `tests/test_tracker_without_a_server.py::test_the_sqlite_tracker_does_everything_the_postgres_one_does`
already asserts `PostgresTracker`'s public method set is a subset of `JobTracker`'s, so this
parity is structurally enforced going forward, not just true today.

## 5. Durable / transient / secret classification

| Attribute | Class | Disposition |
|---|---|---|
| `last_page_url`, `applications.status`, `documents`, `form_answers`, `learned_form_recipes`, `ats_accounts` | Durable (pre-existing) | Unchanged |
| `submission_effects`, `submission_identity_aliases`, `submission_safety_events` | Durable (pre-existing, P0-B1) | Unchanged |
| `login_guard`'s `data/_login_attempts.json` | Durable (pre-existing, P0-B2) | Unchanged |
| `application_checkpoints` (new) | Durable | `page_identity`, `verified_stage`, `account_state` (kind name only), `completed_controls`, `uploaded_documents` (sha256 only), `pending_action`, `handoff_reason` -- all non-secret, all advisory |
| `PageAgent._created_at`, `._signed_in_at`, `._entries`, `.written`, `.history`, `._attached_here`, `.resume_uploaded`, `._pressed`, `._opened_entries`, `._woken`, `._shapes`, `._pending_memories`, `.owner_answers`, `.corrected`, `.account_blocker`, `._paused_state` | Transient | Left exactly as they are: every one either resets safely on a fresh process (rebuilt from the live DOM or re-derived) or already has a narrower durable backstop elsewhere (`owner_answers` → `tracker.record_answer`; sign-in/code/reset attempts → `login_guard`) |
| Passwords, OTPs, magic-link tokens, session cookies, browser storage | Secret | Never enter the checkpoint, never will by the guard-rail test in §2 |

The one transient attribute whose own comment calls out a cross-restart risk, `_created_at`,
now has the durable backstop described in §6 — not by persisting the set itself, but by
giving the one action it guards its own small lifecycle.

## 6. Action lifecycle: account creation (the one closed gap)

`PageAgent` gained three methods (`page_agent.py`, just before `sign_in_step`):
`_pending_account_creation(host)`, `_mark_account_creation_dispatched(host)`,
`_resolve_pending_account_creation(host)`. The lifecycle:

```
(about to press Create Account)
  -> _mark_account_creation_dispatched(host)   durable write, BEFORE the click
     checkpoint.pending_action = "account_creation_dispatched@<host>"
(the click happens)
(next page read, same process or a resumed one)
  -> state.kind != CREATE_FORM?  -> _resolve_pending_account_creation(host)  (durable clear)
  -> state.kind == CREATE_FORM and pending still set?
       -> account_blocker set, Create Account is NOT pressed again
```

This is deliberately **not** a second submission-style transaction system — final submission
stays exclusively P0-B1's (`SubmissionGuardV0`/`begin_submission_dispatch`). It is a small,
narrow, fail-closed lifecycle for the one other consequential action discovery confirmed has
none. It reads fresh from durable storage on every call, no in-memory cache — the same
restart-safety shape `login_guard.py` already uses. It is purely additive: every entry point
is guarded by `hasattr(self.tracker, ...)`, so a test double or a run with no tracker behaves
exactly as before (`test_no_tracker_does_not_block_account_creation`).

It also does not duplicate the *existing* same-process guard
(`account_state.py`'s `memory.created` → `FOR_OWNER`, which already blocks a second call on
the same `PageAgent` object and is left untouched) — it only engages for the case that guard
cannot cover: a second, independent `PageAgent` instance that shares nothing in memory with
the first. `tests/test_resume_after_interruption.py` proves both the new cross-process block
and that it releases correctly once live evidence shows the create form is behind us, and that
a later, legitimate Create Account still proceeds once resolved.

## 7. Resume reconciliation (`recovery.py`)

`PageIdentity(host, step_indicator)` — deliberately coarse, not a full-DOM hash (the task's
own warning). `step_indicator` reuses `state_machine.step_indicator_text()`, factored out of
the existing loop-detection circuit breaker's own fingerprint so there is one selector list,
not two that could drift apart.

`reconcile(stored_payload, live_identity, live_account_state_kind,
live_submission_effect_state, application_present)` is a pure function — evidence gathering
is the caller's job, exactly as `account_state.read_state()` takes a snapshot string rather
than a page. Outcome precedence (tested exhaustively in
`tests/test_recovery_reconciliation.py`):

1. `live_submission_effect_state == "CONFIRMED"` → **SUBMITTED**, unconditionally — wins even
   over a checkpoint that says otherwise, or none at all.
2. No stored checkpoint → **NO_CHECKPOINT** (the pre-B3/migration case, §9).
3. Checkpoint unparseable / future schema → **UNSUPPORTED**.
4. `application_present` is False → **APPLICATION_NOT_FOUND**.
5. `live_account_state_kind` not in `{SIGNED_IN, NONE, ""}` → **AUTH_REQUIRED** — current,
   live account/auth evidence always outranks whatever the checkpoint recorded (task §38's
   literal example: checkpoint `AUTHENTICATED` + live `SIGN_IN_REQUIRED` → `SIGN_IN_REQUIRED`
   wins; proven directly by test).
6. Checkpoint host recorded and disagrees with the live host → **DIFFERENT_APPLICATION**.
7. Step indicators equal → **MATCH**. Both parse as `N of M` with the same `M` → **AHEAD** /
   **BEHIND** by comparing `N`. Anything else (unparseable, or `M` disagrees) →
   **OUTCOME_UNKNOWN** — the explicit "default ambiguous identity to UNKNOWN" the task asks
   for, never a guessed direction.

`recovery.UNSUPPORTED` is literally `checkpoint.UNSUPPORTED` (one shared string, imported
rather than redefined) so a schema-unsupported checkpoint and a reconciliation-unsupported
outcome are reported with the same word, not two that happen to mean the same thing.

## 8. Where checkpoints are written and read

- **`apply_flow.write_recovery_checkpoint()`** — called once per `agent.run()` pass returning
  from `run_page_agent()` (the same once-per-pass boundary `remember_progress` already writes
  `last_page_url` from), not on every DOM read inside that pass. Best-effort: a write failure
  is logged, never raised — a checkpoint speeds up a resume, it is not required for the run to
  proceed.
- **`apply_flow.log_recovery_reconciliation()`** — called once at resume time, right after the
  page a resumed run will actually start from is settled (after the existing
  `shows_the_application()` fallback has already run). **Purely diagnostic**: it logs the
  reconciliation outcome (`RECOVERY_<OUTCOME>`) and records it as an application event; it
  does not change navigation. The existing `shows_the_application()` fallback — unmodified —
  remains what actually governs where a resumed run lands. This satisfies the task's
  reconciliation-logging requirement (§28) while adding zero risk to the already-well-tested
  navigation path.
- **`PageAgent._mark_account_creation_dispatched` / `._resolve_pending_account_creation`** —
  §6's account-creation lifecycle, the one place a checkpoint write gates an action.

## 9. Migration / pre-B3 applications

`read_checkpoint()` returns `None` for any application never checkpointed —
`reconcile()` reports `NO_CHECKPOINT` for exactly that case, and the caller's existing
behavior (inspect `last_page_url`/live page fresh) is completely unchanged; the next
`write_recovery_checkpoint()` call creates the first checkpoint. No existing application
record needs a migration step, and none can be crashed by one — `checkpoint.parse()` never
raises (§2).

## 10. B1 / B2 / vision authority — unmodified, verified

- **B1**: no new code path calls `begin_submission_dispatch`, `SubmissionGuardV0`, or any
  final-submit control. `submission_effect_state` is read via the existing
  `get_submission_effect_state`/`SubmissionProbe` surface and is recorded in a checkpoint only
  as advisory, re-read live at reconciliation time, never trusted from the stored copy —
  `test_a_confirmed_submission_wins_even_over_a_stale_or_garbage_checkpoint` proves a
  checkpoint cannot contradict live submission evidence.
- **B2**: `account_state.read_state()` is called exactly where it already was; no new
  shortcut reads a cached account state in place of it. `reconcile()`'s `AUTH_REQUIRED` path
  is pure classification of a value the caller already obtained the normal way.
- **Vision/model**: untouched; nothing in this phase gives a model/vision judgment any role in
  a resume/recovery decision, consistent with P0-B1's existing observer-only design.

## 11. Saved-page / replay audit

`replay_guard.py` against the full saved corpus: **1239 saved pages (793 distinct) read the
same before and after.** Zero differences in how any existing page is read, classified, or
answered — expected, since every new code path is additive and guarded (`hasattr` checks on
the tracker; the new `sign_in_step` account-creation check only ever changes behavior when a
durable `pending_action` marker is actually present, which no saved page's replay creates).

## 12. Validation

Baseline established from a **clean checkout** of `codex/architecture-reliability` (all P0-B3
changes set aside via `git stash` first, specifically to rule out a collection-order race with
an in-progress background test run — the stash/pop/diff is reversible and nothing was
discarded):

```
4 failed, 2152 passed, 3 skipped in 3297.43s
```

All 4 failures are `tests/test_page_agent.py` tests using the `owner_submits()` helper
(`page.evaluate("document.querySelector('form').requestSubmit()")` +
`page.wait_for_url("**/done", timeout=5_000)`), reproduced twice, in isolation, against this
same clean checkout — i.e. **confirmed pre-existing, unrelated to any file this phase
touches** (the working tree at the time contained zero P0-B3 code):

- `test_a_whole_application_is_read_answered_and_submitted`
- `test_a_carried_over_no_to_sponsorship_is_corrected_from_the_profile`
- `test_a_submit_button_on_a_step_before_the_last_just_moves_on`
- `test_the_cover_letter_is_attached_where_the_form_asks_for_one`

After re-applying the P0-B3 implementation (`git stash pop`) and running the full suite again:

```
4 failed, 2208 passed, 3 skipped in 4206.29s (1:10:06)
```

The exact set of 4 failing node IDs is byte-for-byte identical to the baseline's (diffed
directly, `diff` reports no difference). **Zero new failures, zero new errors, same skip
count.** The `+56` passed delta (2152 → 2208) was reconciled exactly, not just assumed
consistent with "+51 new tests": a full bidirectional `--collect-only` diff between the
clean baseline and the post-implementation tree shows 56 newly-collected node IDs and zero
removed ones --- the 51 tests in the three new B3 files, plus 5 more from
`tests/test_source_has_no_control_characters.py`, a pre-existing repo-wide hygiene check that
parametrizes over every source file and picked up the 5 new `.py` files (`checkpoint.py`,
`recovery.py`, and the three new test files) automatically. All 56 pass.

Targeted regression suites (account/auth, submission-effect, tracker parity, loop-breaker
fingerprint) run separately beforehand, all green: **414 passed** across
`test_account_step.py`, `test_existing_account.py`,
`test_google_sign_in_and_value_readback.py`, `test_optional_account_requirements.py`,
`test_tracker_without_a_server.py`, `test_form_progress_fingerprint.py`,
`test_hidden_choices_and_breaker_reset.py`, `test_phase4_features.py`,
`test_submission_effect_state.py`, `test_login_guard.py`, `test_account_state.py`,
`test_emailed_code_rule.py`. New B3 suites, run standalone: **51 passed** (47 in
`test_checkpoint_state.py` + `test_recovery_reconciliation.py`, 4 in
`test_resume_after_interruption.py`).

## 13. Explicitly out of scope, and why

- **The `--auto` engine path** (`apply_flow`'s non-"reader" mode) does not get a
  `write_recovery_checkpoint`/reconciliation call site. The reader-agent path is the
  documented default and the one every other B1/B2 phase targeted; wiring a second engine
  mode was not required for this gap and would have doubled the surface to validate without a
  corresponding need identified in discovery.
- **Dashboard `_runs.json` / signal-file staleness** (the weak PID-liveness check, the lossy
  `company_title` slug key) — discovery found and flagged these as pre-existing, already
  partially addressed (`f043`, `f107`) residual risks, explicitly not touched: task §45 rules
  out "general observability/UI redesign," and this dashboard surface is a large, separate
  risk area from durable application-state checkpointing.
- **Unifying the five fragmented application-identity schemes** discovery found — the
  checkpoint anchors on the one P0-B1 already uses for exactly this class of risk (§3) rather
  than reconciling all five, which would be the submission-gateway/dashboard redesign task
  §45 rules out.
- **A second, independent action-lifecycle system for every consequential action** (wizard
  Next, repeated-entry Add, uploads) — discovery found these already safe via live-DOM
  re-checks; only account creation had no such backstop, so only it got one (§6). Building a
  generic lifecycle wrapper for actions that are already safe would be speculative
  infrastructure for a need discovery did not confirm.
- **Postgres-native checkpoint storage** — follows the pre-existing, already-documented
  submission-effect precedent exactly (§4) rather than building new Postgres schema for a
  gap P0-B1 itself already accepted.

## 14. Residual risks

- The dashboard's weak PID-liveness check and lossy signal-file key (discovery §5) remain as
  they were — a stale `_waiting_*.txt`/`_signal_*.txt` for the same company+title slug could
  still, in principle, be clickable when a new run starts, exactly as before this phase.
- `reconcile()`'s step-ordinal comparison only understands "N of M" phrasing; a wizard whose
  progress indicator is phrased some other way falls through to `OUTCOME_UNKNOWN` (fail
  closed, by design) rather than AHEAD/BEHIND — correct, but means AHEAD/BEHIND will fire less
  often than a human glancing at the same page might expect.
- `write_recovery_checkpoint`/`log_recovery_reconciliation` are wired only into the reader
  engine's `run_page_agent`/`main()` paths (§13) — a run using the `--auto` engine gets none of
  this phase's checkpoint writes or resume diagnostics.

## 15. Closure correction — the generic checkpoint write could erase the one guard this phase exists for

A follow-up review of the merged P0-B3 commit (`94c59bd`) found a confirmed, reproduced
production-path defect in the account-creation lifecycle (§6), plus two related fail-open
gaps in the same lifecycle. All three are fixed here, in place, without broadening
checkpointing or recovery beyond this one lifecycle. This section is appended, not a rewrite
of §§1–14, which stand as the history of what the original implementation did and why.

### 15.1 The defect: a generic checkpoint write silently erased the account-creation marker

`PageAgent._mark_account_creation_dispatched()` correctly read the existing checkpoint before
writing its own marker. `apply_flow.write_recovery_checkpoint()` — the generic, once-per-pass
progress checkpoint called after every `agent.run()` returns — did not: it called
`checkpoint.build(...)`, which defaults `pending_action=""`, and wrote that over whatever was
there. Reproduced directly: dispatch Create Account (marker written) → call
`write_recovery_checkpoint()` with ordinary, unrelated progress fields (a page URL, a handoff
reason) → the marker read back empty. A resumed process would then see no pending dispatch
and could press Create Account again — exactly the repetition §6 exists to prevent.
`tests/test_resume_after_interruption.py::test_the_generic_production_checkpoint_write_does_not_erase_an_unresolved_dispatch`
is the regression test, and it fails against the pre-correction code (confirmed before
fixing, per the project's reproduce-before-fix rule).

### 15.2 Fix: one merge-based checkpoint update, not hand-reconstruction at each call site

`checkpoint.merge_update(existing: Optional[dict], **changes) -> dict` (`checkpoint.py`) is
now the one way any caller updates a durable checkpoint. It starts from `existing` (parsed
defensively through `parse()`; an unparseable or `None` existing payload is treated as a
fresh application, never raised) and applies only the fields named in `changes` — anything
not named carries over unchanged. `STICKY_FIELDS = ("pending_action", "uncertain_actions")`
names which fields this matters most for, but the mechanism (`dataclasses.replace()` against
the parsed prior checkpoint) gives every field this property, not just those two: a generic
update can enrich `page_url`/`page_identity`/`verified_stage`/`handoff_reason`/anything else
without a caller having to remember to carry forward fields it knows nothing about. Clearing
a sticky field is only ever done by naming it explicitly:
`checkpoint.merge_update(existing, pending_action="")` — which is exactly what
`_resolve_pending_account_creation()` now does.

Both `apply_flow.write_recovery_checkpoint()` and `PageAgent._mark_account_creation_dispatched()`
/`._resolve_pending_account_creation()` now go through `merge_update()`. The account-creation
methods are simpler than before this correction, too: they no longer hand-copy
`page_identity`/`verified_stage`/`account_state`/`handoff_reason` from the existing payload
field by field — `merge_update()` does that for them.

### 15.3 Fix: a configured tracker that fails is fail-closed, never fail-open

Two related gaps in the same lifecycle, both now closed:

- **Read failure.** `_pending_account_creation()` previously returned plain `bool`, with
  `except Exception: return False` — indistinguishable from "nothing pending." It now returns
  `(pending: bool, storage_unavailable: bool)`. `storage_unavailable` is `True` only when a
  tracker is configured and *does* support `read_checkpoint`, but calling it raised. The
  caller (`sign_in_step`) checks this first and refuses to dispatch Create Account at all when
  `True`, with blocker text `"account creation could not be safely recorded; retry after
  checkpoint storage is available"`.
- **Write failure.** `_mark_account_creation_dispatched()` previously logged a warning on a
  write failure and returned `None` — the caller pressed on into `handle_auth_gate(...)`
  regardless. It now returns `bool`: `True` once the durable marker is confirmed written (or
  when no tracker is configured at all — see below), `False` when a configured tracker's read
  (needed to merge) or write actually failed. `sign_in_step` now checks this return value and
  refuses to dispatch when `False`, with the same blocker text.

**No durable tracker configured at all** (`self.tracker is None`, or it does not implement
`read_checkpoint`/`write_checkpoint`) remains the project's existing, intentionally supported
tracker-less/test-double path, and is explicitly *not* treated the same as a storage failure:
both methods return the "proceed" value (`(False, False)` / `True`) in that case, exactly as
before this correction.
`tests/test_resume_after_interruption.py::test_a_checkpoint_read_failure_blocks_account_creation_rather_than_assuming_none_pending`,
`::test_a_checkpoint_write_failure_blocks_account_creation_rather_than_proceeding_unguarded`,
and `::test_no_tracker_does_not_block_account_creation` cover the three-way distinction
directly — a tracker object that raises on `read_checkpoint`/`write_checkpoint` versus
`tracker=None`.

### 15.4 Fix: the marker only clears on positive, deterministic evidence

`_resolve_pending_account_creation()` was previously called whenever `state.kind !=
account_state.CREATE_FORM` — which includes `LOADING` ("the account step, still drawing
itself," `account_state.py`'s own description) and `NONE`/`EMAIL_FIRST`/`CHOOSER`, none of
which are evidence of anything in particular. The call site now gates on a specific allowlist:

```python
_ACCOUNT_CREATION_RESOLVED_KINDS = frozenset({
    account_state.SIGNED_IN, account_state.CODE_ENTRY, account_state.VERIFY_EMAIL,
    account_state.ACCOUNT_EXISTS, account_state.MFA_REQUIRED, account_state.LOCKED,
    account_state.WRONG_PASSWORD, account_state.SIGN_IN_FORM,
})
```

Each of these is a specific, deterministic `account_state.py` result distinct from
`CREATE_FORM` itself — not a fallback/generic bucket. `LOADING`, `NONE`, `EMAIL_FIRST`, and
`CHOOSER` are deliberately excluded: `LOADING` is explicitly transient, and the other three
can describe a page mid-navigation just as easily as a genuine resolution. This is
intentionally conservative — a site that redirects straight from Create Account to the plain
application form with no step indicator and no other account markers (`NONE`) will leave the
marker set — but per the task's own instruction ("If evidence is ambiguous: keep the pending
marker"), the failure mode is an occasional unnecessary owner hand-off on a later, unrelated
create-form encounter for the same application, never a repeated account-creation attempt.
`tests/test_resume_after_interruption.py::test_a_loading_or_ambiguous_state_does_not_clear_the_marker`
and `::test_the_marker_clears_only_on_positive_evidence_the_create_form_is_behind_us` cover
both directions.

### 15.5 What stayed the same

- `recovery.reconcile()` is untouched — still advisory, still live-evidence-first, no
  broadened authority.
- P0-B1's submission-authority chain (`apply_flow.submit_verified()` →
  `click_verified_submit()` → `SubmissionGuardV0.submit_verified()` →
  `begin_submission_dispatch()`) and its `AUTHORIZED`/`DISPATCHED`/`CONFIRMED`/`UNCERTAIN`
  states are untouched.
- P0-B2's `account_state.read_state()` remains the sole authority on live account/auth state;
  nothing here lets a checkpoint manufacture authenticated/verified/MFA-cleared/created
  without live evidence — if anything, §15.4 makes the checkpoint *more* conservative about
  trusting anything other than a specific, named account-state result.
- No new checkpointing concept, write boundary, or reconciliation outcome was added; this is
  a correction to the one lifecycle §6 already introduced, not an extension of scope.

### 15.6 Validation

New/updated tests: `tests/test_resume_after_interruption.py` grew from 4 to 8 tests (the
production-checkpoint-boundary regression, the generic-update-preserves-lifecycle test, the
positive-evidence resolution test rewritten to use a genuinely resolving page, the
loading/ambiguous non-clearing test, the read-failure and write-failure fail-closed tests, and
the no-tracker-preserved test, alongside the pre-existing resolved-then-recreate test).
`tests/test_checkpoint_state.py` gained 5 direct tests of `checkpoint.merge_update()`. All
P0-B3 suites together: **60 passed** (up from 51).

Targeted regression suites (the same twelve files as the original P0-B3 validation —
account/auth, submission-effect, tracker parity, loop-breaker fingerprint): **414 passed**,
identical to before this correction.

Full suite, run against the corrected code (via `pytest -q -rs -n auto`, matching
`.github/workflows/ci.yml`'s own invocation):

```
4 failed, 2217 passed, 3 skipped in 850.06s (0:14:10)
```

The exact 4 failing test names are identical to the clean baseline's
(`test_a_whole_application_is_read_answered_and_submitted`,
`test_a_carried_over_no_to_sponsorship_is_corrected_from_the_profile`,
`test_a_submit_button_on_a_step_before_the_last_just_moves_on`,
`test_the_cover_letter_is_attached_where_the_form_asks_for_one` — all in
`tests/test_page_agent.py`'s `owner_submits()`-based tests, confirmed in §12 as pre-existing
and unrelated to any file this phase or its correction touches). **Zero new failures, zero
new errors, same skip count.** The passed-count delta (2208 → 2217) reconciles exactly to the
9 new tests this correction added (60 B3 tests total, up from 51).

Replay guard: **1243 saved real pages, zero differences** — identical to §11, as expected: no
saved real page exercises the durable account-creation lifecycle or the generic checkpoint
writer's merge semantics, so this corpus could not have caught the defect in the first place
(it is a cross-process/storage-failure scenario, not a page-reading one) — the new
production-path tests in §15.6 are what actually cover it.
