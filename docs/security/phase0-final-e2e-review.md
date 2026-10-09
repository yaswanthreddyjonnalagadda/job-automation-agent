# Phase 0 final: cross-phase invariant matrix

Branch: `integrate/phase0-final`, created from `codex/architecture-reliability` @
`18c2856` (the tip after PR #9 / P0-B5 merged). This reviews how the already-merged
P0-B1 through P0-B5 interact as one system -- not whether each phase still has its
own unit tests (it does, extensively). Findings are backed by production
call-graph citations, not speculation.

## Saved-page corpus, recorded before any further test runs

| group | count |
| --- | --- |
| `output/*/pages/page_*.txt` (flat) | 0 |
| `output/*/pages/*/page_*.txt` (run-scoped) | 162 |
| `output/*/account/*.txt` | 64 |
| `runs/*/axtree_dump.json` | 220 |
| **total** | **446** |

Full path/sha256/size/mtime manifest written outside the repo (per task instruction)
to the session scratchpad, not committed. Zero flat recordings remain -- consistent
with the P0-B5 closure finding that 51 of `output/*/pages/` were already emptied by
the (now-fixed) retention bug before this task began; see `683e5cb`.

## Phase-0 baseline (merged B1-B5 code, before any changes on this branch)

One clean run, `pytest -q -rs -n auto`: **4 failed, 2356 passed, 11 skipped, 0
errors**, 1768.12s. The four failures are the same known, pre-existing Playwright
`owner_submits()` navigation-timeout fixtures carried through every prior phase of
this engagement, unchanged:

- `tests/test_page_agent.py::test_a_whole_application_is_read_answered_and_submitted`
- `tests/test_page_agent.py::test_a_carried_over_no_to_sponsorship_is_corrected_from_the_profile`
- `tests/test_page_agent.py::test_a_submit_button_on_a_step_before_the_last_just_moves_on`
- `tests/test_page_agent.py::test_the_cover_letter_is_attached_where_the_form_asks_for_one`

The eleven skips: 2 unavailable-DB cases, 1 opt-in desktop case, 3 Windows
file-symlink-privilege cases, and the 5 missing historical recording fixtures named
in the P0-B5 closure section above (OCC page_53/page_02/page_08, WinChoice page_09,
Rubrik page_01) -- unchanged from the P0-B5 PR's own final count.

Corpus check after this run: 162 pages / 64 account / 226 axtree = 452 total (up
from 446; some tests legitimately write new `runs/*/axtree_dump.json` diagnostic
captures). No decrease -- the corpus remains protected.

## Invariant matrix

### A. B1 x B2 -- can account/auth actions trigger or bypass submission authority?

**PASS.** The only call chain reaching `begin_submission_dispatch` is
`apply_flow.submit_verified` (apply_flow.py:1227) -> `assistant.click_verified_submit`
(browser_automation.py:6988) -> `_click_verified_submit_locked` (7000) ->
`SubmissionGuardV0.submit_verified` (submission_guard.py:410-509). No account/auth
module (`account_state.py`, `login_guard.py`, `sites/workday.py`) references any of
`click_verified_submit|SubmissionGuardV0|submit_verified|begin_submission_dispatch|
find_submit_button`. `find_submit_button`'s label filter (`safety.SUBMIT_LABEL_RE`,
safety.py:143) does not match "Continue"/"Verify"/"Create Account". Where a
form-associated `type=submit` account button *would* collide with the browser-side
guard's structural `finalControl` heuristic, `complete_emailed_passcode`
(browser_automation.py:6633) explicitly detects `SubmissionGuardV0.click_was_denied`
and refuses to click rather than treating denial as success (6660-6663) -- fails
closed, never open.

NEEDS TEST: no existing test exercises a `<button type=submit>` labeled "Verify" or
"Create Account" inside a real `<form>` against the firewall guard, to lock in that
this fails closed rather than silently.

### B. B1 x B3 -- can recovery retry an already-dispatched submission?

**PASS**, via a mechanism worth stating precisely: `recovery.reconcile()`
(recovery.py:89) is a pure classification function with **no actuation power**. Its
one production call site, `log_recovery_reconciliation` (apply_flow.py:679-719), is
explicitly diagnostic-only -- it logs `result.outcome` and records a tracker note,
and nothing branches on it ("Logs (never acts on)... the existing
`shows_the_application` fallback... is what actually governs navigation, unchanged
by this function"). The task's framing describes `recovery.reconcile()` as the
mechanism that prevents repeating consequential actions on resume; in the current
code, for *submission* specifically, that guarantee instead comes from three
independent, durable layers that do not depend on reconcile() at all:

1. `apply_flow.submit_verified` calls `refuse_submission_replay` (1285-1309) before
   any click, checking durable submission-effect state across identity aliases.
2. `SubmissionGuardV0.submit_verified` independently checks `application.status`.
3. `JobTracker.begin_submission_dispatch` (job_tracker.py:527-589) is transactional
   (`BEGIN IMMEDIATE`) ground truth: an already-DISPATCHED/CONFIRMED/UNCERTAIN state
   raises `RuntimeError`, which the guard turns into a blocked/UNCERTAIN result, never
   a second click.

A resumed run has no fast-path to final submit -- it re-enters the same
`run_page_agent` loop as a fresh run, so any eventual submit attempt re-triggers all
three layers. This is a legitimate instance of this project's "one decision, one
place" rule (B1 owns submission replay protection outright; B3's reconcile is
observational), not a gap -- but it means B3 is not actually "in the loop" for this
particular invariant, which is worth being explicit about rather than assumed.

NEEDS TEST: `recovery.reconcile()` has no case for
`live_submission_effect_state in {"DISPATCHED", "UNCERTAIN"}` (only `CONFIRMED` and
`None` are covered). More importantly, the three-layer replay protection above is
only unit-tested at the tracker/guard level (`test_submission_effect_state.py`), not
through the actual resume path in `apply_flow.py` (simulate a crash immediately after
`begin_submission_dispatch`, then call `apply_flow.submit_verified` again as a fresh
resumed run would, and assert it cannot issue a second click).

### C. B1 x B4 -- can a verified ordinary action authorize submission?

**PASS**, strong structural guarantee. `action_result.py` explicitly disclaims
authority in its own docstring ("it does not decide whether to submit... It has no
authority over any of those"). More tellingly: `action_result`'s dataclass is
imported **only by its own test file** -- no production module
(`browser_automation.py`, `page_agent.py`, `apply_flow.py`) imports it yet, so there
is no live call site today where a B4 `VERIFIED` outcome could reach submission
logic at all. The gateway's authorization object is `safety.AutoSubmitDecision`,
built fresh from live field comparisons -- never a cached ActionResult -- and
`SubmissionGuardV0._valid_authorization` (submission_guard.py:375) enforces this with
a strict `type(decision) is not safety.AutoSubmitDecision` check (not `isinstance`),
rejecting a bool, dict, or any duck-typed stand-in outright
(`test_plain_boolean_cannot_authorize_gateway_submission` already proves this for
`True`).

NEEDS TEST: no test passes a live `ActionResult` instance itself (as opposed to
`True`) into `_valid_authorization`/`submit_verified` to prove the type check
rejects that specific object, given the module exists precisely to represent
"verified" outcomes that must never be conflated with submission authorization.

### D. B1 x B5 -- can diagnostic capture affect submission/guard behavior?

**PASS.** Every diagnostic-capture call site that runs around a submission decision
fires strictly *after* the decision is already made, wrapped in `try`/`except` that
can only reduce evidence, never change the decision:

- `apply_flow.py:1076,1276,1929` -- `diagnostics.capture_safe_screenshot(... "submitted_confirmation.png")`
  runs only after `outcome.kind == "submitted"` / `status == STATUS_SUBMITTED` /
  `decision == "submitted_by_user"` has already been decided; failure is swallowed
  (`except Exception: pass` or a logged warning), never raised back into the decision.
- `safety.py:572-583` (`check_visa_sponsorship_shield`) -- the abort decision
  (`True, clause, ...`) is already fixed by `no_sponsorship_statement(body_text)`
  before the forensic screenshot is even attempted; the screenshot call is wrapped
  separately and can only leave `screenshot_path = None` on failure.

No diagnostic capture call sits upstream of, or interleaved into, a decision's own
logic; `page.content()` is used in `safety.py:564` only as a live-perception
fallback for reading body text (classified NON-DIAGNOSTIC in the B5 source audit),
not as diagnostic capture.

### E. B2 x B3 -- can restart repeat account creation/verification without reconciling live state?

**PASS** for account creation, with existing regression coverage; **NEEDS TEST** for
email-verification-code/link re-consumption on restart.

`recovery.reconcile()` takes `live_account_state_kind` as a caller-supplied,
freshly-read parameter and never reads it from the stored checkpoint;
`ApplicationCheckpoint.account_state` exists as a field but is never populated at
any production call site (dead field, no authority). As cell B already established,
`reconcile()`'s one caller is diagnostic-only.

The actual guarantee for the one genuinely non-idempotent account action (account
creation) is independent of reconcile(): `PageAgent.sign_in_step` always re-derives
state from a fresh `account_state.read_state()` (page_agent.py:1649) on every call,
including after restart. The durable `pending_action="account_creation_dispatched@host"`
marker (page_agent.py:1552-1608) blocks re-pressing Create Account and hands off to
the owner, clearing only on positive live evidence
(`_resolve_pending_account_creation`, 1610-1632). `tests/test_resume_after_interruption.py`
proves this end-to-end with independent `PageAgent` instances simulating restart
(generic-write-doesn't-erase-marker; live SIGNED_IN clears it then legitimate
re-create is allowed; ambiguous/loading state does NOT clear it; storage-failure
fails closed).

Gap: email-verification-code/link consumption has no analogous durable marker --
safety there relies entirely on the same fresh-live-read principle (once verified,
the live page no longer shows CODE_ENTRY/VERIFY_EMAIL, so the code/link-reading
functions simply aren't invoked again) plus `login_guard.may_read_code`'s
per-account read limit. No restart test analogous to
`test_resume_after_interruption.py` exists for this specific path.

NEEDS TEST: a `test_resume_after_interruption`-style test with two independent
`PageAgent`/tracker instances where the live page already shows verified/signed-in,
asserting the code/link-reading function is never called on the second instance.

### F. B2 x B4 -- can a failed auth-field write be counted as a verified application write?

**PASS**, structurally separate code paths. `action_result.py`'s formal
`ActionResult`/`VERIFIED` model is imported nowhere in production code -- only its
own test file references it. The real B4 mechanism,
`page_agent._fill_value_committed()` (page_agent.py:971-984), is used exclusively
inside `PageAgent.do()`'s application-field branches. Auth-field writes never touch
this function at all: password entry uses a raw `.fill(password)`
(browser_automation.py:5292) judged afterward by a completely separate
login-rejection-detection mechanism; OTP/code entry uses `fill_and_dispatch`/
`keyboard.type` directly (page_agent.py:2236-2241), bypassing `do()` and
`_fill_value_committed` entirely. Password fields are also structurally excluded
from the application-answer pipeline: `field_requirements.account_password_fields()`
returns a distinct `PasswordField` type never converted into a `Control`/`Answer`
fed to `do()`.

NEEDS TEST: no regression test currently asserts this isolation explicitly
(`test_action_verification.py` has zero password/OTP cases).

### G. B2 x B5 -- can OTP/password/link values escape through logs/events/diagnostics?

**PASS**, with existing regression-test evidence. `account_state.py` makes zero
logging calls at all. `login_guard.py`'s messages interpolate only
host/email/counts/dates; its durable store persists only `host|email` keys plus
timestamps/hold reasons, never a code or password. The Gmail readers are explicit
about this by comment and code: `passcode_from_gmail` logs only "found a one-time
passcode in Gmail" (browser_automation.py:6509, "never the mail's text: it holds the
code"); `verification_link_from_gmail` logs only the link's netloc (6567-6568, "The
link is never written to the log: it is the account's key"); the verification
function logs only generic verified/failed status.

`diagnostics.install_log_privacy()`'s `_static_log_templates()` collects only
`ast.Constant` first-args at each call site; a dynamic/f-string message is never a
constant node, so it falls through to the placeholder-replacement branch rather than
being kept -- this is exactly the mechanism that catches "secret baked into the
message at the call site," and is proven directly by
`tests/test_diagnostic_sanitization.py` logging a fully dynamic sentinel message and
asserting nothing leaks. `record_event()` call sites across the account/auth flow
pass only static notes/counts.

### H. B3 x B4 -- can a checkpoint claim success B4 did not verify?

**PASS.** The checkpoint's `uncertain_actions` field is populated *only* from
actions B4's own post-fill verification could not confirm
(`page_agent.py:2011-2017`, `uncertain_action_labels()` -- "secret-safe labels for
consequential actions this pass attempted but could not positively verify").
`write_recovery_checkpoint`'s own docstring (apply_flow.py:611-638) states the
broader policy directly: "nothing recorded here is ever trusted back without fresh,
independent verification on the next read -- `recovery.reconcile()`,
`account_state.read_state()` (P0-B2), and `SubmissionGuardV0`/`SubmissionProbe` (P0-B1)
remain the sole authorities." The checkpoint is explicitly advisory, not a claim of
verified success for anything beyond what it was given.

### I. B3 x B5 -- can diagnostic retention delete checkpoint/recovery material?

**PASS**, confirmed by an existing regression test. `cleanup_expired_diagnostics`
restricts its traversal stack to exactly `base_dir/output`, `base_dir/runs`,
`base_dir/logs` and (per the P0-B5 closure fix) never enters `pages/`. It contains
no reference anywhere to `data/`, `applications.db`, `checkpoint`, or
`ApplicationCheckpoint`. The checkpoint/recovery store lives in SQLite at
`data/applications.db` (config.py:26,34), outside all three scanned roots.
`tests/test_diagnostic_retention.py::test_cleanup_removes_only_old_diagnostics`
already explicitly places `tmp_path/'data'/'applications.db'` and
`job/'checkpoint.json'` in its protected list, ages them past the retention cutoff,
runs cleanup, and asserts both survive unmodified.

### J. B4 x B5 -- can event sanitization destroy recovery/handoff metadata?

**PASS**, structurally. `checkpoint.py` and `handoff.py` import neither `diagnostics`
nor anything from its sanitization layer. `db.py:251-261`
(`write_checkpoint`/`read_checkpoint`) routes through a dedicated local-SQLite
checkpoint store, entirely separate from `db.py:263-271` (`events()`), which is the
only place `diagnostics.sanitize_event()` is applied, against a different table
(`application_events`). Concretely, in `apply_flow.py:1057-1071`: the real `Handoff`
object (`owner_handoff`, with its precise category/required-action/resume-condition
fields) is passed directly into `write_recovery_checkpoint()` as a live Python
object and persisted via `checkpoint.merge_update()`; a *separate*, best-effort,
failure-tolerant `tracker.record_event("handoff_started", ...)` call happens next to
it purely for dashboard observability (wrapped in its own try/except, apply_flow.py:1061-1068).
Even if that event call fails or its payload is sanitized away, the authoritative
checkpoint write already has the real object -- recovery data never round-trips
through the sanitized event store.

### K. B1-B5 together -- interrupted run resumes and submits safely?

Addressed primarily through the cross-phase E2E tests below (section: E2E tests),
not pure static analysis, since this is precisely the property a single code
reading cannot fully establish. Preliminary conclusion from A-J: the project's
"one decision, one place" pattern means each consequential-action class
(submission replay, account-creation-pending, verified-write bookkeeping) has its
*own* independent, durable guard rather than funneling through one shared
orchestrator -- which is robust against any *one* phase's authority being silently
reused by another, but places the burden of proof on the E2E tests to show the
independent guards actually compose correctly across a real interruption.

## Matrix summary

| Cell | Verdict | Needs new test? |
| --- | --- | --- |
| A (B1xB2) | PASS | Yes -- submit-typed "Verify"/"Create Account" button vs. firewall |
| B (B1xB3) | PASS | Yes -- reconcile() DISPATCHED/UNCERTAIN case + full resume-path replay test |
| C (B1xB4) | PASS | Yes -- a live `ActionResult` instance rejected by `_valid_authorization` |
| D (B1xB5) | PASS | No -- existing evidence sufficient |
| E (B2xB3) | PASS | Yes -- verification-code/link non-reconsumption on restart |
| F (B2xB4) | PASS | Yes -- explicit auth-field/application-field isolation test |
| G (B2xB5) | PASS | No -- existing evidence sufficient |
| H (B3xB4) | PASS | No -- existing evidence sufficient |
| I (B3xB5) | PASS | No -- existing evidence sufficient |
| J (B4xB5) | PASS | No -- existing evidence sufficient |
| K (B1-B5) | Addressed via E2E tests below | -- |

No DEFECT was found anywhere in the matrix. Six concrete NEEDS TEST gaps were
identified with exact file:line evidence; a new regression test for each was added
(listed in the summary table above), plus one new cross-phase E2E test
(`tests/test_phase0_final_e2e.py`) chaining B4's verified field write through the
real production bridge (`apply_flow.hand_over` -> `apply_flow.submit_verified` ->
the real `JobApplicationAssistant.click_verified_submit` -> `SubmissionGuardV0`) to
a confirmed submission, then a B5 privacy-sentinel check on a diagnostic captured
mid-run, then a resumed/duplicate attempt refused through that same real bridge
(K). No production code was changed -- every finding was PASS; these tests lock in
already-correct behavior per the fix policy's "no speculative production changes."

## B1-B5 targeted regression (new tests included)

`pytest -q -rs -n auto` across every B1-B5 test file plus the new cross-phase E2E
test: **559 passed, 3 skipped** (the same three Windows file-symlink-privilege
skips seen throughout this engagement, already covered on Linux), 298.84s. Zero
failures.
