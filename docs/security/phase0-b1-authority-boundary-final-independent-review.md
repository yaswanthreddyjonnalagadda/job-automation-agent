# Phase 0-B1 — Authority-Boundary Follow-up: Final Independent Review

**Base:** `codex/architecture-reliability`
**Review branch:** `fix/submission-replay-and-containment-hardening`
**Follow-up commit reviewed:** `756b77b`

This review re-derives its own conclusions from the committed diff and
fresh executions. It does not accept the follow-up documentation's
"Finding 6: CLOSED" / "Finding 10: CLOSED" claims as proof. **Both claims
are found to be overstated**, and the reasons are documented in full
below, with a concrete, reproduced counter-example. No production code
was modified during this review. The PR was not merged. P0-B2 was not
started.

---

## 1. Reconstruct the original Finding 6

**VERIFIED** as an accurate description of the pre-follow-up code.

Read `page_agent.py` at the parent of `756b77b` (i.e., before this
follow-up): `_press_next_locked()`'s `elif submit_word:` branch calls
`submit_gate(page, controls, last_step=False)`, whose entire body (for
`last_step=False`) checks CAPTCHA visibility, sponsorship/legal
conflicts, and pending attestations -- and returns `""` (permitting the
click) if none of those fire. Nothing in that function, before this
follow-up, re-examined whether `last_step=False` was actually correct.
That premise rests entirely on `steps_remain`, derived from `plan.step`
-- a field the planner/AI reports, never read from the page's own DOM by
this code. The reconstruction in the follow-up's own documentation
matches the code exactly.

---

## 2. Review the new authority check

**PARTIALLY VERIFIED.** The mechanism itself is implemented correctly and
is genuinely independent of `plan.step` and of the submit button's own
label -- but it is also **narrower in scope than its own name and
documentation claim**, in a way that matters.

- `is_review_step_by_wizard_marker()` queries only
  `[aria-current='step']:has-text('Review')`,
  `[data-automation-id='progressBarActiveStep']:has-text('Review')`, and
  `[class*='active']:has-text('Review')` -- confirmed by reading the
  committed diff directly. It does not reference `plan.step`, `steps_remain`,
  or any button text at all. It is a genuine DOM-structural signal, not a
  repackaging of the AI-reported field.
- Confirmed narrower than `is_review_step()` in the intended way: a page
  with the wizard marker and no submit-labeled button returns `True`; the
  same page with a submit-labeled button but no wizard marker (Schwab's
  actual shape) returns `False` from the new method while
  `is_review_step()` itself still returns `True` -- reproduced directly in
  this review, matching the follow-up's own third test.
- The literal-"Submit"-shortcut exclusion is real and correctly reasoned:
  `is_review_step()`'s first check (`button:text-is('Submit')`) is what
  makes it fire on Schwab's own case; the new method only inspects the
  second, DOM-structural half, so it does not inherit that over-eagerness.
- The result is consulted before any click: `wizard_marks_review` feeds
  directly into `final`'s computation, which gates the subsequent click in
  the same function, before `self.locate(page, control.ref).click(...)`
  executes. No browser JavaScript is involved in that decision.
- **Where this section's verification turns up the major finding**: the
  instruction to "examine what happens when the wizard evidence is
  absent... Determine whether uncertainty can ever restore browser-only
  authority. Do not assume 'no marker found' is safe without tracing the
  actual resulting control flow" was followed through to a concrete
  reproduction, not just reasoning. When no wizard marker is present at
  all -- confirmed to be the common case for ATS systems that are not
  Workday-style wizards (this project's own site adapters explicitly
  target Greenhouse, Lever, Ashby, SuccessFactors, and "similar portals"
  per `CLAUDE.md`, most of which do not use this exact progress-bar
  markup) -- `wizard_marks_review` is unconditionally `False`, the new
  disjunct contributes nothing, and `final`'s value is *identical* to
  what it was before this follow-up. See Section 3 for the reproduced
  consequence.

---

## 3. Prove or disprove Python authority with browser containment removed

**DEFECT FOUND.** Browser-only authority is reproducibly still reachable
through the production entry point, for the general case this follow-up's
own documentation claimed to close.

Constructed independently (not reusing the follow-up's own test fixtures):
a real Playwright page with a form whose `onsubmit` sets
`window.submitted = true` (an observable, irreversible-equivalent side
effect that would prove a real submission occurred), a `PagePlan` with
`step="2 of 5"` and `next_kind="next_step"` (Python is told, falsely, that
more steps remain), and **no wizard marker anywhere in the DOM** -- i.e.,
exactly the shape of a genuinely final submit control on an ATS without
Workday-style wizard chrome, with its step count simply wrong. The test
assistant's `protect_submission` was shadowed to a no-op, so no
`SubmissionGuardV0` was ever constructed; browser-side containment was
completely absent, not merely bypassed.

```
outcome: moved -
window.submitted: True
```

`PageAgent.press_next()` -- the real production entry point, not a
reimplementation -- clicked the control. The irreversible side effect
occurred. The result did not depend on any browser-side guard, denial
counter, or interception, because none was present to depend on. This is
the precise scenario Finding 6 described, reproduced with browser
containment completely removed, and it still passes through Python
unchecked.

**This directly means**: the follow-up's authority rule only applies when
a Workday-style wizard marker happens to be present and happens to
contradict the step count. For any ATS shape without that marker -- which
this review assesses as the majority of real-world cases this project
targets -- the original Finding 6 gap is **fully open**, identical to the
pre-follow-up code, because the new disjunct evaluates to `False` and
contributes nothing to the decision.

---

## 4. Verify the legitimate intermediate-submit case

**VERIFIED**, reproduced independently.

Constructed a page identical in shape to the Schwab case: a plain form
with a "Submit" button, `onsubmit` setting `window.submitted = true`, no
wizard-progress markup of any kind, with the same `plan.step="2 of 5"`,
`next_kind="next_step"`. `press_next()` returned `"moved"` and
`window.submitted` was `True` -- the legitimate flow is unaffected, and no
true-final protection was weakened to achieve that (the hard
`last_step=True` block, unchanged by this diff, still governs the actual
final step whenever Python's own classification reaches it).

---

## 5. Bypass variants

| Variant | Result | Classification |
|---|---|---|
| Wizard marker present, contradicts step count (the follow-up's own scenario) | Refused, stops for owner | VERIFIED |
| **No wizard marker at all, step count wrong, control genuinely final** | **Clicked through; irreversible side effect occurred** | **DEFECT** (Section 3) |
| Hidden/stale wizard marker (`display:none`, left over from a prior SPA state) | `is_review_step_by_wizard_marker()` still returns `True` (Playwright's `.count()` does not filter by visibility) -- over-blocks, i.e. fails in the *safe* direction | RESIDUAL RISK (availability only, LOW) |
| Duplicated markers, one stale+hidden, one real | Same as above -- any match is enough, still over-blocks | RESIDUAL RISK (availability only, LOW) |
| Current-step/total-step mismatch with no marker present | Falls into the "no marker" case above -- same DEFECT | DEFECT (same root cause as Section 3) |
| Submit-like control with an unusual/unrecognized label | Outside this follow-up's scope: governed by Finding 1's structural default-deny rule in the browser guard (unchanged by this commit), not by `submit_gate`. Not re-verified here since `756b77b` does not touch that code. | OUT OF SCOPE for this follow-up |
| Form-associated button whose visible text looks non-final but which submits | Same as the general "no marker" case if `submit_word` is false entirely (falls to the plain `else:` branch in `_press_next_locked`, which predates this entire engagement and was never in scope for Finding 6, which was specifically about the `submit_word`-true branch) | OUT OF SCOPE for this follow-up, but noted: this is an *even broader* pre-existing gap than Finding 6 itself, since in that branch there is no `submit_gate` call of any kind |

No variant was found where the new check caused an *unsafe* outcome (a
click that should have been blocked and was not, beyond the one already
counted in Section 3). Every other variant either correctly refuses or
over-blocks (safe direction).

---

## 6. Verify Finding 10 closure

**NOT CLOSED**, for the same reason as Finding 6.

Checked each stated condition:

- `protect_submission` is genuinely a no-op in the new tests (confirmed
  by reading `tests/test_authority_boundary.py`'s `make_agent()`:
  `assistant.protect_submission = lambda tab: None`) -- VERIFIED.
- No guard remains active: since `protect_submission` never runs, no
  `SubmissionGuardV0` is ever constructed for these tests, confirmed by
  reading the code path and by this review's own independent
  reproduction in Section 3 using the identical pattern. VERIFIED.
- The tests call `press_next()`, the real production entry point, not an
  isolated helper. VERIFIED.
- The final-submit side effect would really occur on an erroneous click:
  confirmed by the forms' own `onsubmit` handlers setting a
  `window`-level flag, and Section 3's reproduction shows this flag does
  in fact get set when the click is wrongly allowed. VERIFIED.
- A complementary legitimate-intermediate test exists and passes.
  VERIFIED.

So the three conditions the review was asked to check off are all
individually true -- **but the test gap the three tests were meant to
close was broader than what they test.** Finding 10, as stated in the
prior review, was: *"No full `PageAgent` decision-path test for an
intermediate-step misclassification where the browser guard is the only
remaining protection."* The new tests cover exactly one shape of that
misclassification (a wizard marker present and contradicted) and its
positive complement (no marker, counter accurate) -- but not the shape
this review reproduced in Section 3 (no marker, counter wrong, genuinely
final), which is the more general and, this review assesses, more common
real-world instance of the exact scenario Finding 10 was written to
cover. No test in the suite would fail if the Section 3 regression were
reintroduced, because no test exercises that input.

---

## 7. Regression review

**VERIFIED.** `git show 756b77b --stat` confirms exactly three files:
`browser_automation.py`, `page_agent.py`, and the new
`tests/test_authority_boundary.py`. Grepped every call site of
`is_review_step()`, `is_review_step_by_wizard_marker()`, `submit_gate()`,
and `press_next()`/`_press_next_locked()` across the repository: the only
behavioral change is the one new disjunct in `final`'s computation;
`is_review_step()`'s own return value is provably unchanged (it now calls
the new method internally instead of inlining the same selector, but the
selector and logic are identical). Every other caller
(`tests/test_phase3_features.py`, `tests/test_finding_the_way.py`,
`tests/test_hidden_choices_and_breaker_reset.py`,
`tests/test_submission_firewall.py`, `apply_flow.py`'s three call sites)
was re-run fresh in this review (see Section 8) and passes.

---

## 8. Validation

All four runs below were executed fresh during this review, against
commit `756b77b`, not reused from the implementation or prior review's
own reported numbers.

**A. Original targeted P0-B1 suite** --
`python -m pytest -q tests/test_submission_effect_state.py tests/test_submission_firewall.py tests/test_auto_submit.py tests/test_safety_rules.py tests/test_repeated_entries.py`:
**`198 passed`** in 579.52s. Matches the expected reference exactly.

**B+C. New authority-boundary tests plus directly affected PageAgent/
review-step tests** --
`python -m pytest -q tests/test_authority_boundary.py tests/test_phase3_features.py tests/test_finding_the_way.py`:
**`67 passed`** in 279.27s. Separately,
`python -m pytest -q tests/test_hidden_choices_and_breaker_reset.py`
(another `press_next()` caller found by this review's own grep, not
listed in the follow-up documentation): **`18 passed`** in 108.08s.

**D. Full suite** -- cache cleared first
(`rm .pytest_cache/v/cache/lastfailed`) so the result could not contain
entries accumulated from any earlier session, then
`python -m pytest -q -rs -n auto`: **`18 failed, 2010 passed, 3 skipped,
1 error`** in 916.88s. Matches the implementation's reported counts
exactly.

**Exact node-ID comparison, not counts alone**: read the freshly-populated
`lastfailed` cache after this run and compared all 18 failing node IDs
plus the 1 collection error, by exact name, against the baseline recorded
in every prior pass and review of this engagement. All 19 entries match
exactly -- the same 12 `tests/test_page_agent.py` behavior failures, the
same 6 `tests/test_failures_catalogue.py` parametrized references, and the
same `tests/test_ant_select.py` collection error. Zero new failures, zero
disappeared failures, zero changed failure identities attributable to
`756b77b`.

---

## 9. Re-check preserved P0-B1 guarantees

**VERIFIED, by diff absence rather than full re-testing**, per the
review's own instruction that this is sufficient when the new commit does
not touch the relevant files. `git show 756b77b --stat` confirms none of
`job_tracker.py`, `apply_flow.py`, `submission_guard.py`, `jd_analyzer.py`,
`db.py`, or `safety.py` appear in the diff. Durable replay protection,
canonical identity/alias handling, `UNCERTAIN` replay refusal, fail-closed
missing/corrupt durable state, `BEGIN IMMEDIATE` dispatch atomicity,
browser structural default-deny, listener-liveness repair, missing-guard
restoration, and verified-submit gateway authority are therefore
structurally unaffected by this commit. The full-suite run in Section 8
(198 of the original targeted tests, all passing) is consistent with this.

---

## Summary of findings

| # | Finding | Classification | Severity |
|---|---|---|---|
| 1 | Original Finding 6 description matches pre-follow-up code | VERIFIED | -- |
| 2 | New check is genuinely DOM-structural, independent of `plan.step` and button label, narrower than `is_review_step()` as intended | VERIFIED | -- |
| 3 | **A genuinely final control, with no wizard marker present, whose step count is simply wrong, is still clicked by Python alone with zero browser-side containment present** | **DEFECT** | **HIGH** |
| 4 | Legitimate Schwab-style intermediate submit still proceeds, true-final hard block unweakened | VERIFIED | -- |
| 5 | Hidden/stale/duplicated wizard markers over-block (safe direction); the no-marker case is the same defect as #3; unrecognized-label and non-`submit_word` cases are out of this follow-up's scope | RESIDUAL RISK (LOW) / DEFECT (same as #3) / OUT OF SCOPE | mixed |
| 6 | Finding 6 closure claim | **NOT CLOSED** (narrow sub-case only) | -- |
| 7 | Regression scope: exactly 3 files, no unintended behavior change elsewhere | VERIFIED | -- |
| 8 | Fresh validation matches reported counts, confirmed by exact node-ID comparison | VERIFIED | -- |
| 9 | Finding 10 closure claim | **NOT CLOSED** (the broader, more common misclassification shape has no test, and would not have caught #3) | -- |
| 10 | Other P0-B1 guarantees unaffected (file-level, confirmed) | VERIFIED | -- |

---

## Finding 6: **NOT CLOSED**

The remaining browser-only-authority path: any genuinely final submit
control, reached while Python's planner/AI reports an incorrect step
count (`plan.step` claiming more steps remain), on a page that does not
carry a Workday-style active-wizard-progress DOM marker. On such a page,
`wizard_marks_review` is always `False`, `final`'s computation is
identical to the pre-follow-up formula, and `submit_gate(last_step=False)`
performs only CAPTCHA/legal/attestation checks before permitting the
click -- with no browser-side containment required to reach this outcome,
as reproduced in Section 3 with the guard entirely absent.

## Finding 10: **NOT CLOSED**

The three new tests prove the narrow fix works and do not regress the
legitimate case, but no test exercises the broader, more common
misclassification shape (no wizard marker, wrong step count, genuinely
final control) that Section 3 reproduced. That scenario would pass
through the current test suite undetected.

---

## Recommendation

**NOT READY FOR P0-B1 MERGE**

Per the review's own stated criteria, a READY recommendation requires
"no browser-only authority remains in the reproduced misclassification
path" -- Section 3 reproduces exactly such a path, with browser
containment completely removed, and shows the irreversible side effect
occurring. This is reported as a DEFECT, not a residual risk, because it
contradicts the follow-up documentation's explicit closure claim for the
finding this entire follow-up was scoped to close. Per this review's
instructions, no fix was attempted; this stops after documenting it.
