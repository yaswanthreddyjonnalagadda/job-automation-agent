# Phase 0-B4 — Verified Actions, Precise Human Handoff & Observability

Base: `codex/architecture-reliability` at `34bfb9a` (P0-B1 + P0-B2 + P0-B3 merged).
Branch: `fix/action-verification-handoff-observability-hardening`.

See `docs/security/phase0-b4-discovery.md` for the full pre-implementation audit. This
document covers what was built, why, and what was deliberately left alone.

## 1. Discovery summary

Three parallel audits mapped the gap between "attempted" and "verified" across the codebase.
Full detail in `phase0-b4-discovery.md`; the headline findings:

- **Already verified, reused as-is**: Ant Design/`rc-select` dropdown resolution
  (`interaction.resolve_ant_dropdown()`), explicit checkbox check/uncheck, Ashby-style toggle
  buttons, date/spinbutton fill, Workday's tag/searchable-input writers, and
  `PageAgent.not_stuck()`'s page-cycle-later re-check.
- **Confirmed unverified, now fixed**: the generic textbox `fill` action (highest-traffic
  path in the file), the radio/checkbox "choose" dispatch (the *normal* way a multiple-choice
  question is answered — the explicit check/uncheck sibling four lines away already verified
  the same control type), native `<select>`, resume upload, and the bulk repeated-entry site
  filler.
- **Navigation**: `_press_next_locked`'s only post-press check was a blunt ref-stripped
  whole-page text-equality diff; validation-error detection ran only once that diff had
  already decided "retry," never on the "moved" path.
- **Handoff**: of ~20 surveyed human-facing stop messages, only two embedded any
  site-identifying token (a raw network host, never the employer's name); `application_
  checkpoints.handoff_reason` was confirmed write-only, never rendered anywhere.
- **P0-B1/P0-B2/P0-B3 authority**: confirmed current and unchanged; this phase reads their
  state and does not duplicate or bypass any of it.

## 2. Action-result model (`action_result.py`)

`ActionResult(action, target, attempted, outcome, evidence_kind, evidence_summary, reason)`,
outcomes `NOT_ATTEMPTED / VERIFIED / REFUSED / VALIDATION_FAILED / NO_CHANGE /
OUTCOME_UNKNOWN / OWNER_REQUIRED`. A pure reporting structure — it decides nothing and holds
no authority; `__post_init__` rejects an unknown outcome string outright. Used directly in
tests as a small, typed vocabulary; production code mostly expresses the same distinctions as
plain booleans/strings at the specific call sites below (see §9 for why it wasn't threaded
through every one).

## 3. Verified actions

| Control | Where | Evidence | File:line |
|---|---|---|---|
| Plain textbox/textarea `fill` | `PageAgent.do()` | `input_value()` read back, fuzzy-compared to the (possibly date-converted) intended value via `_fill_value_committed()` (the same fuzzy match `not_stuck()`'s independent, later re-check already uses — one shared definition, not two that could disagree); one deterministic native-setter retry, then honest failure | `page_agent.py` `do()`, `fill` branch |
| Radio/checkbox via "choose" group-match (the *normal* multi-choice dispatch) | `PageAgent.do()` | `_is_checked()` readback, matching the sibling explicit check/uncheck action's own verification | `do()`, the group-match branch; also the text-match radio fallback |
| Native `<select>` | `PageAgent._choose_exact()` | `el.selectedOptions[0].text` read back, same fuzzy match | `_choose_exact()`, native-select branch |
| Resume upload (all three upload code paths in `do()`) | `PageAgent._confirm_resume_attached()` | Reuses the existing, previously pre-check-only, filename-aware `_resume_on_page()` | new method, wired into all 3 `do()` upload call sites |
| Repeated-entry bulk fill (Workday only, the only adapter with `entry_count()`) | `PageAgent.add_entries_the_site_way()` | Re-reads `adapter.entry_count()` after the filler runs; a mismatch is logged and recorded (§6), the existing once-per-run retry bound is left exactly as it was | same method, post-fill check added |
| Navigation press | `PageAgent._press_next_locked()` | `page_errors()` now also checked when the ref-stripped text *did* change, not only when it didn't | same method, inserted before the final `"moved"` return |

Each fix is proven in both directions in `tests/test_action_verification.py` /
`tests/test_navigation_verification.py`: a genuine, committed write still returns success, and
a rejected/uncommitted one (a controlled input that snaps back, a radio whose own handler
unchecks itself, a managed `<select>` that resets its selection, an upload whose filename
never appears, an entry-count mismatch, a validation banner that survives a partial page
change) is now reported honestly rather than recorded as answered.

## 4. What counts as positive evidence (and what doesn't)

Per control type, the cheapest already-available signal, reusing existing helpers rather than
adding new ones or an AI call: `input_value()`/`is_checked()`/the selected option's text for
ordinary fields (§3); the existing `page_errors()` classifier (already used by
`stuck_on_errors()`) for validation refusal; `entry_count()` for repeated entries; filename
presence for uploads. No full-DOM re-snapshot was added anywhere beyond what the existing
before/after press check already does once per navigation attempt (not per keystroke).

## 5. Navigation verification

`_press_next_locked()`'s decision shape is unchanged (`"moved" | "submitted" | "retry" |
"stop"` — no new return-value taxonomy was introduced, so every existing caller's contract is
untouched). What changed: `page_errors()` is now consulted on *both* branches of the
before/after text comparison, not only the one where nothing changed. A press that visibly
advances the page while a validation error remains (or reappears) is now `"retry"` with the
error text, not a false `"moved"`. `tests/test_navigation_verification.py` proves this
directly, plus a guard-rail test confirming ordinary new content with no error-shaped text
still reads as `"moved"`.

Step-ordinal AHEAD/BEHIND reasoning (`recovery.py`, P0-B3) remains scoped to resume-time
reconciliation only, as it already was — this phase did not wire it into the per-press loop;
the existing `page_errors()`-based check above is the cheaper, already-proven mechanism for
the in-run case, consistent with task section 5's "use the cheapest reliable verification
already available."

## 6. Upload verification

`PageAgent._confirm_resume_attached(page, path)` is the one new upload-verification method:
it checks `_resume_on_page(page)` (pre-existing, filename-aware) after the settle that already
followed every upload attempt, and only then calls `_resume_went_on()` (which sets
`resume_uploaded = True` and records the existing `"resume_attached"` event). Unverified ->
`False`, a new `"action_outcome_unknown"` event, and the caller treats it exactly like any
other answer that could not be committed — no second upload method, no retry loop.
Cover-letter upload verification was not added (no existing filename-aware check for it to
reuse, unlike the resume's `_resume_on_page()`/`resume_seen`/`_file_already_attached()` — see
§11 residual risks); `_attach_resume_to_its_input()` (a different, narrower, already
pre-condition-checked resume-input path) was also left unchanged — see §11.

## 7. Repeated-entry verification

`add_entries_the_site_way()` now re-reads `adapter.entry_count()` immediately after the site
filler runs (only Workday implements this hook today — confirmed in discovery, unchanged).
The existing once-per-run bound on retrying a section (`done`/`redone`, set *before* the
filler runs so a throwing filler is never retried this run either) is deliberately left
exactly as it was — a mismatch is made *visible* (logged, recorded as an
`"action_outcome_unknown"` event, and added to `PageAgent.uncertain_action_labels()`), not
silently invisible for the rest of the run, without changing the retry policy itself (task
section 37: reuse existing bounds, don't add new unbounded loops).

## 8. One precise handoff model (`handoff.py`)

`Handoff(application_key, employer, portal, stage, category, reason, required_action,
work_completed, resume_condition)`. Fourteen categories (`CAPTCHA`, `SMS_MFA`,
`AUTHENTICATOR_MFA`, `SECURITY_KEY`, `PUSH_APPROVAL`, `ACCOUNT_LOCKED`,
`ACCOUNT_CREATION_UNCERTAIN`, `FIELD_REQUIRED`, `UNSUPPORTED_CONTROL`, `VALIDATION_BLOCKER`,
`ACTION_OUTCOME_UNKNOWN`, `APPLICATION_RECOVERY`, `OWNER_REVIEW`, `OTHER`).
`handoff.classify()` is a best-effort, pattern-based label chosen from `Outcome.kind` and the
reason text the codebase *already produces* — reusing the same wording
`emailed_codes.NON_EMAIL_CHANNEL_CORE` already uses to tell an authenticator app, a security
key, and a push approval apart (P0-B2's own vocabulary, not a new detection mechanism). A
wrong classification only affects a display label; `Handoff` has no authority over anything
(§9 reiterates why).

`compose_message()` answers task section 23's questions in one string: which employer, which
portal, the reason, and (when given) the required action and what's already been done —
closing the confirmed gap that almost no existing message named the employer at all.

## 9. Handoff integration point — one place, not twenty

Rather than rewrite each of the ~20 individual message-construction sites in `page_agent.py`
(high risk across a huge, heavily-tested file, for messages that are often already complete
sentences), the `Handoff` is built at the *one* place `apply_flow.run_page_agent()` already
converts an `Outcome` into durable notes/checkpoint (the exact point discovery found already
pre-existing, pre-B4) — `write_recovery_checkpoint`'s call site. `employer`/`portal` (the live
page's host) are added there; the existing message text becomes `Handoff.reason` unchanged.
This is deliberately a labeling/composition layer over text P0-B1/B2/B3 and `page_agent.py`'s
existing logic already produce — it is not a second policy authority: it never decides
whether to submit, what account-state action to take, or how to reconcile a checkpoint; those
remain exactly as they were.

## 10. Handoff persistence / resume condition

Three new, additive fields on P0-B3's existing `ApplicationCheckpoint` (no schema version
bump — purely additive, safe-default fields, exactly like every other field already on it):
`handoff_category`, `handoff_required_action`, `handoff_resume_condition`. The pre-existing
`employer`/`handoff_reason` fields are reused, not duplicated. `write_recovery_checkpoint`
writes these through the same `checkpoint.merge_update()` P0-B3's closure correction already
established — a generic progress pass never silently blanks them. `resume_condition_for()`
gives each category a concrete, checkable condition (e.g. SMS_MFA: "the account step no
longer asks for an SMS/text code"); per task section 25, nothing here forces an owner
Continue where the runtime can already safely detect the blocker is gone on its own (that
detection is P0-B2's `account_state.read_state()`/P0-B1's CAPTCHA check, unchanged — the
`resume_condition` text describes what those mechanisms already watch for, it does not
replace them).

`uncertain_actions` (populated for the first time in this phase — P0-B3 left it schema-only)
is unioned onto whatever the checkpoint already had, never replacing it: this field has no
explicit resolve step yet (unlike `pending_action`'s dispatch/resolve lifecycle), so nothing
in this phase ever clears an entry once added — see §11.

## 11. Dashboard

Minimal, additive change only (task section 32): the existing application detail page's
progress card gains one small "What to do (Category)" block, reading the checkpoint via the
new `JobTracker.read_checkpoint_by_dedup_key()` lookup (the dashboard only ever has the
application's `dedup_key`, never the P0-B1 submission-effect identity a checkpoint is actually
filed under — this is a plain, non-canonicalizing lookup by the `dedup_key` column
`write_checkpoint` already stores and indexes, not an alias-graph resolution). No new page, no
redesign; the block renders nothing when there's no checkpoint or no `handoff_category`.

## 12. Structured events

Reuses `tracker.record_event` exclusively — no new event table. New kinds, all with small,
fixed-shape, secret-safe payloads (`action`/`target_kind`/`evidence_kind`/`category`/
`outcome_kind`/`error_count` — never a form value, never a question's full text beyond what
already existed in a log line):

- `"action_outcome_unknown"` — an unverified resume upload; an entry-count mismatch after a
  repeated-entry fill.
- `"action_validation_failed"` — a navigation press that still shows a validation error
  (payload carries only a count, not the error text itself — the text still reaches the
  owner through the existing `"retry"` feedback/notes channel unchanged).
- `"handoff_started"` — fired once per non-"submitted" pass, at the same integration point as
  §9, payload `{category, outcome_kind, portal}`.

Per task section 30 ("do not flood events"), ordinary field writes get **no** event — only
uploads, repeated-entry mutations, navigation validation failures, and handoffs, matching the
task's own stated priority order.

## 13. Secret-safety

No new payload includes a form value, a full question label, a password, a code, or a
filename beyond what the pre-existing `"resume_attached"` event already logs (the resume
filename — a pre-existing, low-severity, unchanged exposure, not something this phase
introduces or expands). `handoff.compose_message()`'s text is built from `Outcome.reasons`
strings the codebase already produces and already sent to `tracker.notes`/logs; nothing new is
exposed there either — the employer name and portal host are the only additions, and both are
already shown elsewhere on the same dashboard page.

## 14. Preserved authority (unmodified, verified)

- **P0-B1**: `apply_flow.submit_verified()` → `click_verified_submit()` →
  `SubmissionGuardV0.submit_verified()` → `begin_submission_dispatch()` and the
  `AUTHORIZED`/`DISPATCHED`/`CONFIRMED`/`UNCERTAIN` states — untouched. No new code path
  constructs, authorizes, or clicks a final-submit control.
- **P0-B2**: `account_state.read_state()`/`login_guard.py` — untouched; `handoff.classify()`
  only reads text these already produced, never re-derives or overrides account/auth state.
- **P0-B3**: `recovery.reconcile()` — untouched, still advisory/live-evidence-first;
  `checkpoint.merge_update()`'s sticky-field semantics — untouched and reused, not duplicated;
  the account-creation fail-closed lifecycle — untouched.
- **Vision/model**: no new authority anywhere; `handoff.classify()` and the field-write/
  navigation verification above are all plain, deterministic text/DOM checks.

## 15. Adversarial test matrix — results

All cells below are covered by `tests/test_action_verification.py`,
`tests/test_navigation_verification.py`, or `tests/test_handoff_observability.py`, each
proven in both the success and failure direction:

| Case | Result |
|---|---|
| Text input commits / controlled input rejects | PASS both |
| Radio/checkbox choice commits / click doesn't actually check it | PASS both |
| Native select commits / managed select snaps back | PASS both |
| Upload shows attached / upload never shows attached | PASS both |
| Repeated-entry count matches / count mismatches after fill | PASS both |
| Press advances with no error / press changes text but shows an error / press changes nothing / press changes text with no error-shaped text at all | PASS all four |
| Every handoff category classifies correctly from existing text; unknown text defaults to OTHER/OWNER_REVIEW, never crashes | PASS |
| Checkpoint handoff fields round-trip; an old payload with none of the three new keys still parses COMPATIBLE | PASS |
| `read_checkpoint_by_dedup_key` finds what was written, returns None when nothing was, finds the latest on repeated writes | PASS |
| `write_recovery_checkpoint` with `owner_handoff` populates the new fields; `new_uncertain_actions` unions rather than replaces; no new uncertain actions this pass preserves existing ones | PASS |
| Dashboard renders the handoff block when a checkpoint has one; renders nothing extra when it doesn't | PASS |

## 16. Saved-page audit

Not newly run as a separate corpus pass for this phase: every fix here is either (a) a
control-level verification added to `do()`/`_choose_exact()` (exercised by the full
`test_page_agent.py` regression suite, including its real saved-recording-based tests, §
Validation below) or (b) the navigation validation-timing fix (covered by targeted synthetic
Playwright tests, since the specific before/after-with-error scenario is not something a
single static saved snapshot can represent — task section 35's own guidance: "Use Playwright
synthetic pages for action-result cases that cannot be represented by static saved
snapshots"). The replay guard (§ Validation) re-confirms zero behavior change across the full
saved corpus for every page-reading/classification decision, which is what that mechanism can
actually attest to.

## 17. Explicitly out of scope, and why

- **Cover-letter upload verification** — no existing filename-aware check exists to reuse for
  it (unlike the resume's three independent checks); building one from scratch was judged
  lower-priority than the confirmed, higher-traffic gaps actually fixed. Residual risk.
- **`_tick_several`/`answer_location_choices`'s grouped tick-box writes, and the Amazon/
  SuccessFactors adapter-specific gaps** discovery found — lower-traffic than the generic
  `do()` dispatch paths fixed; not reached in this pass. Residual risk, not silently dropped.
- **Non-Workday `entry_count()`** — not a base-class hook today; adding it for other adapters
  with no real markup to validate against would be guessing, which task section 34 explicitly
  warns against ("do not claim real-page coverage for cases absent from the corpus").
- **`uncertain_actions` resolution** — this phase only ever appends; no lifecycle clears an
  entry once added (unlike `pending_action`'s explicit dispatch/resolve pair). Building a full
  resolve step was judged out of proportion for this phase's one producer (repeated-entry
  count mismatches); flagged as a residual risk, not quietly left inconsistent.
- **The pre-existing `Outcome.kind` dead-code inconsistency** (`apply_flow.py` dispatches on
  `"blocked_validation_loop"`/`"no_sponsorship"`, which `page_agent.py` never actually
  produces) — confirmed pre-existing in discovery, not introduced or worsened here;
  `handoff.classify()` does not assume either kind is ever produced, so it is unaffected
  either way. Left unfixed, as explicitly out of this phase's scope.
- Per task section 46: no ATS expansion, no SMS/TOTP integration, no checkpoint/submission
  redesign, no AI/model architecture change, no metrics dashboard, no large UI redesign, no
  `main` integration.

## Validation

Baseline, established once from a clean `codex/architecture-reliability` tip (`34bfb9a`), via
`pytest -q -rs -n auto` (matching `.github/workflows/ci.yml`):

```
4 failed, 2217 passed, 3 skipped in 893.18s (0:14:53)
```

All 4 are the same pre-existing `tests/test_page_agent.py` `owner_submits()`-based failures
confirmed unrelated to this phase (and to P0-B3 before it) in every prior round of this
engagement.

During implementation, one real regression was found and fixed before this final run: the
resume-upload verification fix (§6) was initially too strict for a page that never renders
the uploaded filename as visible text (it only held the file on the `<input type=file>`
element itself) -- `PageAgent._resume_file_input_holds_it()` was added so attachment evidence
also covers that deterministic DOM fact, not only the pre-existing filename-in-text check.
Reproduced via `tests/test_page_agent.py::test_the_owner_allowed_signing_so_the_agent_signs_
last_and_submits` failing, then fixed, then confirmed passing again.

Final full suite, same invocation:

```
4 failed, 2288 passed, 3 skipped in 934.51s (0:15:34)
```

The exact 4 failing test names are identical to the baseline's (diffed directly). **Zero new
failures, zero new errors, same skip count.** The passed-count delta (2217 → 2288 = +71)
reconciles exactly: 66 new tests across the three new B4 test files (17 + 4 + 45) plus 5 from
`tests/test_source_has_no_control_characters.py`, the pre-existing repo-wide hygiene check
that parametrizes over every source file and picked up the 5 new `.py` files
(`action_result.py`, `handoff.py`, and the three new test files).

Targeted regression, run separately beforehand, all green: **530 passed** across the B1/B2/B3
regression suites (account/auth, submission-effect, tracker parity, loop-breaker fingerprint,
checkpoint/recovery, dashboard, field fillers) plus a full `tests/test_page_agent.py` run
(**97 passed**, the same 4 pre-existing failures, confirmed twice -- once before finding the
upload-verification regression above, once after fixing it).

Replay guard: **1260 saved real pages, zero differences** in how any is read, classified, or
answered.

## 18. Residual risks

- Cover-letter attachment has no post-upload verification (§17).
- `_tick_several`, `answer_location_choices`, and the Amazon/SuccessFactors adapter-specific
  write paths remain unverified (§17) — discovery flagged these as real but lower-traffic
  gaps.
- `uncertain_actions` only ever grows within this phase; a stale entry from a resolved
  situation persists in the checkpoint until some later phase gives it a resolve step.
- The navigation validation-timing fix calls `self.snapshot(page)` one additional time per
  press when the ref-stripped text already changed — a deliberate, bounded cost (once per
  navigation attempt, not per keystroke), consistent with task section 5's guidance, but worth
  noting as the one place this phase adds a real (small, proportionate) per-action cost.
- The dashboard lookup (`read_checkpoint_by_dedup_key`) is a best-effort, most-recently-
  updated match on a non-canonical key; if an application's checkpoint were ever written under
  a `submission_key` whose `dedup_key` column was for some reason stale or absent, the
  dashboard would simply show nothing extra (fails toward no display, not a wrong display).

## 19. Closure correction — eliminating "verification unavailable -> True" across every confirmed path

A follow-up review of the merged P0-B4 commit (`4200f01`) found the same defect class
recurring at several more sites than the original pass closed: a browser mutation is
attempted, verification is unavailable or bypassed, the helper returns `True` anyway, and
production records the answer as successfully committed. This section fixes every confirmed
instance across the existing supported production mutation paths, without broadening scope
beyond verification of what already exists (no new ATS adapter, no guessed markup).

### 19.1 Ordinary fill — three bypass points in `do()`'s `fill` action

- The native-setter fallback used when `fill_and_dispatch()` itself raises previously logged
  success and `return`ed `True` unconditionally, with **no readback at all**. It now runs the
  same `input_value()` verification the ordinary path uses, via a `try/except/else` around
  the fallback's own `loc.evaluate(...)` call — verified only if the fallback itself didn't
  raise **and** the readback confirms the value.
- The initial `input_value()` readback's `except: return True` was changed to `except: return
  False` — a verification read failure is evidence withheld, not evidence of success.
- The retry's own `input_value()` readback had the identical `except: return True`, fixed the
  same way.

A verification failure here is deliberately left to resolve the same way it already did
before this phase existed: `PageAgent.not_stuck()`'s independent, later, page-cycle re-check
already treats a control that genuinely vanished because the page moved on as fine ("the page
rebuilt itself; the next read will show it") — this fix does not fight that handling, it only
stops a transient or stale readback from being counted as success *before* that later,
stronger check ever runs.

### 19.2 The cached `native_select` recipe path

`self.locate(...).select_option(...)` followed by an unconditional `return True` is now
followed by the same `el.selectedOptions[0].text` readback the live (non-cached)
native-select path already uses. A verification mismatch is routed through the exact same
`except Exception: self._mark_recipe_failed(identity)` branch a throw from `select_option()`
already used — the existing bounded recipe-failure policy now also covers "verified wrong,"
not only "threw."

### 19.3 "choose" dispatched onto a plain textbox/searchbox

`fill_and_dispatch(...)` followed by an unconditional `return True` now reads back
`input_value()` and verifies it the same way the ordinary `fill` action does, reusing the
same `_fill_value_committed()` helper (one shared definition, not a second copy that could
disagree with it).

### 19.4 Cover-letter upload — new verification, never confused with the resume

Cover-letter upload previously set `_letter_attached = True` on a non-throwing
`set_input_files()`/chooser/`assistant.attach_cover_letter()` call alone — no check of any
kind. `PageAgent._confirm_cover_letter_attached(page, path, control)` is new:

1. **Section-scoped text evidence**: reuses `_section_holds_a_file()` — the same
   section-scoped check `attach_documents()` already used as a *pre*-check ("a section that
   already holds a file is left alone") — now also as a *post*-check, scoped to the specific
   control's own section (`container`/`question`) so an attached resume elsewhere on the page
   can never satisfy cover-letter verification.
2. **File-input evidence**: falls back to `_file_input_holds_name()` (generalized from the
   resume's own `_resume_file_input_holds_it`, now a single shared helper both documents use)
   for a section whose markup shows no filename-shaped text at all.

Both reader-path `do()` call sites (the "nearby file input" branch and the final
set-input-files/chooser fallback) now route cover-letter uploads through this verifier instead
of an unconditional `True`. The **non-reader** production path,
`JobApplicationAssistant.attach_cover_letter()` (`browser_automation.py`), had the identical
gap in both of its branches and is fixed the same way: the file branch now re-checks
`_file_already_attached()` (the same evidence it already used as a pre-check) immediately
after the upload; the text-box branch now reads `input_value()` back and requires it to
overlap with the pasted text, rather than trusting `fill_and_dispatch()` alone.

### 19.5 Remaining discovered residual paths — class audit

- **`_tick_several`** ("tick all that apply"): each pick's `set_checked()`/`click()` is now
  followed by `_is_checked(target) is True`; the function returns `True` only if every pick
  verified, `False` otherwise (a verification read failure, `_is_checked` returning `None`, is
  treated as not-verified).
- **`answer_location_choices`**: the checkbox/radio tick loop now only appends a box's name to
  `done` (and therefore only ever writes to `self.written`) once `_is_checked()` confirms it —
  previously appended unconditionally after a non-throwing attempt.
- **Amazon adapter** (`sites/amazon.py`, `answer_platform_question`): the textbox branch now
  reads `input_value()` back after `.fill()`+`.blur()`; the select2 branch now reads the
  widget's own `.select2-selection__rendered` text back after clicking an option — the exact
  same rendered-text evidence `platform_questions()` already reads for this widget, reused
  here as a post-check rather than invented fresh.
- **SuccessFactors adapter** (`sites/successfactors.py`): `upload_attachment` now re-checks the
  adapter's own, pre-existing `attachment_is_empty()` after the chooser/dialog completes,
  rather than reporting success on no-exception alone; `set_date` now reads the date widget's
  own text input back and requires digit overlap with the intended value (the widget may
  reformat separators/order, so this checks the date's digits survived, not an exact string
  match) — a transient/zeroed-out field after the fill sequence is now `False`, not `True`.

No remaining confirmed production mutation path reports success without some positive,
already-existing evidence being checked. The two pre-existing, intentionally-unchanged
exceptions remain: a disabled/hidden field (nothing was mutated, so there is nothing to
verify — `is_inert`/`is_disabled` still `return True` immediately, unchanged) and any path
P0-B1/B2/B3 already governs (final submission, account/auth, recovery reconciliation), which
this closure does not touch.

### 19.6 Validation

Focused B4 closure tests (new, this pass): **27** across `tests/test_action_verification.py`
(generic fill's three bypasses + the production `apply_answers` integration test + the
cached-recipe pair + the choose-onto-textbox pair + three cover-letter cases + two
`assistant.attach_cover_letter()` pairs + `_tick_several`/`answer_location_choices`) and a new
`tests/test_adapter_write_verification.py` (**8**, Amazon's two branches and SuccessFactors'
two methods, each in both directions). All B4 tests together (original + this closure): **120
passed**.

B1/B2/B3 targeted regression (the same suites as every prior round — account/auth,
submission-effect, tracker parity, loop-breaker fingerprint, checkpoint/recovery, dashboard,
field fillers): **530 passed**, unchanged. A full `tests/test_page_agent.py` run: **97
passed**, the identical 4 pre-existing failures.

Full suite (`pytest -q -rs -n auto`):

```
4 failed, 2316 passed, 3 skipped in 817.66s (0:13:37)
```

The exact 4 failing test names are identical to the reference's
(`test_a_whole_application_is_read_answered_and_submitted`,
`test_a_carried_over_no_to_sponsorship_is_corrected_from_the_profile`,
`test_a_submit_button_on_a_step_before_the_last_just_moves_on`,
`test_the_cover_letter_is_attached_where_the_form_asks_for_one`). **Zero new failures, zero
new errors, same skip count.** The passed-count delta (2288 → 2316 = +28) reconciles exactly:
27 new focused closure tests plus 1 from the pre-existing repo-wide source-hygiene check
(`tests/test_source_has_no_control_characters.py`) picking up the one new file,
`tests/test_adapter_write_verification.py`.

Replay guard: **1270 saved real pages, zero differences** (up from 1260 — the two new test
files' own real-browser runs accumulate a few more page recordings in the course of running,
not a behavior change in any saved page's reading/classification).
