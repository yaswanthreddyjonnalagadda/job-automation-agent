# Phase 0-B1 — Final Closure Review

**Repository:** `yaswanthreddyjonnalagadda/job-automation-agent`  
**Review branch:** `fix/submission-replay-and-containment-hardening`  
**Committed head reviewed:** `1d312cf1f59e470d2370ae17569a032df741e5e0`  
**Parent authority-boundary attempt:** `756b77b6986d717f1cbadd4920b4c79c34a860a2`

No production code was modified during this review. Nothing was merged. P0-B2 was not started.

## Repository-state note

The two documents named as review inputs,

- `docs/security/phase0-b1-general-authority-boundary-followup.md`
- `docs/security/phase0-b1-authority-boundary-final-independent-review.md`

were not present in the pushed branch tree at `1d312cf`. The committed code, commit diff/message, and committed tests were therefore treated as authoritative for this closure review. This mismatch should be corrected separately, but it is not the reason for the merge recommendation below.

## 1. Authority model

**VERIFIED, with a blocking defect in what qualifies as positive `NON_FINAL` evidence.**

The committed `PageAgent._press_next_locked()` policy is structurally tri-state for submit-labelled controls:

- `NON_FINAL` can enter the intermediate-submit path.
- `FINAL` fails closed through the normal final-step `submit_gate()`.
- `UNKNOWN` also fails closed, because `step_finality != "NON_FINAL"` makes the control final.

`submission_step_finality()` is page-derived and does not itself read `plan.step`. Missing/unsupported evidence returns `UNKNOWN`, and exceptions return `UNKNOWN`.

However, `plan.step` still computes `steps_remain` and suppresses the broader `is_review_step()` signal. That is acceptable only if the independent signal returning `NON_FINAL` is itself strong enough to prove that the submit-capable action is not the application's final action. The ARIA branch does not meet that standard.

## 2. Exact former no-wizard defect

**VERIFIED CLOSED for the exact former reproduction.**

The new committed test `test_a_genuinely_final_control_with_no_marker_and_a_wrong_step_count_is_refused` uses the real `PageAgent.press_next()` path, an actually submitting form, incorrect `plan.step` metadata, no recognized progress structure, and a `JobApplicationAssistant` whose `protect_submission` is explicitly shadowed to a no-op. With no recognized structure, `submission_step_finality()` resolves `UNKNOWN`, so Python stops before the click.

The companion `test_browser_containment_is_not_what_refused_the_click` first proves that a direct click really fires `window.submitted`, then clears the flag and verifies that the PageAgent path stops with no submission guard attached.

This closes the exact no-wizard reproduction that defeated commit `756b77b`.

## 3. Browser independence

**VERIFIED for the new authority-boundary tests.**

`make_agent()` constructs the real `JobApplicationAssistant` with `__new__`, shadows `protect_submission` to a no-op, and never constructs `SubmissionGuardV0`. The explicit proof test also asserts that the assistant has no `_submission_guard` attribute. The refusal therefore does not depend on guard listeners, denial counts, or interception.

## 4. Tri-state evidence handling

### Workday-style marker

**VERIFIED in the safe direction.**

A single recognized marker with parseable `step N of M` text returns:

- `FINAL` when `current >= total`;
- `NON_FINAL` only when `current < total` and the marker is visible.

Duplicated markers return `UNKNOWN`. A hidden marker that claims more steps remain does not return `NON_FINAL`. A marker containing `Review` returns `FINAL` even if hidden; that can over-block, but it cannot authorize submission.

### ARIA progressbar

**DEFECT — MEDIUM.**

The implementation treats a single visible element matching

`[role='progressbar'][aria-valuenow][aria-valuemax]`

as positive `NON_FINAL` evidence whenever:

- JavaScript `Number(aria-valuenow)` and `Number(aria-valuemax)` are finite,
- `max > 0`,
- `now < max`, and
- the accessible name merely contains the word `step`.

That is not sufficient proof that the progressbar represents the application's wizard/finality state.

### Independent reproduction 1 — irrelevant progressbar

The exact committed `_STEP_FINALITY_SCRIPT` was executed in Chromium against:

```html
<div role="progressbar"
     aria-valuenow="2"
     aria-valuemax="5"
     aria-label="Upload step 2 of 5"></div>
<form onsubmit="window.submitted=true; return false">
  <button type="submit">Submit</button>
</form>
```

Observed:

```text
finality = NON_FINAL
direct click => window.submitted == true
```

The progressbar describes an upload sub-process, not application navigation, yet it is accepted as positive proof that the final Submit control is intermediate.

With `plan.next_kind == "next_step"` and misleading `plan.step == "2 of 5"`:

1. `submit_word` is true.
2. `steps_remain` is true, suppressing the broader `is_review_step()` submit-button shortcut.
3. `submission_step_finality()` returns `NON_FINAL`.
4. `final` becomes false.
5. `submit_gate(..., last_step=False)` performs CAPTCHA/legal/attestation checks but does not block solely because the action may be final.
6. Python proceeds to the ordinary locator click.

If browser containment is absent/bypassed, the genuinely final action can therefore execute. This is the same authority class as Finding 6: browser containment can again become the sole remaining barrier.

### Independent reproduction 2 — malformed numeric value

The exact committed script was also executed with:

```html
<div role="progressbar"
     aria-valuenow=""
     aria-valuemax="5"
     aria-label="Step progress"></div>
```

Observed:

```text
finality = NON_FINAL
```

JavaScript `Number("")` is `0`, so an empty malformed `aria-valuenow` is treated as valid positive evidence. A negative value such as `aria-valuenow="-1"` likewise returns `NON_FINAL` because the implementation does not enforce the ARIA value range.

These cases contradict the intended rule that malformed/inconsistent evidence resolves to `UNKNOWN`.

## 5. Legitimate intermediate behavior

**VERIFIED by committed test coverage, but not sufficient to offset the defect above.**

`test_a_non_workday_stepper_with_real_aria_progress_evidence_still_proceeds` demonstrates that a visible `role="progressbar"` with `aria-valuenow="2"`, `aria-valuemax="5"`, and `aria-label="Step 2 of 5"` results in an allowed intermediate transition.

The compatibility goal is therefore represented. The problem is that the positive-evidence predicate is too broad, not that all intermediate submits are blocked.

## 6. Alternate authority-path inspection

**No separate direct final-submit gateway bypass was found in the committed follow-up diff.**

The follow-up changes only the finality helper, the PageAgent gate, authority-boundary tests, and the submission-firewall expectation.

The authoritative verified-send path remains:

`apply_flow.submit_verified()` → `assistant.click_verified_submit()` → `SubmissionGuardV0.submit_verified()` → durable `begin_submission_dispatch()` before the click.

The blocking bypass identified in this review remains inside the ordinary `PageAgent._press_next_locked()` intermediate-submit path: an incorrectly accepted `NON_FINAL` result can authorize the normal locator click without entering the verified final-submit gateway.

## 7. Conservatism of positive NON_FINAL evidence

**NOT VERIFIED.**

The Workday numeric marker is comparatively narrow and page-structure-specific.

The generic ARIA progressbar signal is not bound to the application wizard. Requiring only the word `step` in its name does not establish that the bar describes application steps rather than upload, onboarding, profile completion, document processing, or another nested workflow on the same page.

Additionally, numeric parsing is permissive enough to accept malformed empty and out-of-range values.

Therefore the current implementation still violates the closure invariant:

> Python must positively establish that the submit-capable action itself is non-final before clicking it outside the authoritative final-submit gateway.

## 8. Finding 10

**Finding 10: CLOSED**

The original test gap was the absence of a full `PageAgent.press_next()` reproduction of Finding 6 with browser containment removed.

The committed authority-boundary tests now:

- use the real `PageAgent.press_next()` entry point;
- disable `protect_submission` completely;
- include the exact former no-wizard failure;
- prove the underlying control really can submit;
- exercise misleading step metadata;
- exercise an unsupported ATS shape;
- exercise several malformed/ambiguous cases;
- preserve a legitimate intermediate flow.

That original test gap is closed.

A **new TEST GAP (MEDIUM)** exists: there is no adversarial test proving that an unrelated progressbar containing the word `step`, or malformed values such as empty/negative `aria-valuenow`, resolve to `UNKNOWN` through the full PageAgent path.

## 9. Regression scope

**VERIFIED by committed diff inspection.**

Commit `1d312cf` is narrowly scoped to:

- `submission_step_finality()`;
- the PageAgent finality decision;
- `tests/test_authority_boundary.py`;
- one changed submission-firewall expectation.

No durable replay, canonical identity, SQLite dispatch, verified gateway, listener-liveness, missing-guard restoration, or confirmation-state code is changed by this follow-up commit.

## 10. Recorded-page compatibility

**NOT INDEPENDENTLY RE-RUN in this review.**

The commit message reports 1,185 saved pages / 793 distinct pages with identical before/after interpretation. The recorded pages are not present in the pushed Git tree, so that claim could not be independently re-executed from the connected repository.

This is not the blocking reason for the recommendation.

## 11. Validation status

Because a merge-blocking authority defect was independently reproduced, this review stopped rather than spending further cycles re-running the full suite, consistent with the instruction to document the exact remaining bypass and stop if NOT READY.

Independently executed during this review:

- exact committed `_STEP_FINALITY_SCRIPT` in Chromium;
- irrelevant upload-step progressbar → `NON_FINAL`;
- empty `aria-valuenow` → `NON_FINAL`;
- negative `aria-valuenow` → `NON_FINAL`;
- direct final-submit button click in each fixture produced the observable submission side effect.

The repository has no GitHub Actions runs/status checks for head `1d312cf`, and the execution environment cannot clone GitHub directly, so the reported `198 / 11 / 82 / full-suite` counts were not independently re-run here after the blocker was found.

## Required conclusion

**Finding 6: NOT CLOSED**

The exact former no-wizard defect is closed, but the broader invariant is not: a misleading `plan.step` plus an unrelated or malformed ARIA progressbar can cause `submission_step_finality()` to return `NON_FINAL`, reopening the ordinary PageAgent click path for a genuinely final control. Browser containment can therefore still be the sole remaining protection.

**Finding 10: CLOSED**

The original full-production-path/no-browser-guard test gap is closed, although the newly discovered ARIA-evidence bypass needs its own regression tests.

**Required remediation before merge:** tighten positive `NON_FINAL` evidence so a generic progressbar is cryptographically—not merely linguistically—bound to the application wizard is not realistic; instead use a deterministic structural association (for example, known application-step container/relationship or adapter-specific proof) and strictly validate numeric ARIA semantics. If that association cannot be proven, resolve `UNKNOWN` and fail closed. Add full `PageAgent.press_next()` no-guard tests for an unrelated progressbar and malformed/empty/negative values.

NOT READY FOR P0-B1 MERGE
