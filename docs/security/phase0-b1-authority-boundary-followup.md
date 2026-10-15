# Phase 0-B1 — Authority-Boundary Follow-up (Finding 6)

**Scope:** one narrowly scoped fix for Finding 6 from
`docs/security/phase0-b1-final-independent-review.md`. No other finding
from that review was addressed. No broad refactor was made. Not merged.
P0-B2 not started.

**Commit:** `756b77b` on `fix/submission-replay-and-containment-hardening`
(pushed; branch not merged).

---

## Root cause

`PageAgent._press_next_locked()` classifies a submit-labeled control into
one of three outcomes: the true final step (`final=True`, which
`submit_gate(last_step=True)` unconditionally refuses to click itself, by
design, confirmed unchanged by this fix); an intermediate submit
(`submit_word=True`, `final=False`); or ordinary navigation. The
intermediate-submit branch calls `submit_gate(page, controls,
last_step=False)`, which checks CAPTCHA visibility, sponsorship/legal
conflicts, and pending attestations -- but never re-verifies the premise
that got it into that branch in the first place: that this is genuinely
*not* the last step.

That premise rests entirely on `steps_remain`, computed by regex-matching
`plan.step` -- a field the AI/planner itself reports, not something this
code reads from the page's own DOM. If that field is wrong (a misread or
hallucinated "step N of M", or an unrelated number the same pattern
happened to match), Python had no independent way to notice. The only
thing then standing between that misclassification and a real,
uncontained click was the browser-side `SubmissionGuardV0` -- which this
PR's own Finding 1 fix already narrows but does not claim is a universal
semantic firewall. This violated the stated invariant: browser-side
containment must be defense-in-depth only, never the sole authority.

## Exact authority rule now enforced

> A submit-labeled control whose AI-reported step count claims more steps
> follow is not clicked by `PageAgent` if the page's own DOM-structural
> wizard-progress chrome independently says this *is* the active review
> step. That contradiction is treated as the final step -- Python stops
> for owner review -- rather than trusted on the step count's word alone.

Concretely: `_press_next_locked`'s `final` computation gained one more
disjunct, `submit_word and wizard_marks_review`, where `wizard_marks_review`
comes from a new, narrower check,
`JobApplicationAssistant.is_review_step_by_wizard_marker()`, that only
tests the DOM-structural half of the existing `is_review_step()`
(`[aria-current='step']:has-text('Review')`,
`[data-automation-id='progressBarActiveStep']:has-text('Review')`, and
`[class*='active']:has-text('Review')`) -- deliberately *not* also firing
on a literal "Submit"-labeled button the way `is_review_step()` itself
does. That distinction matters: `is_review_step()`'s button-text shortcut
already fires on Schwab's own step 2 of 5 (its button reads exactly
"Submit"), which is precisely why the pre-existing code already has to
let the step counter override `is_review_step()` for that case. Reusing
`is_review_step()` itself as the new cross-check would have broken that
legitimate flow immediately; the new, narrower check does not have that
problem because it never looks at the button's own label at all.

## Code changed

- `browser_automation.py`: `JobApplicationAssistant.is_review_step()` now
  delegates its second (non-button-text) check to a new sibling method,
  `is_review_step_by_wizard_marker()`, which contains exactly that
  check and nothing else. `is_review_step()`'s own observable behavior
  for every existing caller is unchanged -- confirmed by re-running every
  test that exercises it (see Validation).
- `page_agent.py`: `_press_next_locked()` computes `wizard_marks_review`
  alongside the existing `is_review`/`steps_remain` and adds it as one
  more `or` term in `final`'s computation. No other line in this function
  changed.

## Tests added (`tests/test_authority_boundary.py`, new file)

All three call `PageAgent.press_next()` directly -- the real, production
decision path -- against a real Playwright page, with the test's
assistant's `protect_submission()` shadowed to a no-op so no
`SubmissionGuardV0` is ever constructed or attached. A pass therefore
proves Python's own refusal; it is not propped up by browser-side
containment at all.

- `test_a_step_counter_contradicted_by_the_pages_own_wizard_chrome_is_refused`
  -- the exact Finding 6 scenario: a page whose own wizard chrome
  (`[data-automation-id='progressBarActiveStep']` reading "Review") says
  this is the review step, while the (AI-reported) `plan.step` claims
  "2 of 5". Asserts the outcome is `"stop"` and the form's own
  `onsubmit` handler never fired (`window.submitted is None`). Before
  this fix, tracing the unmodified `final` formula against this exact
  page/plan shows it evaluates to `False`, falling through to
  `submit_gate(last_step=False)`, which has no check that would refuse
  it -- this is not asserted via a reverted build, but the formula's own
  arithmetic for this case is unambiguous and recorded here for the
  record.
- `test_an_ordinary_intermediate_submit_with_no_contradiction_still_proceeds`
  -- the complementary, required case: the identical plan/step count
  against a plain form with no wizard-progress chrome at all (Schwab's
  actual shape). Asserts the outcome is `"moved"` and the click did
  proceed (`window.submitted is True`).
- `test_the_wizard_marker_check_itself_is_independent_of_a_submit_labeled_button`
  -- confirms `is_review_step_by_wizard_marker()` is not a disguised
  restatement of `is_review_step()`'s own button-text shortcut: `True` on
  a page with wizard chrome and no submit button at all, `False` on
  Schwab's actual shape (submit button present, no wizard chrome) where
  the broader `is_review_step()` is separately confirmed still `True`.

## Validation

- `tests/test_authority_boundary.py` alone: `3 passed`.
- The existing 198 targeted P0-B1 tests
  (`tests/test_submission_effect_state.py tests/test_submission_firewall.py
  tests/test_auto_submit.py tests/test_safety_rules.py
  tests/test_repeated_entries.py`), re-run against this commit:
  **`198 passed`** -- unchanged from the independent review's own
  confirmed count.
- Every other caller of the now-partially-delegated `is_review_step()`
  (`tests/test_phase3_features.py`, `tests/test_finding_the_way.py`),
  plus the new authority-boundary tests and the 198 above, run together:
  **`265 passed`**.
- Full repository suite
  (`python -m pytest -q -rs -n auto`, cache cleared first for a reliable
  node-ID comparison): **`18 failed, 2010 passed, 3 skipped, 1 error`** in
  799.85s.
- Compared against the prior independently-verified baseline
  (`18 failed, 2006 passed, 3 skipped, 1 error`): passed count increased
  by 4 (the 3 new authority-boundary tests plus one incidental addition
  elsewhere in the collected set); failed, skipped, and error counts are
  unchanged. Verified by exact failing-node-ID comparison, not arithmetic
  alone: the freshly-populated `lastfailed` cache (cleared immediately
  before this run) lists the identical 18 failing node IDs and the
  identical 1 collection error (`tests/test_ant_select.py`) recorded as
  the pre-existing baseline throughout every prior pass and review of
  this engagement -- no entry disappeared, no new entry appeared.

## Finding disposition

- **Finding 6 (Python/browser authority boundary for the intermediate-
  submit misclassification case): CLOSED.** A submit-labeled control that
  Python's step-counter heuristic alone would have treated as
  intermediate, but that the page's own DOM-structural wizard-progress
  chrome identifies as the review step, is now refused by Python itself,
  independent of whatever the browser-side guard would or would not have
  caught. Verified with the guard entirely absent from the test.
- **Finding 10 (test gap: no test of this exact misclassification path
  through the full `PageAgent` decision logic): CLOSED.** Covered by
  `test_a_step_counter_contradicted_by_the_pages_own_wizard_chrome_is_refused`,
  which drives the real `press_next()` entry point with no guard
  attached.

Unrelated residual risks from the independent review (items 3, 8, 11; the
one remaining open test gap, item 9) were intentionally left untouched,
per scope.

## P0-B2 started

NO.

## Stopping point

Not merged. Not begun P0-B2. Commit `756b77b` is pushed to
`fix/submission-replay-and-containment-hardening` for a final independent
review.
