# Phase 0-B4 Discovery — Verified Actions, Precise Human Handoff & Observability

Base: `codex/architecture-reliability` at `34bfb9a` (P0-B1 + P0-B2 + P0-B3 merged).
Branch: `fix/action-verification-handoff-observability-hardening`.

Read-only discovery, produced before any P0-B4 production change, across three parallel
audits: (1) field-write/upload/repeated-entry verification, (2) navigation verification and
existing authority boundaries, (3) handoff messaging and the observability/event
infrastructure. It answers the task's fifteen questions, then states what P0-B4 reuses versus
builds new.

## 1. Which important actions already verify their outcome?

- **Ant Design / `rc-select` custom dropdowns**: `interaction.resolve_ant_dropdown()`
  (`interaction.py:213-341`) re-reads `.ant-select-selection-item` after the click and returns
  `False` if the select doesn't show the chosen label — genuinely verified end-to-end. Reused
  for address fields via `wipe_and_enforce_location_sweep` (`interaction.py:533-539`).
- **Checkbox via explicit check/uncheck**: `page_agent.py:4340-4363` ends with
  `_is_checked(loc) == want` — verified.
- **Ashby-style `aria-pressed` toggle buttons**: `PageAgent._press_toggle()`
  (`page_agent.py:4611-4631`) re-parses a fresh snapshot after settling and only returns
  `True` if `now.checked` is true — verified.
- **Date boxes / spinbuttons**: `form_fields.fill_date()` (`form_fields.py:753-775`) and
  `PageAgent.put_in_a_spinbutton()` (`page_agent.py:5023-5059`) both read back
  `input_value()`.
- **Workday's multi-value/tag and searchable-input writers**: `sites/workday.py`'s
  `select_skills()` (617-648) and `select_from_searchable_input()` (650+) re-read the
  committed tag list / `_searchable_value_committed` after each attempt.
- **Page-cycle-level re-verification**: `PageAgent.not_stuck(given, after)`
  (`page_agent.py:3705-3740`), called once per page cycle after a fresh snapshot, independently
  re-derives whether each answer actually stuck (checked-group membership, `is_checked`, or a
  fuzzy value compare) and retries/hands off if not — this is the real "was this field written"
  gate, distinct from and stronger than any single helper's own return value.
- **Final submission**: unchanged, entirely P0-B1's (`submission_guard.SubmissionGuardV0`).
  Durable `DISPATCHED` is written *before* the click; evidence is polled *after*
  (`wait_for_submission_evidence`, whose own docstring says "a click is not evidence").
- **Account/auth**: unchanged, entirely P0-B2's `account_state.read_state()` plus P0-B3's
  restart-safe account-creation lifecycle.

## 2. Which helpers return `True` merely because an interaction was attempted?

The dominant pattern across `page_agent.py`'s live `do()`/`choose()` dispatch. Highest-traffic,
highest-confidence gaps (full list of 21 sites in the raw audit; these are the ones P0-B4
actually touches — see §"What this phase builds"):

1. `page_agent.py:4253-4339` — the generic `fill` action (plain textbox/textarea): performs
   `fill_and_dispatch` + Tab, then `return True` unconditionally. The *only* readback anywhere
   in the block is a narrow city/zip/postal-code special case (4324-4338). This is the single
   highest-traffic unverified path in the file.
2. `page_agent.py:4490-4500` — "choose"/"check" dispatched onto a radio/checkbox/switch via
   group matching (the normal way a multiple-choice question is answered): sets the box,
   `return True`, no `_is_checked()` readback — in direct contrast to the sibling explicit
   check/uncheck path four lines above (§1) which does verify the same control type.
3. `page_agent.py:4698-4708` (`_choose_exact`, native `<select>` branch) — `select_option()`
   then `return True`, no readback, even though `form_fields.choose()`'s own native-select
   branch (`form_fields.py:521-531`) demonstrates the fix already exists elsewhere in the
   codebase and simply isn't reused here.
4. `page_agent.py:4588-4609` (`_tick_several`, "tick all that apply") and `page_agent.py:4556-
   4571` (`answer_location_choices`'s checkbox/radio branch) — both write a group of boxes and
   report success with no `_is_checked()` recheck at all.
5. `page_agent.py:4364-4421` + `page_agent.py:5878-5883` (`_resume_went_on`) — resume/cover-
   letter upload sets `resume_uploaded = True` the instant the Playwright call avoids throwing.
6. `page_agent.py:1933-1965` (`add_entries_the_site_way`) — marks a Work Experience/Education
   section done/redone *before* invoking the site's bulk filler, never re-checks afterward.
7. `sites/successfactors.py:56-78` (`upload_attachment`) — reports success on no-exception,
   despite the same adapter class exposing `attachment_is_empty()` (45-54), never called after.
8. `sites/amazon.py:65-96` (`answer_platform_question`) — both branches (textbox, select2)
   report success with no readback of the resulting committed text.

## 3. Which call sites interpret attempted == succeeded?

Every call site that records into `self.written[...]` does so immediately off `do()`'s boolean
return (`page_agent.py:2734-2737, 3379-3385, 3092-3095, 3514, 3548, 3595/3610, 3980, 4073,
4569/4582`) — no re-check at that exact moment. The real safety net is `not_stuck()` (§1),
called once per page cycle, which independently re-derives success from a fresh snapshot and
triggers a retry/hand-off if something didn't stick. Gaps in that net: it explicitly **skips
uploads** ("a file shows up differently on every site; the submit check looks for it",
`page_agent.py:3713-3714`), and if a control can no longer be matched in the fresh snapshot at
all, it's silently treated as fine ("the page rebuilt itself", `page_agent.py:3715-3717`)
rather than flagged. `self.written` is never retroactively corrected if `not_stuck()` later
finds a value missing.

`_answer_verification_state`/`_commit_learned_memory` (`page_agent.py:5338-5391`) are a
**separate, narrower** mechanism — P0-B3's docs correctly describe this as "stage now, verify
on a later snapshot, commit only if verified," but it gates only cross-run *recipe/answer
memory* persistence, not the immediate in-run "was this field written" question (that's
`not_stuck()`'s job, with the upload-skip and vanished-control gaps noted above). It explicitly
skips upload actions too (`page_agent.py:5341-5342`).

## 4. Which actions already re-read the DOM after acting?

See §1's list (Ant dropdown, explicit checkbox, toggle buttons, date/spinbutton, Workday
multi-value writers) plus `not_stuck()`'s page-cycle-level re-read. Everything in §2's list
does not.

## 5. Which actions already use validation errors as evidence?

- `page_agent.py:819-833` `page_errors(snapshot)` extracts quoted/trailing error-shaped text.
- `page_agent.py:2762-2783` `PageAgent.stuck_on_errors(self, page)` re-reads `page_errors()` —
  but it is called **only from the main loop, only after `press_next` has already returned
  `"retry"`** (call site `page_agent.py:3292-3297`). If the same error tuple repeats, it
  escalates to `Outcome("owner_needed", ...)`; if new, it feeds the next plan iteration.
- **Confirmed timing gap**: this check never runs on the path where `_press_next_locked`
  decided `"moved"` (§ "Navigation verification" below) — a press that changes the ref-stripped
  snapshot text even slightly while a validation banner remains visible is read as success,
  with zero error inspection.
- A related, independent, narrower check, `PageAgent.rejected_by_the_page()`
  (`page_agent.py:1997-2015`), runs *before* `press_next` is even called, for the specific case
  of "the page says a field is required even though it visibly shows a value."

## 6. Which uploads verify actual attachment?

Nothing does so *at upload time*. Filename-aware DOM checks exist and are real —
`_resume_on_page()` (`page_agent.py:5869-5876`), `_file_already_attached()`
(`browser_automation.py:4008-4023`), `resume_seen` (set in `_save()`,
`page_agent.py:5924-5927` only if the filename is found in a saved snapshot) — but every one of
them is wired as a **pre-check** (avoid re-uploading / detect "already done from an earlier
pass"), never as a **post-check** confirming a just-performed upload actually landed. The
submit-time gate `_tailored_resume_missing()` (`page_agent.py:5768-5801`) is an OR across
`resume_uploaded OR resume_seen OR _resume_on_page() OR _resume_attached_before()`; since
`_resume_went_on()` sets `resume_uploaded = True` unconditionally the instant the Playwright
call doesn't throw, it short-circuits this OR to "attached" before the more rigorous,
filename-aware checks ever get consulted, for the very run the upload happens in. Cover-letter
attach has no post-upload confirmation and no equivalent of `resume_seen`/`_resume_on_page` at
all.

## 7. Which Next/Continue paths verify page advancement?

The one production press path, `PageAgent._press_next_locked()`
(`page_agent.py:5558-5766`, via the thin wrapper `press_next`, `page_agent.py:5548`). Its
**only** post-press check (`page_agent.py:5761-5766`) is a blunt whole-page,
ref-stripped-accessibility-snapshot **text-equality diff**:

```python
after = re.sub(r"\[ref=[\w-]+\]|\[active\]", "", self.snapshot(page))
if after == before:
    ...  # "retry", pulling any "alert" text found purely to enrich the message
return "moved", page, ""
```

No step-indicator comparison, no URL comparison, no positive check for a validation-error
panel participates in the moved/retry decision itself — error text is only pulled in *after*
"retry" has already been chosen, to enrich the message (§5's gap). A same-URL wizard advance to
a later step is indistinguishable in kind from a no-op in this check — it only "works" because
new questions *usually* change the visible text, not because advancement is positively detected.

`StateFingerprintCircuitBreaker`/`compute_state_fingerprint()` (`state_machine.py`) is
constructed and invoked exactly once in production code (`page_agent.py:5702-5706`),
**immediately before** the click, purely to decide whether 3 consecutive identical reads
should trip `BLOCKED_VALIDATION_LOOP` — it is never recomputed *after* the press to confirm
the press caused movement; it answers "are we stuck," not "did this press work."

A real, purpose-built step-ordinal AHEAD/BEHIND concept exists (`recovery.py`'s
`parse_step_ordinal`/`reconcile()`, from P0-B3) but is scoped entirely to process-restart
resume reconciliation (comparing a durable checkpoint to a freshly reopened page) and is
advisory-only even there (`log_recovery_reconciliation`'s own docstring: "Logs (never acts
on)"). It is not wired into the per-press loop at all.

## 8. Which repeated-entry Add/Save paths verify the entry exists afterward?

- `repeated_entries.py` itself has no add/count-driving logic — only parsing (`entry_map()`)
  and per-field answer/describe helpers.
- `PageAgent.add_entries_the_site_way()` (`page_agent.py:1912-1965`) pre-checks
  `adapter.entry_count(tab, heading)` (only Workday implements this — §"entry_count" below) to
  decide whether to fill/redo/skip a section, but marks the section `done`/`redone`
  **before** calling the filler and never re-checks `entry_count()` afterward. A filler that
  throws (swallowed, logged) or completes "successfully" with 0/wrong entries is invisible for
  the rest of the run.
- A **different, better-verified** path exists for the plan-driven "Add Experience" button
  press: `PageAgent.press_next()` (`page_agent.py:5601-5616`) recomputes the current entry
  count from a fresh snapshot-derived `entry_map` and compares it to the profile's own history
  count before deciding whether pressing Add again is warranted — a genuine before-you-add-
  again check, but it only guards the plan's own button press, not the site-adapter-filler path
  above.
- Field-level corrections *inside* an already-existing entry (`_correct_entries`) do flow into
  the page-cycle `not_stuck()` safety net (§1/§3); only the bulk "fill N entries via the site's
  own filler" act sits entirely outside any verification net.

## "entry_count()" / attachment-evidence helpers: implemented but only used as pre-checks

Only `sites/workday.py` implements `entry_count()` (`sites/workday.py:232-240`); it is not even
declared as a base-class hook in `sites/base.py` (unlike `attachment_is_empty`,
`upload_attachment`, `open_picker`, `set_date`, which are), so `add_entries_the_site_way`'s
`hasattr(adapter, "entry_count")` gate silently no-ops for every non-Workday adapter. The same
"pre-check only, never post-check" pattern recurs for `attachment_is_empty()` — implemented by
Workday, Greenhouse, Amazon, and SuccessFactors adapters, called only *before* an upload to
decide whether to upload at all, never *after* to confirm a just-performed upload landed.

## 9. Which account/auth actions are already sufficiently covered by B2/B3?

Confirmed unchanged and accurate as of `34bfb9a`: `account_state.read_state()`
(`account_state.py:145`, a pure function of a snapshot string, 14 kinds, decision table
`next_step()` at 289-363) and `login_guard.py` (file-backed 24h sliding-window rate limiter
keyed `host|email`, read/write API unchanged). Neither reads or writes anything in
`checkpoint.py`. P0-B4 does not need to, and must not, touch either.

## 10. Which final-submit actions are already sufficiently covered by B1?

Confirmed unchanged chain: `apply_flow.submit_verified()` → `refuse_submission_replay()` (fail
closed on existing DISPATCHED/AUTHORIZED/UNCERTAIN/CONFIRMED) → `assistant.click_verified_submit()`
→ `_click_verified_submit_locked()` (re-checks CAPTCHA/attestations/legal conflicts, finds the
button) → `SubmissionGuardV0.submit_verified()` (validates authorization, durably
`begin_submission_dispatch()` *before* the click, authorizes+clicks via a page-injected
capability token, compares denial-count before/after) → back in `apply_flow`,
`wait_for_submission_evidence()` (polls for confirmation; "a click is not evidence" per its own
docstring) → `finish_submission_effect(CONFIRMED|UNCERTAIN)`. `press_next`'s own `submit_gate()`
is the only point of contact: it refuses to let `_press_next_locked` click anything classified
`final`/`submit_word` until `submit_gate()` returns `""`, and `submit_gate(last_step=True)`
always returns a non-empty "automatic submission is off" string unless overridden — the real
last-step Submit is never auto-clicked from inside `PageAgent`. P0-B4 does not need to, and must
not, touch any part of this chain.

## 11. How human handoff reasons are currently generated

Entirely inside `sign_in_step()`/`complete_account_code()` for account-related stops
(`self.account_blocker`, reset to `""` every pass, `page_agent.py:1318, 1380, 1628`) and
directly inside `PageAgent._run_locked()`/its helpers for everything else (`Outcome(kind=
"owner_needed", ...)`, plus `"captcha"`, `"gave_up"`, `"disqualified_policy_mismatch"`,
`"needs_user"` as distinct `Outcome.kind` values). A **third**, independent mechanism exists for
the review-border hand-over: `apply_flow.hand_over()` builds its own `report` dict and calls
`safety.handover_status(report)` for the message — built from `validate_application()`/
`read_back_fields()` output, not from `Outcome.reasons`/`account_blocker`. A **fourth** exists
for post-review submission-evidence messages (`safety.verification_status()`). There is no
single, consistent handoff-message pipeline today — four independent message-construction
mechanisms feed into `tracker.update_status(..., notes=...)` by different routes.

Of roughly 20 distinct human-facing stop messages surveyed (11 `account_blocker` sites + ~12
direct `Outcome` sites), only **two** interpolate any site-identifying token into the message
text itself (`page_agent.py:1747-1750` and `:1782`, both using the raw network **host**, e.g.
"acme.wd1.myworkdayjobs.com" — never the human-readable company name). The rest — including
every message routed through `account_state.next_step().why`, which is most of them — say
nothing about which employer, portal, or stage produced them (e.g. `"the account step never
showed its form: look at the page, then press Continue"`, `"this page keeps asking the same
things and is not moving on -- the rest of it needs you"`). `self.job.company` is never
interpolated into any handoff message anywhere — it's only available separately, on the
dashboard's own list/detail rendering.

## 12. Which handoffs currently lose employer/portal/stage/action context?

Effectively all of them, in the message text itself (§11). The context (`self.job.company`,
`self.job.title`, the URL/host) exists on the `PageAgent`/dashboard but is not woven into the
stop reason string that becomes `Outcome.summary` → `tracker.notes` →
`checkpoint.handoff_reason`. Confirmed: **`application_checkpoints.handoff_reason` (P0-B3) is
write-only** — written at exactly one call site (`apply_flow.py:1054-1056`, inside
`write_recovery_checkpoint`), and never read back anywhere except `checkpoint.py`'s own parser
and tests. `web_ui.py` has zero occurrences of the word "checkpoint." A dashboard/process
restart today genuinely does collapse a precise stop reason into nothing — the checkpoint
carries the information but nothing ever displays it.

`applications.notes` (the field the dashboard *does* render, in full on the detail page and
truncated on the list) is a **single overwritten value** (`UPDATE ... SET notes =
COALESCE(?, notes)` in both backends) — each new stop reason replaces the previous one; only
`application_events` is append-only, but the dashboard's history table (§13/§14) only ever
shows `kind` + the first 160 characters of `message`, never `payload`.

## 13. Which actions/events already go through `tracker.record_event()`?

Real (non-test) `kind` values found, by call site:

| kind | file:line | message | payload |
|---|---|---|---|
| `"note"` | `apply_flow.py:719` (recovery reconciliation log) | `"recovery: {outcome} -- {why}"` | none |
| `"note"` | `apply_flow.py:751` (`remember_progress`) | `"{outcome.summary}: {url}"` | none |
| `"note"` | `apply_flow.py:1631-1632` (resume pickup) | `"picked up again; was {status}"` | none |
| `"auto_submit"` | `apply_flow.py:941-945` (`hand_over`) | `decision.summary()` | **yes** — `AutoSubmitDecision.as_dict()`: `eligible`, `reasons`, `field_comparisons` (each `{label, on_form, approved, source, matches, required}`), `evidence_paths`, `decided_at` |
| `"resume_attached"` | `browser_automation.py:4533`, `page_agent.py:5888` | resume filename only | none |
| `"status_change"` | `web_ui.py:522` | fixed template, `new_status` only | none |

A **separate, look-alike** API exists for submission safety specifically:
`SubmissionGuardV0._record_event` → `tracker.record_submission_safety_event(dedup_key, kind,
payload)`, writing to the distinct, append-only, trigger-protected `submission_safety_events`
table (kinds: `SUBMISSION_BLOCKED`, `SUBMISSION_STORAGE_FAILURE`, `SUBMISSION_REPLAY_BLOCKED` —
small, fixed-vocabulary diagnostic payloads, no form content). This is P0-B1's existing
machinery and is out of scope for P0-B4 to touch or duplicate.

## 14. Which important decisions exist only in logs?

Every distinct validation-retry/error-classification decision inside `_press_next_locked`/
`stuck_on_errors`/`rejected_by_the_page` (§5/§7) — none of it is recorded through
`tracker.record_event` today, only through `logger.info`/`logger.warning` calls and the
generic `outcome.summary` text that eventually reaches `applications.notes`. Similarly, every
field-write "returned True without verification" site in §2 produces no event of any kind —
success or failure is visible only via `self.written`/log lines, never durably recorded as a
distinct fact.

## 15. Which events risk containing sensitive form values?

One clear, major existing case: `apply_flow.py:941-945`'s `"auto_submit"` payload
(`field_comparisons`) carries real, applicant-specific form answers (`on_form`) side by side
with the agent's approved values (`approved`) — by design, since the owner needs to see exactly
this to verify an auto-submit decision was correct, and it is already rendered back verbatim on
the dashboard's "Fields checked" table (`web_ui.py:1437-1453`). This is a pre-existing,
deliberate P0-B1 transparency feature, not a P0-B4 concern to fix — it is cited here only as
precedent/contrast: **any new P0-B4 structured event payload must be held to a stricter,
secret-safe standard than this existing precedent**, per the task's explicit instructions (no
raw form values, no question text beyond a short label, truncate/sanitize). Secondary, lower-
severity candidates: `page_agent.py:5523`'s blocker text embeds the actual form question
(`f"needs your answer: {question[:90]} ..."`, could be a sensitive screening question);
`page_agent.py:5496`'s note embeds both the page's text and the profile's own fact value side
by side; resume filenames (often containing the applicant's real name) land in
`application_events.message` via the `"resume_attached"` kind.

## Two other confirmed, load-bearing findings

### `checkpoint.py`'s `last_verified_action`/`uncertain_actions` are schema-only today

Of P0-B3's three lifecycle fields, only `pending_action` has a real production producer
(the account-creation dispatch lifecycle, entirely inside `page_agent.py`). `last_verified_
action` and `uncertain_actions` are defined, parsed, round-tripped through `merge_update()`,
and asserted-sticky in tests — but **no production code path writes a non-empty value into
either field today**. (Also noted: `last_verified_action` is not itself in `checkpoint.py`'s
`STICKY_FIELDS` tuple — moot today since nothing writes it, but worth deciding deliberately
when P0-B4 gives it a real producer.)

### A pre-existing `Outcome.kind` vocabulary inconsistency (dead code, not introduced by this audit)

`apply_flow.py` dispatches on `outcome.kind == "blocked_validation_loop"` (`apply_flow.py:1069`)
and `outcome.kind == "no_sponsorship"` (`apply_flow.py:1089`), but `page_agent.py` never
actually constructs either kind — it produces `"disqualified_policy_mismatch"` instead
(`page_agent.py:2921`). Both `apply_flow.py` branches are confirmed dead code today (repo-wide
grep: `"blocked_validation_loop"` appears only in that one dispatch, a dashboard status-pill
styling reference, and a test name; `"no_sponsorship"` only in that one dispatch). This predates
P0-B4 and is not something this phase is asked to fix; it's recorded here because it directly
informs the handoff-category normalization work (§21 of the task) — any new category mapping
must not assume these two kinds are ever actually produced.

## What this phase reuses rather than rebuilds

Per the above: P0-B1's entire submission chain, P0-B2's `account_state`/`login_guard`, P0-B3's
checkpoint storage/merge/reconciliation mechanics, and every already-verified helper listed in
§1 (Ant dropdown resolution, explicit checkbox check/uncheck, toggle buttons, date/spinbutton
fill, Workday's tag/searchable-input writers, `not_stuck()`'s page-cycle safety net). P0-B4's
job is the gaps in §2-§8 and §11-§15, not a parallel system alongside any of the above.
