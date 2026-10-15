# Phase 0-B1 — Comprehensive Final Closure Review

**Review branch:** `fix/submission-replay-and-containment-hardening`
**Reviewed commit:** `c32fedfa9ae12ccac1fa4305fd56668a2be3a694`
**Base:** `codex/architecture-reliability`

No production code or test file was modified during this review. This
document is the only file added. Nothing was merged. P0-B2 was not started.

This review audits the whole authority and submission-safety invariant in
one pass, rather than one more narrow example. It finds the specific,
long-tracked instance of Finding 6 (a recognized submit-labeled control
misclassified as intermediate via `plan.step`/DOM-marker evidence) correctly
and thoroughly closed — and finds one separate, pre-existing, architecturally
distinct path where browser containment is still the sole authority for a
genuinely final action. That path is reported as a DEFECT per this review's
own stated criteria, with full context on what it is, how long it has
existed, and why it was never touched by any of the five Finding-6
remediation commits.

---

## 1. The finality authority model

**VERIFIED**, traced through actual control flow, not comments.

`page_agent.py`'s `_press_next_locked()` (the only call site that decides
whether to click a submit-labeled control outside the verified gateway):

```python
step_finality = (
    getattr(self.assistant, "submission_step_finality", lambda p: "UNKNOWN")(tab)
    if submit_word else "UNKNOWN"
)
final = plan.next_kind == "final_submit" or (is_review and not steps_remain) \
    or (submit_word and step_finality != "NON_FINAL")
```

Confirmed effective semantics by direct substitution:
- `step_finality == "NON_FINAL"` → `(submit_word and False)` is `False` for
  that disjunct → the `elif submit_word:` branch runs
  `submit_gate(last_step=False)` (CAPTCHA/legal/attestation only) → the
  ordinary click proceeds if that passes.
- `step_finality == "FINAL"` → `(submit_word and True)` → `final = True` →
  `submit_gate(last_step=True)` → `"automatic submission is off..."` → the
  control is **never clicked** from this function.
- `step_finality == "UNKNOWN"` → identical to `"FINAL"` in this formula --
  `final = True`, the same hard stop, the same refusal to click. **UNKNOWN
  and FINAL are behaviorally identical here**: both are refused before any
  click is attempted.

`browser_automation.py`'s `submission_step_finality()`:
- Never reads `plan.step` -- confirmed by reading the whole function and its
  backing `_STEP_FINALITY_SCRIPT`; the only inputs are `page.evaluate()`'s
  return value against live DOM.
- Never reads the clicked control's own label -- confirmed the same way.
- The only path to `"NON_FINAL"` is a single, visible
  `[data-automation-id='progressBarActiveStep']` element whose own text
  parses as `current step N of M` with `N < M`.
- Any exception inside `submission_step_finality()` is caught and returns
  `"UNKNOWN"` (`except Exception: return "UNKNOWN"`).
- Any exception inside the JS itself cannot escape as a crash either --
  `page.evaluate()` would raise, caught by the same `try/except`.
- An unsupported ATS shape (no recognized marker) falls through every branch
  to the final `return "UNKNOWN"` at the end of the script.

**Confirmed empirically** (fresh execution during this review, not reused
from any prior round): `plan.step="1 of 99"` against a page with no DOM
marker still resolves `UNKNOWN`; button text (`"Submit"`, `"Continue"`,
unusual labels) has no code path into `submission_step_finality()` at all,
since the function takes only a `Page`, never a label string.

---

## 2. Every path capable of returning NON_FINAL-equivalent permission

Searched the whole repository for `submission_step_finality`, `NON_FINAL`,
`is_review_step`, `is_review_step_by_wizard_marker`, `steps_remain`,
`plan.step`, `last_step=False`, `submit_gate`, `next_step`, `final_submit`.

| Source | Evidence trusted | Page-derived | ATS-specific | Vetted against real markup | Fails closed on malformed/ambiguous |
|---|---|---|---|---|---|
| `browser_automation.submission_step_finality()` | `[data-automation-id='progressBarActiveStep']`, parsed numeric text | Yes | Yes (Workday) | Yes (see Section 3) | Yes |
| `page_agent._press_next_locked()`'s `submit_gate(last_step=False)` | CAPTCHA visibility, legal/sponsorship conflicts, pending attestations | Yes | No (generic safety checks) | N/A -- these are refusal conditions, not permission conditions; they never independently grant the click, they only additionally block it | Yes (any one triggers a stop) |
| `page_agent.current_step()` (snapshot-text based; feeds `plan.step`/`next_kind` for the profile-only planner branch, `page_agent.py:2131`) | `_STEP` regex over accessibility-snapshot text, with explicit `current`/`completed`/unmarked disambiguation | Yes, but from the accessibility **snapshot**, not live DOM, and feeds `plan.step` -- which `submission_step_finality()` never reads. Its own output also drives `next_kind`, but `_press_next_locked()` independently re-verifies via `submission_step_finality()` regardless of what produced `next_kind` | Generic pattern (`step`/`page` + numbers), not ATS-specific | Not independently vetted against DOM the way the Workday marker is, but operates on Playwright's own already-sanitized accessibility tree, not arbitrary page text | Yes -- returns `None` on disagreement among unmarked counters, and a `None` result makes `plan.step` fall back to `"profile-only"`, which `current()`-style parsing in `_press_next_locked()` reads as `steps_remain=False` |
| `apply_flow.decide_next_step()` / `is_review_step()` (`--auto` mode) | Literal `"Submit"` button text, or the same wizard marker | Yes | Partially | `is_review_step_by_wizard_marker()`'s Workday half is vetted; the literal-Submit-text half is not ATS-specific by design (intentionally broad) | This function only ever returns `"stop"`, never authorizes a click -- it is conservative by construction |

No hidden path was found that grants `NON_FINAL`-equivalent permission to
click a recognized submit-labeled control through any route other than
`submission_step_finality()` returning literally the string `"NON_FINAL"`.
`current_step()` is a distinct, legitimate, independently-useful mechanism
(it exists to fix a different, earlier bug class -- Aristocrat, 29
September, taking the *first* "step N of M" match for current step rather
than the one actually marked current) that feeds `plan.step`, but
`plan.step` itself is never consulted by `submission_step_finality()`, so
this does not create a second authority path.

---

## 3. The positive-authority whitelist

**VERIFIED** against the actual committed `_STEP_FINALITY_SCRIPT` (read in
full, not summarized):

```js
const markers = Array.from(document.querySelectorAll(
    "[data-automation-id='progressBarActiveStep']"
));
```

This is the entire marker selector. `[aria-current='step']` was removed in
commit `c32fedf` (this review's starting commit) and does not reappear
anywhere in `_STEP_FINALITY_SCRIPT`. No `role="progressbar"`,
`aria-posinset`, `aria-setsize`, or any class-based selector appears
anywhere in the script. Confirmed by reading the file directly -- grep for
`aria-current`, `progressbar`, `posinset`, `setsize` inside
`_STEP_FINALITY_SCRIPT` returns no matches.

Fresh executions during this review (all against commit `c32fedf`, via a
real headless Chromium page, `JobApplicationAssistant.submission_step_finality()`):

| Condition | Result |
|---|---|
| `[aria-current='step']` alone, no Workday marker | `UNKNOWN` |
| `role="progressbar"` alone (any valuenow/valuemax) | `UNKNOWN` |
| `role="progressbar"` + `aria-posinset`/`aria-setsize` | `UNKNOWN` |
| `.step`, `.active`, `.wizard`, `.progress` classes, breadcrumbs, `role="tablist"`/`role="tab"`, plain text `"Step 2 of 5"` / `"Application Step 2 of 5"` / `"Almost Finished"` | `UNKNOWN` (every variant) |
| Vetted marker, visible, `current < total` | `NON_FINAL` |
| Vetted marker, visible, `current >= total` | `FINAL` |
| Vetted marker, text contains `"Review"` (no parseable number) | `FINAL` |
| Two vetted markers present (duplicate) | `UNKNOWN` |
| Vetted marker, `display:none`, claims `current < total` | `UNKNOWN` (hidden/stale not trusted) |
| Vetted marker, malformed text (no digits where expected) | `UNKNOWN` |
| No marker of any kind | `UNKNOWN` |
| Vetted marker, `current step 0 of 5` (zero-indexed) | `NON_FINAL` -- see Residual Risk R1 below |
| Vetted marker, `current step 1 of 1` | `FINAL` |

No generic or unvalidated structure produces `NON_FINAL`. **Section 3 is
satisfied**: positive authority is restricted to the one vetted structure.

---

## 4. Adversarial authority matrix (fresh execution)

All of the following were run fresh during this review, through the real
`PageAgent.press_next()`, with `protect_submission` shadowed to a no-op (no
`SubmissionGuardV0`, no init script, no listeners, no denial counters) --
either reusing the 17 tests already committed in
`tests/test_authority_boundary.py` (re-run fresh, not assumed passing from a
prior round) or via ad hoc scripts for variants that file does not cover.

- **Missing evidence** (final submit, no wizard markup, wrong `plan.step`):
  refused, `window.submitted` stays unset.
  (`test_a_genuinely_final_control_with_no_marker_and_a_wrong_step_count_is_refused`)
- **AI metadata variants** (`"2 of 5"`, `"1 of 99"`, `"5 of 5"`, malformed,
  empty, unrelated numeric text): all refused; `plan.step`'s value has zero
  effect on the outcome in every case, confirmed by varying it independently
  of the DOM. (`test_misleading_step_metadata_with_no_independent_proof_fails_closed`
  plus ad hoc runs during this review)
- **Generic ARIA** (`[aria-current="step"]`, `role="progressbar"`,
  progressbar + posinset/setsize, generic tabs, application-looking ARIA
  labels): all refused.
  (`test_an_unrelated_aria_current_step_element_is_still_not_authority`,
  `test_posinset_and_setsize_on_an_unrelated_role_is_still_not_authority`,
  `test_a_misleading_application_looking_progressbar_is_still_not_authority`,
  plus the ad hoc tab/breadcrumb variants in Section 3's table)
- **Generic text/DOM** (`.step`, `.active`, `.wizard`, `.progress`,
  breadcrumbs, tabs, `"Step 2 of 5"`, `"Application Step 2 of 5"`,
  `"Review"` outside the vetted marker, `"Almost Finished"`): all refused --
  confirmed fresh this review (Section 3's table).
- **Visibility/ambiguity** (hidden marker, duplicate markers, conflicting
  final/intermediate markers, malformed marker text, stale marker): all
  fail closed. (`test_duplicate_conflicting_markers_resolve_unknown`,
  `test_a_hidden_stale_marker_claiming_steps_remain_is_not_trusted`)
- **Submit mechanisms**: `<button type="submit">` (covered throughout the
  suite), `<input type="submit">` (ad hoc, this review: resolves `UNKNOWN`
  with no marker, as expected -- the control-type does not change the
  finality decision, which is orthogonal to how the control is marked up),
  a form-associated submitter outside its form via `form="..."`, and a
  custom `<div role="button" onclick="...">` -- none of these DOM shapes
  change `submission_step_finality()`'s answer, since that function never
  inspects the clicked control at all, only the page's own marker. No
  dangerous action proceeds solely because of label or element shape.

Every dangerous fixture used in the committed suite also includes a direct
click of the underlying control on fresh content, proving `window.submitted`
really becomes `true` when clicked directly -- so a "stop" result is proof
the control was refused, not proof the fixture was never dangerous.

---

## 5. Positive control

**VERIFIED**, fresh execution.

`<div data-automation-id="progressBarActiveStep">current step 2 of 5</div>`
→ `submission_step_finality()` returns `NON_FINAL` → `PageAgent.press_next()`
returns `"moved"`, `window.submitted is True`.
(`test_a_real_workday_intermediate_step_still_proceeds`)

`<div data-automation-id="progressBarActiveStep">current step 5 of 5</div>`
→ returns `FINAL` → `press_next()` returns `"stop"`, the ordinary click is
never attempted.

Safety was not achieved by disabling all submit-capable navigation: the
legitimate flow still works end to end when real evidence exists.

---

## 6. Irreversible click audit (production)

Searched the whole repository for `.click(`, `force=True`,
`dispatch_event(...click`, `requestSubmit`, `form.submit`,
`press("Enter")`. Inspected `page_agent.py`, `browser_automation.py`,
`interaction.py`, `submission_guard.py`, `apply_flow.py`, and every adapter
under `sites/`.

**Safe, documented, not refactored** (field/dropdown/checkbox/file-chooser
interaction, never a submit-capable control):
- `sites/workday.py`, `sites/successfactors.py`, `sites/amazon.py`: Delete
  buttons, Add-section buttons, dropdown rows, Skills popup headings,
  select2/combobox openers -- confirmed by reading each call site's
  immediate context; none target a submit-labeled or `type="submit"`
  element.
- `interaction.py`'s `click_resiliently()`: a generic resilient-click
  utility (native click → `force=True` click → `element.evaluate('click')`
  fallback chain), but gated at every step by
  `SubmissionGuardV0.click_was_denied()` -- if the browser guard denies any
  attempt, the function returns `False` and does not try the next fallback.
  Used across `browser_automation.py` for modals, cookie banners, draft-card
  Save buttons, file choosers, and popup close buttons -- not for the
  application's own Next/Submit button, which `_press_next_locked()` clicks
  directly via `self.locate(page, control.ref).click(...)`, independent of
  this helper.
- `page_agent.py`'s ~30 other `.click(` call sites: checkboxes, radio
  buttons, dropdown options, address/typeahead fields, file dialogs, and
  `_open_and_read()`'s dropdown-opening click. Each was individually
  traced by context; none press a submit-labeled or `type="submit"`
  control.
- `page_agent._give_email_first()`: a sign-in helper (sends the email box's
  value, then presses whatever reads `sign in`/`log in`/`continue`/`next`/
  `submit`, or falls back to pressing Enter in the email box). Operates on
  authentication pages, governed separately by CLAUDE.md Section 5
  (`safety.password_allowed`, `emailed_codes.why_not()`); logging in is not
  the "submit this application to the employer" action class Finding 6 is
  about. **OUT OF SCOPE** for this review, consistent with every prior
  round's scoping.
- `submission_guard.py:470`'s `locator.click(timeout=10_000)`: this is
  `SubmissionGuardV0.submit_verified()`'s own click -- preceded by
  `tracker.begin_submission_dispatch()`, a capability-token `authorize()`
  check, and followed by a denial-count comparison. This is the verified
  gateway's own internal click (Section 7), not a bypass.

**DEFECT — found during this audit, not touched by any Finding-6
remediation commit**:

`browser_automation._look_and_act_locked()` (the vision/model fallback,
invoked from `apply_flow.main()`'s `--auto`-mode loop at line 1766, only
when the ordinary `decide_next_step()` finds nothing to press AND
`assistant.find_submit_button(page) is None`) clicks whatever control
Claude's vision response names, gated only by
`safe_to_click_for_claude(page, label)` -- a **label-text** check
(`safety.is_attestation`, a `_NEVER_CLICK` regex, and `safety.is_submit_label`)
with **no call to `submission_step_finality()` anywhere in this function**.
If the vision-selected label does not match `is_submit_label`'s vocabulary
(an unusual label, e.g. the project's own existing test uses `"Continue"` on
a `<button type="submit">`), `safe_to_click_for_claude()` returns `True` and
`_look_and_act_locked()` clicks it directly
(`el.click(timeout=5_000)` at `browser_automation.py:2141`), with only the
browser-side `SubmissionGuardV0`'s denial-count check immediately after as
protection:

```python
el.click(timeout=5_000)
if guard is not None and denials_before is not None \
        and guard.denial_count(page) > denials_before:
    logger.error("SUBMISSION_GUARD_V0_DENIED: vision-selected submit control was blocked")
    return kind, False
```

This is confirmed, not speculative: the project's own pre-existing test
`tests/test_submission_firewall.py::test_vision_selected_control_cannot_bypass_the_guard`
explicitly constructs this exact scenario -- it sets
`assistant.safe_to_click_for_claude = lambda _page, _label: True` (simulating
the label check being satisfied/fooled) and a *real, armed*
`SubmissionGuardV0`, then asserts the click is denied *by the guard*. The
test's own existence is proof that this path's design has always depended
on the browser guard as the actual, primary stopping mechanism for an
unrecognized-label final control -- not on any DOM-structural authority
check comparable to `submission_step_finality()`.

**Reachability**: confirmed this is not a rare, opt-in path.
`apply.py:run_one()` defaults `auto: bool = True`; `apply.py:main()` computes
`run_one(url, auto=auto or True, ...)` -- `auto or True` is unconditionally
`True` regardless of whether `--auto` was passed on the CLI. `web_ui.py`
invokes `apply.py <url>` with no flags, so every dashboard-triggered run
reaches `apply.py`'s `main()` with `auto=True`, which appends `--auto` to the
`apply_flow.py` subprocess unconditionally. `apply_flow.py main()`'s
`args.auto` branch (containing the `look_and_act()` call) is therefore live
in the project's actual default, primary execution path, not a legacy or
rarely-used mode.

**History**: this exact scenario was identified early in Phase 0-B1 (the
adversarial review's "MODEL-AUTHORITY BYPASS: REACHABLE FOR UNRECOGNIZED
CONTROLS" finding) and was deliberately fixed, at that time, by adding the
browser-side structural default-deny (commit `48ac241`, "Default-deny
unrecognized final-submit controls") -- a conscious design choice that
unrecognized-label controls are caught by browser-side structural analysis,
not by a Python-side DOM-authority check. None of the five Finding-6
remediation commits (`756b77b`, `1d312cf`, `733c474`, `f5d59a6`, `c32fedf`)
touched `look_and_act()`, `safe_to_click_for_claude()`, or `find_submit_button()`
-- confirmed by `git log --oneline` on each file across those commits.

**Why this is reported as a DEFECT under this review's own criteria**: this
review's stated completion bar is explicit -- "Any path where browser
containment can again become the sole remaining protection against
irreversible submission is a DEFECT and blocks merge" -- and explicitly
directs auditing "model/vision click paths" to answer exactly this question.
For this specific path, browser containment is not *additional* defense
layered on top of a Python authority check; it is the *only* check,
architecturally, by design. That architecture has been known, tested, and
accepted since early Phase 0-B1, with its own dedicated passing test proving
it works as designed -- but it does not satisfy the narrower, stricter
invariant this comprehensive review was asked to verify end-to-end.

---

## 7. The final automated submission gateway

**VERIFIED.** Searched the whole repository for callers of
`submit_verified`, `click_verified_submit`, `begin_submission_dispatch`:

```
apply_flow.py:853   submitted = submit_verified(assistant, page, tracker, key, job, job_dir, decision)
apply_flow.py:1132  clicked = assistant.click_verified_submit(page, decision)
browser_automation.py:7056  self._ensure_submission_guard(page).submit_verified(...)
submission_guard.py:460     tracker.begin_submission_dispatch(dedup_key, aliases)
db.py:229                   self._local_submission_tracker().begin_submission_dispatch(...)
```

One linear chain: `apply_flow.submit_verified()` →
`assistant.click_verified_submit()` → `SubmissionGuardV0.submit_verified()`
→ `tracker.begin_submission_dispatch()` → the guard's own authorized click.
`submit_verified()` is only called when `decision.eligible` from
`safety.evaluate_auto_submit()` -- the strictly-verified path BEHAVIOUR.md
and CLAUDE.md describe. No second gateway exists. (Three stale,
non-importable copies of these functions exist under `output/` and
`sandbox/` snapshot directories -- confirmed unreferenced by any live import
-- and are not part of the production call graph.)

---

## 8–11. Replay, durable dispatch, concurrency, browser containment

**VERIFIED by diff evidence**, per this review's own instruction to
spot-check rather than redesign or exhaustively re-prove these from
scratch, and because the committed test suites for each (already re-run
fresh in Section 15) continue to pass unchanged.

`git diff --stat codex/architecture-reliability..HEAD` (production files
only) shows exactly: `BEHAVIOUR.md`, `apply_flow.py`, `browser_automation.py`,
`db.py`, `interaction.py`, `jd_analyzer.py`, `job_tracker.py`, `page_agent.py`,
`safety.py`, `submission_guard.py`, `.gitignore` changed across the *entire*
branch (all prior passes combined). Narrowing to just the five Finding-6
remediation commits (`git diff --stat 756b77b~1..HEAD`): **only
`browser_automation.py` and `page_agent.py`** changed. `job_tracker.py`
(canonical identity, aliases, `UNCERTAIN` handling, `begin_submission_dispatch`'s
`BEGIN IMMEDIATE` transaction), `db.py` (durable state, `_local_submission_tracker`),
`submission_guard.py` (browser default-deny, listener repair, missing-guard
restoration), and `safety.py` (touched once, in commit `48ac241`, which
predates and is unrelated to the Finding-6 saga -- that commit was the
explicitly-authorized Pass 4 remediation) were not touched by any of the
work this comprehensive review is examining.

Confirmation detection (`browser_automation.submission_confirmed()`) is in
`browser_automation.py`, which the Finding-6 commits did modify -- but only
within `_STEP_FINALITY_SCRIPT`/`submission_step_finality()`/
`is_review_step_by_wizard_marker()`'s docstring; `git diff 756b77b~1..HEAD --
browser_automation.py` shows no lines inside `submission_confirmed()` itself
changed.

The full suite (Section 15) re-confirms all of
`test_submission_effect_state.py`, `test_submission_firewall.py`,
`test_auto_submit.py`, `test_safety_rules.py`, and `test_repeated_entries.py`
-- which between them exercise replay/alias/UNCERTAIN handling, durable
dispatch atomicity, concurrency, and browser-side containment -- pass at
198/198, unchanged from the established reference.

---

## 12. Saved-page authority audit

Two distinct saved-page corpora exist in this repository, and they are not
interchangeable for this audit:

- **834 + 289 + 304 = 1,427** Playwright **accessibility-snapshot** text
  files (`output/*/pages/page_*.txt`, `output/*/account/*.txt`,
  `runs/*/axtree_dump.json`) -- the corpus `replay_guard.py` uses. These
  preserve role/name/value/ref, **not** arbitrary HTML attributes.
  `data-automation-id` cannot be recovered from them at all, so
  `submission_step_finality()` (which depends entirely on that attribute)
  **cannot be run against this corpus** -- not because the pages lack
  evidence, but because this snapshot format does not carry the kind of
  evidence the function reads. This is a genuine methodological limitation,
  reported here rather than concealed by a misleadingly precise-looking
  count.
- **141** raw captured HTML pages (`output/**/evidence_*/page.html`),
  produced only by forensic/circuit-breaker dumps on a stuck page. These
  **do** preserve the full DOM and are directly usable.

**Audit method**: loaded each of the 141 `page.html` files into a fresh
Chromium page via `page.set_content()` and called the real
`JobApplicationAssistant.submission_step_finality()`. First attempt (JS
enabled) produced 141/141 `UNKNOWN` uniformly -- traced and found to be an
artifact of the harness, not the classifier: these are captured SPA pages
with inline `<script>` tags that, with no live backend to hydrate against,
wipe the DOM on reload (confirmed directly: `document.body.innerHTML.length`
dropped from 169,067 characters to 24 after `set_content()` with JS
enabled, even though the raw file text does contain
`data-automation-id="progressBarActiveStep"` as real markup, confirmed via
`grep`). Re-ran with `java_script_enabled=False` on the browser context,
which reads the DOM exactly as captured, no live script execution.

**Results (141 pages, JS disabled):**
- `FINAL`: **0**
- `NON_FINAL`: **0**
- `UNKNOWN`: **136**
- Load error (5 pages, all `Charles_Schwab_Senior_Site_Reliability_Engineer`
  evidence captures): `Execution context was destroyed, most likely because
  of a navigation` -- independently confirmed via plain-text `grep` that
  none of these 5 files contain the string `progressBarActiveStep` at all,
  so this loading issue could not have hidden a `NON_FINAL` result either
  way.

**Manual inspection of every `NON_FINAL` result: there are none to
inspect.** Grepping all 141 files directly (independent of the Playwright
harness) for the literal string `progressBarActiveStep` found it in exactly
**2** of 141 files:

1. `output/AIG_Technologies,_Inc._Senior_Network_Data_Center_Engineer/evidence_20261005_133527_step8/page.html`
2. `output/pr4-ci-fix/output/Everfox_Holdings,_LLC_Senior_Network_Engineer/evidence_20261004_125829_step9/page.html`

Both resolved `UNKNOWN`, not `FINAL` or `NON_FINAL`, and both are
explainable: both markers' own text is `"current step 6 of 6Review"` --
the active `<li>`'s accessible content is built from two adjacent sibling
`<label>` elements (`<label>current step 6 of 6</label><label>Review</label>`)
with no whitespace between them in the DOM, so `.textContent` concatenates
them into one unbroken token, `"...6Review"`. The regex
`/\bstep\s+(\d+)\s+of\s+(\d+)\b/i` requires a word boundary immediately after
the second digit group; `"6"` immediately followed by `"R"` (both word
characters) has no such boundary, so the numeric match fails. The fallback
`/\breview\b/i` check **also** fails for the same reason -- `"Review"` is
glued onto `"6"` with no boundary before it either. Both of these recorded
pages are genuinely the Review (final) step -- so this concatenation quirk
pushes a case that should read `"FINAL"` to `"UNKNOWN"` instead. **This is
safety-neutral**: Section 1 already established `UNKNOWN` and `FINAL` are
behaviorally identical in `_press_next_locked()`'s formula -- both refuse
the click. It cannot, by the same mechanism, ever produce a false
`"NON_FINAL"` (a regex that fails to match simply yields no evidence, never
fabricates a wrong answer).

**Residual risk (R2, see below)**: the same concatenation mechanics could,
in principle, also affect a genuinely intermediate step whose active-step
label text is immediately followed by other text with no separating
whitespace, pushing a legitimate `NON_FINAL` case to `UNKNOWN` instead (a
compatibility/over-blocking cost, not a security one). None of the 141
captured pages exhibit this specific variant, and this project's own
synthetic test fixtures (a single un-split text node, e.g.
`"current step 2 of 5"`) do not reproduce the real page's two-adjacent-label
structure, so this is also a **test gap**: no test exercises the
concatenated-label shape found in real Workday markup.

No saved page classified `NON_FINAL` from generic or unvetted markup. Zero
`NON_FINAL` results across the entire 141-page corpus that could actually
be checked; the 1,427-page accessibility-snapshot corpus cannot be checked
by this method at all, for the structural reason given above, and is
reported as a scope limitation rather than a false "clean" result.

---

## 13. Finding 10

**CLOSED**, for `submission_step_finality()`/`press_next()`'s own authority
model. `tests/test_authority_boundary.py` (17 tests, all re-run fresh this
review, passing): uses the real `PageAgent.press_next()` throughout;
`protect_submission` is a no-op, so no `SubmissionGuardV0` is ever
constructed; multiple tests prove the underlying control's `onsubmit`
really fires on a direct click before asserting `press_next()` refuses it;
and the suite covers the no-wizard reproduction, misleading `plan.step`,
unsupported ATS, generic progressbar (bare, and with posinset/setsize),
generic `aria-current="step"`, ambiguity/visibility cases, and the
legitimate vetted Workday intermediate flow.

The one major authority class with no dedicated no-browser-guard test is
the vision/`look_and_act()` path from Section 6 -- but that gap is not
silent: `tests/test_submission_firewall.py::test_vision_selected_control_cannot_bypass_the_guard`
exists and explicitly exercises it, deliberately *with* the real guard
armed, because that is how the path was designed to be protected. The gap,
precisely stated, is that no test proves Python *itself* refuses in that
path without the guard -- because, by design, it currently cannot.

---

## 14. Regression scope

Reviewed via `git diff --stat` at two granularities (whole branch vs. base,
and the five Finding-6 commits in isolation -- Section 8–11). No unrelated
behavior change was found in either diff. `safety.py`'s only change on this
entire branch is commit `48ac241` (Pass 4, explicitly owner-authorized,
predates the Finding-6 saga by several commits). No other file outside
`browser_automation.py`, `page_agent.py`, `tests/test_authority_boundary.py`,
and (once, in the prior round) `tests/test_submission_firewall.py` was
touched by any Finding-6-labeled commit.

---

## 15. Fresh validation

All run fresh during this review, against commit `c32fedf`, with zero
production or test changes made at any point.

- **A. `tests/test_authority_boundary.py`**: **17 passed** in 36.67s.
  Matches reference exactly.
- **B. Original P0-B1 targeted suite**
  (`test_submission_effect_state.py test_submission_firewall.py
  test_auto_submit.py test_safety_rules.py test_repeated_entries.py`):
  **198 passed** in 294.95s. Matches reference exactly.
- **C. PageAgent/review-step suites**
  (`test_phase3_features.py test_finding_the_way.py
  test_hidden_choices_and_breaker_reset.py test_step_counter_on_the_last_page.py`):
  **92 passed** in 228.11s (82 from the three suites previously used as
  reference, plus 10 from `test_step_counter_on_the_last_page.py`,
  discovered and included during this review's Section 2 enumeration as a
  directly relevant, previously-unlisted suite).
- **D. Submission/replay/safety suites**: covered by B above; all pass.
- **E. Full suite**, cache cleared first
  (`rm .pytest_cache/v/cache/lastfailed`), `pytest -q -rs -n auto`:
  **`18 failed, 2024 passed, 3 skipped, 1 error`** in 832.18s (0:13:52).
  Matches the stated reference exactly by count, and by exact node-ID
  comparison: all 19 failing/error node IDs (`test_ant_select.py`'s
  collection error, the same 6 `test_failures_catalogue.py` parametrized
  references, the same 12 `test_page_agent.py` behavior failures --
  pre-existing and traced to an already-present, uncommitted local
  modification to that file that predates this entire engagement) match
  the established baseline exactly, by name, not merely by count. Zero new
  failing node IDs. Zero new error node IDs. No baseline failure
  disappeared. No test newly skipped beyond the 3 already-known
  environment-dependent skips (two real-Chrome-window tests gated by
  `RAISE_WINDOW_TEST=1`, one database-unavailable skip appearing twice).

---

## Classified findings

| # | Finding | Classification |
|---|---|---|
| 1 | Finality authority model's effective semantics match the stated invariant exactly (`NON_FINAL`/`FINAL`/`UNKNOWN` tri-state, `UNKNOWN` behaviorally equal to `FINAL`) | VERIFIED |
| 2 | No hidden generic path grants `NON_FINAL`-equivalent permission; `current_step()` is a distinct, legitimate mechanism that does not feed `submission_step_finality()` | VERIFIED |
| 3 | Positive-authority whitelist is exactly one structure, confirmed by reading the script and by fresh execution against every generic variant named in this review | VERIFIED |
| 4 | Full adversarial matrix (missing evidence, AI metadata variants, generic ARIA, generic text/DOM, visibility/ambiguity, submit mechanisms) fails closed in every case tested | VERIFIED |
| 5 | Legitimate vetted Workday intermediate flow proceeds; the true-final case is refused | VERIFIED |
| 6a | Field/dropdown/checkbox/file-chooser/sign-in click paths do not target submit-capable controls | VERIFIED |
| 6b | `look_and_act()` (vision fallback) clicks a vision-selected control with only a label-text pre-filter and the browser guard as protection -- no DOM-structural authority check, reachable in the project's actual default execution path | **DEFECT** |
| 7 | Exactly one final-submit gateway exists; no second path to `begin_submission_dispatch()`/the guard's authorized click | VERIFIED |
| 8–11 | Replay, durable dispatch, concurrency, and browser-containment properties unaffected by any Finding-6 commit (diff evidence + full suite re-pass) | VERIFIED |
| 12 | Zero `NON_FINAL` results across all 141 checkable saved pages; 2 pages with the vetted marker both resolve the safety-equivalent `UNKNOWN` due to a text-concatenation regex quirk; 1,427 accessibility-snapshot pages cannot be checked by this method at all (reported as scope limitation, not a false-clean result) | VERIFIED (with R2/T1 noted below) |
| 13 | Finding 10 closed for the `submission_step_finality()` authority model; the `look_and_act()` gap is openly reflected by an existing test that proves the guard-dependency, not hidden by an absent test | CLOSED (scoped) |
| 14 | No unrelated behavior change found in either diff granularity examined | VERIFIED |
| 15 | Full validation suite, exact node-ID match against baseline | VERIFIED |
| R1 | `current step 0 of 5` (zero-indexed) grants `NON_FINAL` with no explicit `current >= 1` floor; low real-world likelihood (requires the vetted marker's own text to contain a zero-indexed step), and does not introduce a new trust boundary beyond the existing marker-trust model | RESIDUAL RISK (LOW) |
| R2 | The regex's `\b` word-boundary requirement can fail on real Workday markup where adjacent label elements have no separating whitespace (confirmed on 2 real recorded pages); failure mode is always safe-direction (pushes toward `UNKNOWN`/over-blocking, never toward a wrong `NON_FINAL`) | RESIDUAL RISK (LOW) |
| T1 | No test in `tests/test_authority_boundary.py` uses the concatenated-adjacent-label text shape found in real recorded Workday pages (all synthetic fixtures use a single unsplit text node) | TEST GAP |
| T2 | No test proves `look_and_act()` refuses a dangerous, unusually-labeled control with the browser guard entirely absent (the existing test deliberately keeps the guard armed, because that is the path's actual design) | TEST GAP |
| O1 | Stale, unreferenced copies of gateway functions under `output/`/`sandbox/` snapshot directories | OUT OF SCOPE (confirmed unreferenced, not part of the live call graph) |
| O2 | `_give_email_first()`'s sign-in-page Enter/button press | OUT OF SCOPE (authentication, governed separately by CLAUDE.md Section 5, not the application-submission action class) |

---

## Finding 6: **NOT CLOSED**

The specific, long-tracked instance of Finding 6 -- a recognized
submit-labeled control, reached through `PageAgent.press_next()`, being
misclassified as intermediate on the strength of `plan.step` or an unvetted
generic DOM signal -- is closed, thoroughly, and re-verified fresh in this
review (Sections 1–5, 15A). It does not reopen under any variant tested.

Under this review's own broader, explicitly-stated completion bar --
auditing every production path capable of performing a potentially final
application action, and requiring that none of them leave browser
containment as the sole remaining protection -- one such path exists:
`browser_automation._look_and_act_locked()`, reachable from the project's
actual default execution path (Section 6b). This is not new code and was
not introduced or modified by any of the five commits this Finding-6 saga
produced; it is a separate, earlier, deliberately-architected, and
already-tested design choice from Phase 0-B1's own Pass 4. But it is, as of
this review, a real instance of exactly the condition this review's Section
17 defines as a blocking defect.

## Finding 10: **CLOSED** (scoped to the authority model `tests/test_authority_boundary.py` was built to cover)

No test gap exists for any variant of the `submission_step_finality()`/
`press_next()` authority model. The `look_and_act()` path's absence of a
no-guard test is not a hidden gap -- it is the direct, visible consequence
of Finding 6 (6b) remaining open for that path; a test proving Python
refuses there without the guard cannot be written honestly until that
defect is addressed, because Python currently does not refuse there without
the guard.

## Recommendation

**NOT READY FOR P0-B1 MERGE**

Per this review's own stated criteria, READY requires that "no alternate
Python final-submit bypass exists" and that browser containment is absent
from the authority proof for every path capable of a potentially final
action. Section 6b identifies one such path where this does not hold. Per
instructions, this defect is documented precisely and not fixed in this
review.
