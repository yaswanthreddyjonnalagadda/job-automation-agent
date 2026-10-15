# Phase 0-B1 — General Authority-Boundary Follow-up

**Branch:** `fix/submission-replay-and-containment-hardening`
**Base:** `codex/architecture-reliability`
**Prior commit under repair:** `756b77b` (the narrow, Workday-only authority-boundary fix)

This follow-up closes the gap that
`docs/security/phase0-b1-authority-boundary-final-independent-review.md` found in
`756b77b`: Python trusted an AI-reported step counter (`plan.step`) as sufficient
evidence that a submit-labeled control was not the application's last step,
whenever no Workday-style wizard marker contradicted it. For any ATS shape
without that marker -- the common case for Greenhouse, Lever, Ashby,
SuccessFactors, and similar portals -- an incorrect step count had nothing else
standing between it and a real, uncontained submit; the browser-side guard was
the only remaining protection, and the independent review reproduced a click
going through with that guard completely absent.

## Reproduced defect (why `756b77b` was insufficient)

`756b77b` added one disjunct to `_press_next_locked()`'s `final` computation:
`submit_word and wizard_marks_review`, where `wizard_marks_review` came from
`is_review_step_by_wizard_marker()` -- true only when an active Workday-style
wizard-progress element's own text contained the word "Review". On a page
carrying no such element at all, `wizard_marks_review` was unconditionally
`False`, and the rest of the formula was byte-for-byte identical to the
pre-`756b77b` code: `submit_word and not (steps_remain and plan.next_kind ==
"next_step")`, where `steps_remain` came only from parsing `plan.step`.

The final independent review constructed a page with a genuinely final submit
control (a real `onsubmit` side effect), no wizard marker of any kind, and
`plan.step = "2 of 5"` (false), with `protect_submission` shadowed to a no-op so
no browser-side guard was ever constructed. Calling the real
`PageAgent.press_next()` produced `outcome: moved`, `window.submitted: True` --
the click went through with zero containment anywhere in the path.

## Previous trust model

```
submit-labeled control, plan.next_kind != "final_submit"
  -> is there a Workday wizard marker that itself says "Review"?
       yes -> treat as final (refuse)
       no  -> does the AI-reported step counter say more steps remain?
                yes -> treat as non-final (click)
                no  -> treat as final (refuse)
```

The AI-reported step counter was the deciding vote whenever no wizard marker
existed -- which is most of the time, for most ATS shapes this project
targets.

## New authority model

A submit-labeled control's finality is now a tri-state, read entirely from the
page's own DOM structure and never from `plan.step` or the control's own
label:

```
submit-labeled control, plan.next_kind != "final_submit"
  -> submission_step_finality(tab):
       "FINAL"      -> treat as final (refuse)
       "NON_FINAL"  -> treat as non-final (click)
       "UNKNOWN"    -> treat as final (refuse)   <-- fail closed, not "proceed"
```

`plan.step` is no longer consulted anywhere in this decision. It continues to
feed only the pre-existing, unrelated `is_review and not steps_remain` clause
(the dampener on `is_review_step()`'s own broader "a Submit button exists
somewhere on the page" signal) -- a clause this follow-up did not touch, and
which can only make the overall check *more* conservative (it can force an
additional refusal; it cannot authorize a click the new tri-state would
otherwise refuse).

### Definition of positive `NON_FINAL` evidence

`submission_step_finality()` (new, `browser_automation.py`) reads two
independently verifiable DOM structures, in one atomic `page.evaluate()` call:

1. **Workday's own active-step marker**
   (`[data-automation-id='progressBarActiveStep']`) or the general ARIA
   current-step pattern (`[aria-current='step']`). Confirmed against a real
   recorded Workday page
   (`output/AIG_Technologies,_Inc._Senior_Network_Data_Center_Engineer/evidence_20261005_133527_step8/page.html`),
   this element's own text is not just the step's display name -- it carries a
   plain, numeric phrase: `"current step 6 of 6"`, `"completed step 5 of 6"`,
   etc. That phrase is parsed directly (`/\bstep\s+(\d+)\s+of\s+(\d+)\b/i`):
   `current < total` is `NON_FINAL`; `current >= total` is `FINAL`. This
   generalizes `756b77b`'s literal `"Review"`-text match to any final step
   name (`"Summary"`, `"Confirm Details"`, etc.), in the safe direction only
   (it can only add more `FINAL` detections, never fewer). If exactly one such
   marker is present but its text does not parse as `"step N of M"`, the
   literal word "Review" is still checked as a fallback, preserving `756b77b`'s
   original signal unchanged. More than one such marker on the page (a
   duplicated or conflicting wizard marker) is treated as ambiguous and
   contributes no evidence either way.
2. **The standards-based ARIA progressbar pattern**
   (`[role="progressbar"]` with numeric `aria-valuenow`/`aria-valuemax`),
   restricted to the `NON_FINAL` direction only. This covers non-Workday
   steppers without guessing at framework-specific markup. To avoid mistaking
   an unrelated upload/loading progress bar for wizard chrome, it additionally
   requires the bar's own accessible name (`aria-label`, or the text of the
   elements its `aria-labelledby` points at) to contain the word "step", and
   requires exactly one such bar to be present. It is deliberately never used
   to prove `FINAL` -- reaching 100% on a progress bar is not, on its own,
   proof that the application itself is done.

Both signals additionally require the element to be visible
(`getClientRects().length > 0`) before granting `NON_FINAL` -- a hidden/stale
leftover node from a prior SPA state claiming more steps remain is not trusted
as permission, though a hidden/stale node claiming "Review" (or `current >=
total`) is still honored for `FINAL` (over-blocking is the safe direction).

Absence of any recognized structure, a malformed value (`aria-valuenow` that
is not a number), an internally inconsistent one (`now >= max` on the
progressbar path, which grants neither `FINAL` nor `NON_FINAL`), or more than
one conflicting marker all resolve to `"UNKNOWN"` -- and `"UNKNOWN"` is refused
exactly like `"FINAL"`.

## Code changed

- `browser_automation.py`: added `submission_step_finality()` and its backing
  `_STEP_FINALITY_SCRIPT`. `is_review_step_by_wizard_marker()` and
  `is_review_step()` are unchanged (left callable for any other caller;
  `is_review_step()` still composes the same way it always did); nothing in
  this change depends on them any more for the decision that matters.
- `page_agent.py`: `_press_next_locked()`'s `final` computation no longer
  derives any part of the submit-labeled-control decision from `steps_remain`
  or `wizard_marks_review`. It now consults `submission_step_finality()`
  (via `getattr(..., lambda p: "UNKNOWN")`, defaulting to the fail-closed
  state if the assistant does not implement it at all) and refuses unless the
  result is exactly `"NON_FINAL"`.

No other file was touched. `submit_gate()`, `safety.py`, `job_tracker.py`,
`apply_flow.py`, `submission_guard.py`, `jd_analyzer.py`, and `db.py` are
untouched by this commit.

## Compatibility implications for unsupported ATS DOM shapes

An ATS whose intermediate "Submit"-labeled steps carry no recognized
structural progress signal will now stop for owner review on those steps,
where before (both pre-`756b77b` and under `756b77b`) it would proceed
automatically on the strength of the AI-reported step counter alone. This is
an intended, accepted trade-off per the task's own invariant: it is acceptable
for this fix to produce additional owner-review stops on unsupported DOM
shapes; it is not acceptable for safety to depend on the browser guard.
Support for a specific ATS's own intermediate-submit shape can be extended
later by teaching `submission_step_finality()` a new, verified, positive
`NON_FINAL` signal for that shape -- never by reintroducing `plan.step` as
authority.

## Tests added

All in `tests/test_authority_boundary.py`, calling the real
`PageAgent.press_next()` with `protect_submission` shadowed to a no-op (no
browser-side guard constructed at all), per the task's requirement that these
run with browser containment completely absent:

- `test_a_step_counter_contradicted_by_the_pages_own_wizard_chrome_is_refused`
  -- unchanged, `756b77b`'s own case (Section 6.C).
- `test_a_genuinely_final_control_with_no_marker_and_a_wrong_step_count_is_refused`
  -- the exact reproduction from the final independent review (Section 6.A).
  Fails against `756b77b` (returns `"moved"` there).
- `test_misleading_step_metadata_with_no_independent_proof_fails_closed` --
  `plan.step = "1 of 99"`, no DOM evidence (Section 6.E).
- `test_an_unsupported_ats_progress_shape_resolves_unknown_not_non_final` --
  an ad hoc `<div class="step-indicator">Step 2 of 5</div>` that matches
  neither recognized structure (Section 6.B).
- `test_a_non_workday_stepper_with_real_aria_progress_evidence_still_proceeds`
  -- the legitimate-flow case, generalized beyond Workday via the ARIA
  progressbar pattern (Section 6.D).
- `test_duplicate_conflicting_markers_resolve_unknown`,
  `test_a_malformed_progressbar_value_resolves_unknown`,
  `test_an_inconsistent_progressbar_value_resolves_unknown_not_final`,
  `test_a_hidden_stale_marker_claiming_steps_remain_is_not_trusted` --
  garbled/ambiguous/hidden progress data (Section 6.F).
- `test_browser_containment_is_not_what_refused_the_click` -- Section 7's
  explicit proof: the fixture's `onsubmit` handler is shown to really fire on
  a direct click (no `PageAgent` involved), and a separate `press_next()` call
  on the same markup refuses before ever reaching that click.
- `test_the_wizard_marker_check_itself_is_independent_of_a_submit_labeled_button`
  -- unchanged except for a fixture rename, still verifies
  `is_review_step_by_wizard_marker()` directly.

One existing test was updated, not weakened:
`tests/test_submission_firewall.py::test_page_agent_plan_does_not_authorize_submit[next_step-click]`
asserted that the real, armed `SubmissionGuardV0` was what denied the click
for a plain "Submit Application" button with no DOM evidence and
`next_kind="next_step"`. That assumption was the gap this follow-up closes:
Python itself now refuses before ever attempting the click, so no denial is
recorded by the browser guard at all. The parametrize's expected value for
that case was changed from `"click"` to `None`, with a comment explaining why
-- the new behavior is strictly safer (refusal moved from the browser layer to
Python), not weaker.

## Validation

**A. Original P0-B1 targeted suite** --
`python -m pytest -q tests/test_submission_effect_state.py tests/test_submission_firewall.py tests/test_auto_submit.py tests/test_safety_rules.py tests/test_repeated_entries.py`:
first run surfaced the one test needing the update described above (`1 failed,
197 passed`); after updating it, re-run: **`198 passed`** in 187.09s --
matching the established reference count exactly.

**B. All authority-boundary tests** --
`python -m pytest -q tests/test_authority_boundary.py`: **`11 passed`** in
32.69s.

**C. PageAgent / review-step / navigation suites** --
`python -m pytest -q tests/test_phase3_features.py tests/test_finding_the_way.py tests/test_hidden_choices_and_breaker_reset.py`:
**`82 passed`** in 174.86s.

**D. Full suite** -- cache cleared first
(`rm .pytest_cache/v/cache/lastfailed`), then `python -m pytest -q -rs -n
auto`: **`18 failed, 2018 passed, 3 skipped, 1 error`** in 738.55s (0:12:18).

The reference baseline recorded before this follow-up was `18 failed, 2010
passed, 3 skipped, 1 error`. The `+8` passed is exactly the net new tests
added to `tests/test_authority_boundary.py` (11 now, 3 before). The 18
failing node IDs plus the 1 collection error were compared by exact name,
not by count, against that baseline (read from the freshly-repopulated
`.pytest_cache/v/cache/lastfailed`): all 19 entries match exactly -- the
same `tests/test_ant_select.py` collection error, the same 6
`tests/test_failures_catalogue.py` parametrized references, and the same 12
`tests/test_page_agent.py` behavior failures (these 12 are pre-existing and
unrelated to this change -- they trace to an already-present, uncommitted
local modification to `tests/test_page_agent.py` that predates this
follow-up and this engagement's own session). Zero new failures, zero
disappeared failures, zero changed failure identities attributable to this
commit.

## Finding 6: CLOSED

The general case -- any submit-labeled control reached while Python's own step
counter is wrong, on any ATS shape, with or without a wizard marker -- now
requires positive, independently-verified DOM evidence of non-finality before
Python will click it outside the authoritative final-submit gateway. Absence
of such evidence refuses the click exactly like a recognized final control.
The no-wizard-marker reproduction from the final independent review
(Section 3 of `phase0-b1-authority-boundary-final-independent-review.md`) was
re-run against this fix with browser containment entirely absent
(`test_a_genuinely_final_control_with_no_marker_and_a_wrong_step_count_is_refused`,
and again explicitly in `test_browser_containment_is_not_what_refused_the_click`)
and now refuses.

## Finding 10: CLOSED

`tests/test_authority_boundary.py` now covers: the original Workday-marker
contradiction, the general no-evidence case, extreme misleading metadata, an
unsupported ATS shape, the legitimate non-Workday-stepper case, four
ambiguous/garbled-evidence variants, and an explicit, dedicated proof that
browser-side containment is not what is doing the refusing. No test gap
remains for the scenario the final independent review used to demonstrate the
defect.
