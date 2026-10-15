# Phase 0-B1 — Remediation Pass 4

## Scope

Remediates all six findings in `docs/security/phase0-b1-adversarial-review.md`
(7 October 2026), Finding 2 (alias annexation permitting a second dispatch)
first as the confirmed blocker, then Findings 1, 3, 4, 5, 6 in the same pass.
No P0-B2 work was started. No unrelated refactors were made.

---

## Finding 2 — Alias annexation permitting a second dispatch (BLOCKER)

### Root cause

`job_tracker.JobTracker._identity_root()` resolved an alias key with a single
SQL lookup (`alias_key -> dedup_key`, one hop). When an established root with
its own pre-existing dependent (`B` owning `B_alias`) was itself annexed under
a new root (`A`), only `B`'s own row was updated to point at `A`; `B_alias`'s
row still pointed at `B`. Resolving `B_alias` therefore stopped at `B` and
never discovered `A`. Once `A` reached `DISPATCHED`, `B` and `B_alias` had no
row of their own, so `begin_submission_dispatch("B_alias")` found no blocking
state anywhere it looked and wrote a fresh, unblocked `DISPATCHED` row.

### Fix

Replaced single-hop resolution with transitive canonical resolution:

- `JobTracker._resolve_root(conn, key)` walks the alias chain to its true,
  terminal root (bounded to 64 hops as a cycle-safety backstop; merges cannot
  create a cycle given the existing guards, so this is never expected to
  trip).
- `JobTracker._identity_group(conn, key)` is now a reverse breadth-first walk
  from that canonical root through every recorded alias edge at any depth, in
  both directions of the graph (a root annexed under a still-higher root, or a
  multi-hop chain on either side, is found completely).
- `get_submission_effect_state()`, `begin_submission_dispatch()`,
  `finish_submission_effect()`, `record_submission_safety_event()`, and
  `submission_safety_events()` all resolve their `dedup_key` argument to its
  canonical root *first*, then read or write that one row. Every member of a
  merged identity now operates on the literal same database row -- not a
  caller-supplied alias list checked alongside a separate "own" row, which is
  the structural gap that let the two diverge.
- `begin_submission_dispatch()`'s canonical resolution and its state read/
  write happen inside the same `BEGIN IMMEDIATE` transaction, so there is no
  gap between determining the canonical identity and deciding whether to
  block.
- `record_submission_identity_alias()` is unchanged in its refusal rules (an
  alias with its own independent `submission_effects` row, or already
  recorded under a different root, is never merged) but now resolves the
  primary's root transitively before attaching the new alias to it.

### Files changed

`job_tracker.py` (identity resolution, all submission-effect accessor
methods); `apply_flow.py`'s `refuse_submission_replay()` already consulted
`submission_identity_group()` from Pass 3 and required no further change --
it benefits from the corrected resolver automatically.

### Regression tests added (`tests/test_submission_effect_state.py`)

- `test_annexing_a_root_with_an_existing_dependent_still_blocks_that_dependent`
  -- the exact reproduction: `B` owns `B_alias`, `A` annexes `B`, `A`
  dispatches; `A`, `B`, and `B_alias` are each independently confirmed
  refused and each independently confirmed to read back `DISPATCHED`.
- `test_canonical_resolution_handles_a_four_level_stored_chain` -- a raw,
  directly-inserted `alias3 -> alias2 -> alias1 -> root` chain; every one of
  the four keys resolves to the identical, complete four-member group, and
  dispatching under `root` blocks all three aliases.
- `test_a_root_annexed_beneath_another_root_still_blocks_its_own_dependents`
  -- `leaf -> mid` built first, then `mid` (a root with its own dependent)
  annexed beneath `application-identity`; both `leaf` and `mid` are refused
  once `application-identity` dispatches.

### Targeted result

`tests/test_submission_effect_state.py`: `46 passed`.

### Full-suite result

See the combined full-suite section below; this change is included in it.

### Reproduction: the exact previously-successful call, now refused

Before the fix (captured during triage, reproducing the adversarial review's
own finding):

```
begin_submission_dispatch(B_alias) -> NOT BLOCKED: DISPATCHED   <-- SECOND DISPATCH
```

After the fix, the identical call sequence:

```
=== B_alias / B / A annexation ===
  root-A: BLOCKED [OK]
  root-B: BLOCKED [OK]
  B-sub-alias: BLOCKED [OK]
```

`tracker.begin_submission_dispatch("B-sub-alias")` now raises
`RuntimeError: Submission replay blocked by durable state DISPATCHED` instead
of returning `"DISPATCHED"`.

---

## Finding 1 — Unrecognized-label submit bypass (BLOCKER)

### Root cause

Two independent layers both recognized only a closed, enumerated vocabulary:
`safety.SUBMIT_LABEL_RE` (Python) and the identical pattern injected into
`submission_guard.py`'s browser-side `finalControl()`. A `button type="button"`
with a custom `onclick` handler and an unlisted label ("Confirm & Send",
"Finalize", a localized equivalent) matched neither the label vocabulary nor
`structuralSubmitter` (which requires `type="submit"` or no `type`
attribute), so `finalControl()` returned `null` and the click passed through
uncontained.

### Fix

Vocabulary was widened (layer 1), but the real fix is structural (layer 2,
the one that does not depend on anticipating every synonym): `submission_guard.py`'s
`finalControl()` gained a bounded default-deny rule. A labelled,
form-associated, enabled control that is not a known submit synonym, not
ordinary navigation (Next/Continue), not a known non-final/utility word
(add/remove/edit/cancel/back/save-for-later/finish-later/...; new
`safety.SAFE_UTILITY_LABEL_RE`), not a dropdown/disclosure trigger
(`aria-haspopup`/`aria-expanded`/`aria-controls`), and is the *sole* such
remaining candidate in its form, is treated as a possible final submit and
denied. An icon-only or otherwise unlabeled control is left exactly as before
(a pre-existing, documented limitation, not newly swept in).

Two false positives were found and fixed during verification, not left for
someone else to discover: a bare "Finish Later" control (a real label in this
project's own `test_page_agent.py` fixture) and a BambooHR-style
`aria-haspopup` dropdown opener beside a "Choose File" button both initially
tripped the new rule and were added to the exclusion lists.

A third false positive -- bare "Done" and "Complete" colliding with
`interaction.commit_draft_cards()`'s own, pre-existing use of "Done" as a
per-entry card-commit button label -- was found in the full-suite run (see
"Regression found and fixed" below) and fixed by removing those two
ambiguous bare words from `SUBMIT_LABEL_RE`; the structural rule still
catches a genuine final use of either word without needing the word in any
vocabulary.

### Files changed

`safety.py` (`SUBMIT_LABEL_RE` widened, minus "done"/"complete"; new
`SAFE_UTILITY_LABEL_RE` / `is_safe_utility_label()`); `submission_guard.py`
(`finalControl()`'s new default-deny branch, `opensSomething()` exclusion).

### Regression tests added (`tests/test_submission_firewall.py`)

- `test_unrecognized_label_on_a_type_button_control_is_still_blocked`
  (parametrized: Confirm & Send, Confirm and Send, Review & Send, Finalize,
  Complete, Done, Save & Finish, Proceed, Postuler, Bewerben) -- the exact
  adversarial reproduction, now blocked.
- `test_unrecognized_label_alongside_a_real_next_button_still_moves_on` --
  the rule must not fire when an ordinary Next/Continue exists.
- `test_known_safe_utility_labels_on_type_button_controls_still_work`
  (parametrized) and `test_finish_later_beside_a_real_next_button_is_not_swept_up`
  and `test_a_dropdown_opener_beside_a_file_chooser_is_not_swept_up` --
  the three false positives found and fixed during this pass.
- `test_unlabeled_icon_only_control_is_not_swept_up_by_default_deny`.

### Targeted result

`tests/test_submission_firewall.py`: `52 passed`.
`tests/test_phase2_primitives.py` (covers `commit_draft_cards`): `7 passed`.

### Reproduction

Before the fix: `<button type="button" onclick="window.submitted=true">Confirm &amp; Send</button>`
clicked through with `window.submitted == True` and zero guard denials.
After the fix, the identical click leaves `window.submitted is None` and
records one `click`-kind denial.

---

## Finding 3 — Inactive guard on new tabs/popups (BLOCKER)

### Root cause

`SubmissionGuardV0`'s injected script starts every document at
`state.active = false`; only an explicit `activate(page)` call set it to
`true`. The constructor evaluated the script into existing pages/frames but
never called `activate()` on them, and nothing hooked `BrowserContext`'s
`"page"` event, so a page opened after construction (`window.open()`,
`target="_blank"` -- common on Taleo/BrassRing/Workday external links) never
had its guard activated by anything, on any of its documents, for its whole
lifetime.

### Fix

`SubmissionGuardV0.__init__()` now calls `self.activate(page)` for every page
already in the context at construction time, and registers
`context.on("page", self._activate_new_page)` so every page opened afterward
is activated automatically. `activate()`'s existing `framenavigated`/
`frameattached` listeners (unchanged) then keep re-arming the guard across
that page's own future navigations, including cross-origin ones -- this was
already correct for a page `activate()` had been called on; the gap was only
that popups/pre-existing pages never got that first call.

### Files changed

`submission_guard.py` (`SubmissionGuardV0.__init__`, new `_activate_new_page`).

### Regression tests added (`tests/test_submission_firewall.py`)

- `test_popup_opened_after_the_guard_is_constructed_is_still_guarded` --
  `window.open()` from a page, a real cross-origin `goto()` inside the popup,
  then a submit click, with no explicit `activate()` call from the test.
- `test_page_that_already_existed_before_guard_construction_is_guarded` --
  the other half: a page navigated before `SubmissionGuardV0()` is even
  constructed.

### Targeted result

`tests/test_submission_firewall.py`: `52 passed` (included above).

### Reproduction

Before the fix: a popup opened via `window.open()` after guard construction,
then navigated to a routed cross-origin URL, clicked a
`button[type=submit]` labeled "Submit Application" with `window.submitted`
becoming `True` and zero denials recorded for that page. After the fix, the
identical sequence leaves the form's effect unset and records one denial.

---

## Finding 4 — Tracking-parameter identity fragmentation (HIGH)

### Root cause

Three separate copies of the same narrow tracking-parameter exclusion list
(`jd_analyzer.submission_identity_url`, `job_tracker._strip_tracking`,
`db._strip_tracking`) each only stripped `utm_*`, `gh_src`, `src`, `source`,
`ref`, `referrer`, `trackingid`. Platform click-ids (`trk`, `fbclid`,
`gclid`, `msclkid`, `twclid`, `li_fat_id`, `mc_cid`, `rb_clickid`,
`shared_id`) were absent from all three, so the same posting reached via a
referral link hashed to a different effect key than the bare URL.

### Fix

Consolidated into one principled policy in `jd_analyzer.py`:
`is_tracking_query_param()`, matched by a small set of known exact names, a
`*clid` suffix rule covering the whole ad-click-id family generically
(`gclid`/`fbclid`/`twclid`/`dclid`/future unlisted variants), and
`msclkid` as an explicit exact name (it is spelled "clkid", not "clid", and
the suffix rule alone misses it -- caught and fixed during verification, not
left for someone else to find). `job_tracker._strip_tracking` and
`db._strip_tracking` now both call this one function instead of keeping
their own copies -- one decision, one place, so a future tracker only needs
updating once.

An identifying parameter this function does not recognize is never stripped:
verified directly against a materially different job id and an arbitrary
unrecognized query parameter, both of which still produce distinct effect
keys.

### Files changed

`jd_analyzer.py` (new `is_tracking_query_param()`), `job_tracker.py`,
`db.py` (both now call the shared function).

### Regression tests added (`tests/test_submission_effect_state.py`)

- `test_platform_click_tracking_parameters_do_not_fragment_identity`
  (parametrized over `trk`, `fbclid`, `gclid`, `msclkid`, `twclid`,
  `li_fat_id`, `mc_cid`, `rb_clickid`, `shared_id`, `dclid`).
- `test_tracking_parameter_stripping_does_not_remove_identifying_query_params`.

### Targeted result

`tests/test_submission_effect_state.py`: `46 passed` (included above).

### Reproduction

Before the fix: `submission_effect_key_for_url(".../job/123?job=123&trk=linkedin_jobs")
!= submission_effect_key_for_url(".../job/123?job=123")`. After the fix, the
two are equal for every parameter in the list above, while a materially
different job id or an unrecognized query parameter still produces a
different key.

---

## Finding 5 — Login-host alias skipping (HIGH)

### Root cause

`apply_flow._remember_submission_identity_alias()` was called from inside
`remember_progress()`, gated by the same `worth_returning_to(url)` check
`remember_progress()` already used for an unrelated purpose (deciding
whether a URL is worth reopening a human at for Resume). That function's
exclusion list drops any URL containing `/login`, `signin`, or `sign-in` --
so a genuine login-host hop in the posting -> login -> ATS -> application ->
review chain was silently never recorded as an alias.

### Fix

The alias-recording call was moved out from behind `worth_returning_to()`
and made unconditional (gated only by `submission_effect_key_for_url()`'s own
validation, which already rejects non-HTTP(S) and insufficiently specific
URLs such as `about:blank`). `update_last_page()` (the Resume-UX concern)
keeps its own, separate `worth_returning_to()` gate -- the two questions
("is this the same submission identity" and "is this worth reopening a
human at") are different questions and no longer share one gate.

### Files changed

`apply_flow.py` (`remember_progress()`).

### Regression tests added (`tests/test_submission_effect_state.py`)

- `test_login_host_hop_is_now_recorded_as_an_alias` -- a login URL is now
  found in the bound identity's alias group, while `last_page_url` (the
  Resume-UX concern) remains unset, confirming the two gates are correctly
  decoupled rather than one silently weakening the other.

### Targeted result

`tests/test_submission_effect_state.py`: `46 passed` (included above).

### Reproduction

Before the fix: `apply_flow.remember_progress()` called with a login URL
recorded nothing in `submission_identity_aliases`. After the fix, the same
call leaves the login URL's effect key in `submission_identity_group(bound_key)`.

---

## Finding 6 — Confirmation false positives (MEDIUM)

### Root cause

`browser_automation.JobApplicationAssistant.submission_confirmed()` matched
`_CONFIRMATION_PHRASES` as a bare substring anywhere in the whole page body
(`phrase in body`). An unrelated FAQ/error/help page containing incidental
text such as "Application received after 5pm will be reviewed next day" or
"Status inquiry for previously submitted application" matched and was
recorded `CONFIRMED` even though no submission had occurred on that run.

### Fix

Replaced the whole-body substring search with `_CONFIRMATION_HEADING_RE`,
matched only against the full text of heading/alert elements
(`h1, h2, h3, [role=alert], [role=status]`), anchored at both ends (minor
trailing punctuation aside) so the *entire* heading text must be one of the
known short confirmations, not merely contain the words inside a longer
sentence. A short, bounded trailing mention of the role/company is allowed
specifically after "thank you ... for applying" (real-world confirmations
commonly add it, and no plausible unrelated-policy sentence starts that way);
the terser phrases ("Application submitted.") stay strict. Both named
misleading examples, and several additional adversarial variants constructed
during verification (a multi-sentence heading mixing a real "Thank you for
applying." with a trailing misleading disclaimer in the same element), were
checked and correctly rejected.

Under-confirming (a real confirmation this does not recognize, left
`UNCERTAIN` pending manual reconciliation) is the deliberately safe fallback
direction and was not treated as a defect to eliminate -- it does not permit
a second submission.

### Files changed

`browser_automation.py` (`submission_confirmed()`,
`_CONFIRMATION_HEADING_PHRASES` / `_CONFIRMATION_HEADING_RE` replacing
`_CONFIRMATION_PHRASES`).

### Regression tests added (`tests/test_safety_rules.py`)

- `test_incidental_mentions_on_an_unrelated_page_are_not_a_confirmation`
  (parametrized: both named adversarial examples plus two additional
  misleading-heading variants).
- `test_a_real_confirmation_heading_is_still_recognised` (parametrized:
  positive fixtures across several real-world phrasings).

### Targeted result

`tests/test_safety_rules.py`: `47 passed`.

### Reproduction

Before the fix: `<h2>Application received after 5pm will be reviewed next
day</h2>` made `submission_confirmed()` return `True`. After the fix it
returns `False`; a genuine `<h1>Thank You for Applying!</h1>` still returns
`True`.

---

## Regression found and fixed during full-suite validation

The first full-suite run after all six findings' fixes landed showed `19
failed` (Pass 3's known baseline of 18 pre-existing, unrelated
dirty-worktree failures, plus one new failure):
`tests/test_phase2_primitives.py::test_commit_open_sections_preserves_safety_against_submit_label`.

Root cause: `interaction.commit_draft_cards()` has an existing, deliberate
safety check (`if safety.is_submit_label(label): continue`) that *skips*
clicking a button if its label reads as a final submit -- while still
clicking its own hardcoded target list, which includes bare `"Done"`, as the
legitimate per-entry card-commit action. Finding 1's `SUBMIT_LABEL_RE`
widening had added bare "done" (and "complete") as recognized final-submit
synonyms, so the safety check now skipped the legitimate "Done" click too.

Fix: removed bare "done" and "complete" from `SUBMIT_LABEL_RE` (kept
"complete application", which is unambiguous). Both words are also common
labels for committing a single inline entry or marking a sub-step complete,
a different action from a final application submit, and too ambiguous as
single words to ever be a safe universal synonym for either meaning. A
genuine final use of either word on an application step is still caught by
the Finding 1 structural default-deny rule, which does not depend on the
word at all -- verified directly:
`test_unrecognized_label_on_a_type_button_control_is_still_blocked` is
parametrized over `"Complete"` and `"Done"` among other labels and passes.

This is recorded here, not folded silently into Finding 1's section above,
because it is a regression this remediation introduced and then found and
fixed within the same pass, exactly as the exit criterion requires: Pass 4
is not accepted merely because the six original demonstrations stop
reproducing.

---

## Collection and full-suite accounting

- **Collection before this pass** (Remediation Pass 3's final state):
  `1965` node IDs, `1` pre-existing error.
- **Collection after this pass**: `2012` node IDs, the same `1` pre-existing
  error (`tests/test_ant_select.py` importing `_somewhere_profile`, absent
  from the inherited, locally modified `tests/test_page_agent.py` since
  Pass 1 -- unrelated to this work and not modified by it, per the owner's
  standing instruction not to touch unrelated dirty-worktree tests).
  `+47` collected, all new regression tests from this pass; no baseline test
  disappeared.
- **Targeted result** (all tests added or touched by this pass, run
  together): `tests/test_submission_effect_state.py tests/test_submission_firewall.py
  tests/test_auto_submit.py tests/test_repeated_entries.py tests/test_safety_rules.py
  tests/test_confirmation_needs_evidence.py tests/test_phase2_primitives.py`:
  all passed (see each finding's section for the per-file counts from the
  runs that isolated them).
- **Dropdown/toggle-button/form-field regression check** (most exposed to
  Finding 1's structural rule and Finding 3's always-on guard, run as its
  own pass since none of it was touched by this remediation):
  `tests/test_dropdown_reading.py tests/test_toggle_button_choices.py
  tests/test_form_fields.py tests/test_blank_dropdown_choices.py`:
  `147 passed`.
- **Full suite, first run** (`python -m pytest -q -rs -n auto`):
  `18 failed -> 19 failed` (the one new regression above), `1990 passed`,
  `3 skipped`, `1 error`, `1079.34s`.
- **Full suite, final run, after the regression fix**: `18 failed, 1991
  passed, 3 skipped, 1 error, 1172.48s`.
- **Reconciliation against Pass 3's baseline** (`1944 passed, 18 failed, 3
  skipped, 1 error`): `1944 + 47 (new tests, all passing) = 1991` passed;
  `18` failed, unchanged. The arithmetic is exact in both directions -- the
  regression's extra failure and its fix's extra pass cancel out to the same
  net change the new tests alone would have produced.
- **New regressions in the final state**: none. The `18` failures are the
  exact same pre-existing dirty-worktree failures recorded throughout Passes
  1-3 (12 `tests/test_page_agent.py` behavior failures, 6
  `tests/test_failures_catalogue.py` reference failures) -- unaffected by
  this pass, per the owner's standing instruction that reconciling the dirty
  `tests/test_page_agent.py` file is out of scope for every pass to date.

## Invariants preserved

- Irreversible action authority remains centralized in
  `JobApplicationAssistant.click_verified_submit()` /
  `SubmissionGuardV0.submit_verified()` / `JobTracker.begin_submission_dispatch()`;
  no site adapter gained independent submit authority, and none of this
  pass's changes touch `sites/`.
- No existing safety check was weakened to make a test pass: every fix in
  this pass either closes a gap (Findings 1-5) or narrows a detector's false
  positives in the fail-closed direction (Finding 6 -- under-confirming
  remains the fallback, not over-confirming).
- `safety.py` was changed only with the owner's explicit, itemized approval
  given in the Pass 4 authorization message, per the project's standing
  rule that `safety.py` changes require that approval on the record.
- A job/application cannot be dispatched or submitted twice merely because
  it is reachable through another alias, URL variant, tab, popup, login
  host, or UI wording -- this was the exact property Finding 2 broke and is
  now covered by dedicated tests at multiple chain depths and in both
  annexation directions.

## P0-B2 started

NO.
