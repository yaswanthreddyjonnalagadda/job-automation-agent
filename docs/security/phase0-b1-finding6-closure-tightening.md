# Phase 0-B1 — Finding 6 Closure Tightening

**Branch:** `fix/submission-replay-and-containment-hardening`
**Base:** `codex/architecture-reliability`
**Commit under repair:** `1d312cf` (the general authority-boundary fix)

> **Superseded, 7 October 2026.** The `aria-posinset`/`aria-setsize` fix
> described below (committed as `733c474`) was itself found insufficient by a
> further closure review (`docs/security/phase0-b1-final-closure-review.md`,
> an independent, historical record this document does not alter): that
> attribute pair is plain set-position semantics for a set-item role, and its
> presence does not establish that the element belongs to, or describes, the
> application's own wizard rather than an unrelated upload, onboarding, or
> document sub-process. The generic, non-Workday signal has since been
> **removed entirely** -- see "Round 2: complete removal of the generic
> signal" below, which is the current, superseding state of the code. The
> content immediately below this notice is kept as an accurate historical
> record of what Round 1 actually did and why; it no longer describes the
> code as it stands.

A closure review of `1d312cf` found that `submission_step_finality()`'s generic,
non-Workday signal -- any `role="progressbar"` whose accessible name merely
contained the word "step" -- could be satisfied by evidence that had nothing to
do with the application's own step sequence, and that its numeric parsing of
`aria-valuenow`/`aria-valuemax` relied on JavaScript's own coercion rules in a
way that accepted malformed input as valid.

## Confirmed defects

Independently reproduced (not merely taken on report) by calling
`JobApplicationAssistant.submission_step_finality()` directly against three
constructed pages, before any fix:

| Input | Result before this fix |
|---|---|
| `role="progressbar" aria-valuenow="2" aria-valuemax="5" aria-label="Upload step 2 of 5"` (an unrelated upload-progress indicator) | `NON_FINAL` |
| `role="progressbar" aria-valuenow="" aria-valuemax="5" aria-label="Step progress"` | `NON_FINAL` |
| `role="progressbar" aria-valuenow="-1" aria-valuemax="5" aria-label="Step progress"` | `NON_FINAL` |

The first defect: a bare accessible-name text match ("step" appears anywhere
in the label) is not evidence the progressbar describes the *application's*
steps -- any unrelated progress indicator whose label happens to contain that
word satisfied it.

The second and third: `Number("")` and `Number("-1")` are both valid, finite
JavaScript numbers (`0` and `-1` respectively) -- the prior check
(`Number.isFinite(now) && ... && now < max`) accepted both, since `0 < 5` and
`-1 < 5` are both true. The parsing relied on `Number()`'s own coercion rather
than validating that the attribute actually held a well-formed, non-negative
number.

Each of these meant a genuinely final Submit control, reached while
`plan.step` is wrong, on a page carrying nothing more than an unrelated or
malformed progress indicator, could still enter the ordinary intermediate
path -- leaving browser containment as the only remaining protection, exactly
the class of defect this whole follow-up chain exists to close.

## Fix

### 1. Structural association, not text

The generic signal no longer accepts a bare `role="progressbar"` with any
particular label text. It now additionally requires `aria-posinset` and
`aria-setsize` -- the real ARIA "position in an ordered/sized set" attribute
pair (the pattern used by tabs, listitems, and step indicators to say "I am
item K of N in a set") -- to be present on the same element. An unrelated
upload or loading progress bar has no reason to carry this pair; a genuine
step/wizard item does, by the ARIA pattern's own purpose. The accessible-name
"step" text check is removed entirely; nothing in the new path reads the
label at all.

### 2. Strict numeric parsing

A new `strictNumber()` helper replaces the bare `Number()` coercion for
`aria-valuenow`/`aria-valuemax`: it requires the trimmed attribute string to
match `^-?\d+(\.\d+)?$` in full before parsing it at all, rejecting an empty
string, whitespace, `"NaN"`, `"Infinity"`, hex-looking text, and any other
non-numeric content outright, rather than trusting what `Number()` happens to
coerce them to. The full validation before granting `NON_FINAL` is now: both
values parse under `strictNumber()`, `now >= 0`, `max > 0`, `now <= max`
(internal consistency), and, for `NON_FINAL` specifically, `now < max`
strictly. Any failure of these -- including `now > max` -- resolves to
`UNKNOWN`, never `FINAL` and never `NON_FINAL`.

Workday's own numeric signal (`"current step N of M"` parsed via
`/\bstep\s+(\d+)\s+of\s+(\d+)\b/i`) was not affected by either defect: a regex
capture group of `\d+` can never be empty, negative, or non-numeric by
construction, so no change was needed there.

### 3. Preserved behavior

Unchanged: Workday's numeric current/total marker (`FINAL` at/after total,
`NON_FINAL` before it); `UNKNOWN` for an unsupported page with no recognized
structure; `UNKNOWN` for more than one conflicting marker; a hidden/stale
marker unable to authorize `NON_FINAL` (still checked via
`getClientRects().length > 0`); and the legitimate intermediate transition
when trustworthy, now-doubly-verified (structural + numeric) evidence exists.

## Code changed

- `browser_automation.py`: `_STEP_FINALITY_SCRIPT` rewritten -- added
  `strictNumber()`, replaced the `role="progressbar"[...]"step"-in-label`
  selector with `role="progressbar"[aria-valuenow][aria-valuemax]
  [aria-posinset][aria-setsize]`, and applied the strict, fully-validated
  numeric comparison described above. Docstring on
  `submission_step_finality()` updated to describe the new evidence and the
  defects that motivated it. No other function touched.
- `tests/test_authority_boundary.py`: the legitimate non-Workday fixture
  (`ARIA_PROGRESSBAR_NON_FINAL_STEP`) gained `aria-posinset`/`aria-setsize` so
  it still passes the tightened association gate. Five new tests added
  (unrelated upload progressbar, empty value, negative value, value exceeding
  its own max, malformed string), each calling the real
  `PageAgent.press_next()` with no browser-side guard attached. Two tests
  whose fixtures predated the association requirement
  (`test_a_malformed_progressbar_value_resolves_unknown`,
  `test_an_inconsistent_progressbar_value_resolves_unknown_not_final`) were
  removed as redundant: their fixtures lacked `aria-posinset`/`aria-setsize`
  entirely, so under the new code they would pass for the association gate
  alone rather than exercising the numeric-validation logic the new,
  more precisely-targeted tests isolate directly.

## Validation

Independently reproduced all three confirmed defects before touching the fix
(see table above), then re-ran the identical constructed pages after the fix:
all three now resolve to `UNKNOWN`; the legitimate evidence case (full
structural + numeric evidence) still resolves to `NON_FINAL`.

- `tests/test_authority_boundary.py`: **14 passed** in 16.08s.
- Original P0-B1 targeted suite
  (`test_submission_effect_state.py test_submission_firewall.py
  test_auto_submit.py test_safety_rules.py test_repeated_entries.py`):
  **198 passed** in 199.81s.
- PageAgent/review-step/navigation suites
  (`test_phase3_features.py test_finding_the_way.py
  test_hidden_choices_and_breaker_reset.py`): **82 passed** in 145.76s.
- Full suite, cache cleared first: **`18 failed, 2021 passed, 3 skipped, 1
  error`** in 737.15s (0:12:17). The `+3` over the prior `2018 passed` is
  exactly the net new tests (5 added, 2 removed). All 18 failing node IDs
  plus the 1 collection error were compared by exact name against the
  established baseline: identical set, same `test_ant_select.py` collection
  error, same 6 `test_failures_catalogue.py` parametrized references, same 12
  pre-existing `test_page_agent.py` failures (unrelated to this change; see
  `phase0-b1-general-authority-boundary-followup.md`). Zero new regressions.

## Finding 6: CLOSED (tightened) -- as of Round 1, since superseded by Round 2 below

The general case closed in `1d312cf` remains closed, and the specific gap
this review found in that fix's own generic-evidence path -- an unrelated or
malformed progress indicator being mistaken for application-wizard evidence
-- is now closed as well. `NON_FINAL` requires both a real structural
relationship to an ordered step set and strictly-validated numeric values;
absence of either resolves to `UNKNOWN`, which fails closed exactly like
`FINAL`.

---

## Round 2: complete removal of the generic signal

**Commit under repair:** `733c474` (Round 1's `aria-posinset`/`aria-setsize` fix)

A further closure review
(`docs/security/phase0-b1-final-closure-review.md`) found Round 1's own fix
still insufficient: `aria-posinset`/`aria-setsize` are plain ARIA
set-position semantics for a set-item role (tabs, listitems, options) --
their presence establishes only "this element is item K of N in *some*
ordered set", never specifically that the set in question is *this
application's own* wizard/navigation sequence. The implementation checked
only for their presence, not for any relationship to the application itself,
so an unrelated widget -- an upload indicator, an onboarding stepper, a
document-processing progress bar -- carrying that same generic ARIA pattern
could still satisfy the selector and grant `NON_FINAL`.

### Confirmed (re-reproduced independently before fixing)

| Input | Result before Round 2 |
|---|---|
| `role="progressbar" aria-valuenow="2" aria-valuemax="5" aria-posinset="1" aria-setsize="3" aria-label="Upload progress"` | `NON_FINAL` |
| `role="progressbar" aria-valuenow="2" aria-valuemax="5" aria-posinset="2" aria-setsize="5" aria-label="Application Step 2 of 5"` (a deliberately convincing, application-looking label) | `NON_FINAL` |

Both single-element checks above resolve to `UNKNOWN` after this round's fix,
confirmed directly against `JobApplicationAssistant.submission_step_finality()`
before writing the corresponding tests.

### Fix

The entire generic, non-Workday branch of `_STEP_FINALITY_SCRIPT` is removed
-- not replaced with another cross-ATS heuristic (text-based, ARIA-based, or
otherwise). Positive `NON_FINAL` evidence is now restricted to the single
structure actually vetted against a real recorded production page: Workday's
own active-step marker, whose own text carries a numeric `"current step N of
M"` phrase (`browser_automation.py`'s `submission_step_finality()`
docstring documents the recorded page this was confirmed against). Any other
page shape -- an unsupported ATS, or a generic progressbar however
convincing its label or ARIA attributes -- resolves `"UNKNOWN"`, which fails
closed exactly like `"FINAL"`.

The now-unused `strictNumber()` helper (Round 1's numeric-coercion fix) was
removed along with the branch that was its only caller; the *principle* it
encoded -- never let `Number()`'s own coercion of an attribute string decide
a safety-relevant comparison -- remains fully honored by the one signal that
is left, since a digit-only regex capture group (`\d+`) can never be empty,
negative, or non-numeric by construction, with no coercion step involved at
all.

### Preserved behavior

Unchanged: Workday's numeric current/total marker (`FINAL` at/after total,
`NON_FINAL` strictly before it, requiring visibility); `UNKNOWN` for more
than one conflicting marker; a hidden/stale marker unable to authorize
`NON_FINAL`; `UNKNOWN` for any unsupported ATS shape (an accepted
compatibility cost, per the task's own framing -- a future ATS-specific
positive proof may be added later, but only after individual validation
against that ATS's own real recorded markup, never as another generic
heuristic); and the legitimate Workday intermediate transition when real
step-count evidence exists.

### Code changed

- `browser_automation.py`: `_STEP_FINALITY_SCRIPT`'s entire generic
  `role="progressbar"` branch (and its now-orphaned `strictNumber()` helper)
  removed; the function falls straight to `"UNKNOWN"` once the Workday-marker
  branch is exhausted. `submission_step_finality()`'s docstring rewritten to
  describe the two prior generic-signal attempts, why each was found
  insufficient, and the current, narrower evidence model. No other function
  touched.
- `tests/test_authority_boundary.py`: the legitimate-flow fixture/test is now
  `WORKDAY_LEGITIMATE_INTERMEDIATE_STEP` /
  `test_a_real_workday_intermediate_step_still_proceeds` (Workday's own real
  structure), replacing the Round 1 generic-ARIA fixture/test that no longer
  represents a working case. Two new tests added:
  `test_posinset_and_setsize_on_an_unrelated_role_is_still_not_authority`
  (Round 2's own reproduction, with an explicit direct-click proof that the
  control is genuinely dangerous) and
  `test_a_misleading_application_looking_progressbar_is_still_not_authority`
  (a deliberately convincing application-looking label, still refused). The
  four existing malformed/empty/negative/inconsistent-value tests are kept
  unchanged, as a permanent regression guard against reintroducing that
  numeric-coercion class of bug if any future, individually-vetted
  ATS-specific signal is ever added.

### Validation

Independently reproduced both Round 2 defects before fixing (table above);
re-ran the identical constructed pages after the fix: both now resolve to
`UNKNOWN`. Also re-confirmed, in the same pass, that the previously-fixed
Round 1 defects (unrelated upload-labeled progressbar, empty/negative
`aria-valuenow`) and the Workday-legitimate and unsupported-ATS cases all
still resolve correctly.

- `tests/test_authority_boundary.py`: **16 passed** in 17.84s.
- Original P0-B1 targeted suite
  (`test_submission_effect_state.py test_submission_firewall.py
  test_auto_submit.py test_safety_rules.py test_repeated_entries.py`):
  **198 passed** in 198.82s.
- PageAgent/review-step/navigation suites
  (`test_phase3_features.py test_finding_the_way.py
  test_hidden_choices_and_breaker_reset.py`): **82 passed** in 147.69s.
- Full suite, cache cleared first: **`18 failed, 2023 passed, 3 skipped, 1
  error`** in 788.84s (0:13:08). The `+2` over the prior `2021 passed` is
  exactly this round's net new tests. All 18 failing node IDs plus the 1
  collection error were compared by exact name against the established
  baseline: identical set, same `test_ant_select.py` collection error, same
  6 `test_failures_catalogue.py` parametrized references, same 12
  pre-existing `test_page_agent.py` failures (unrelated to this change).
  Zero new regressions.

## Finding 6: CLOSED (narrowed to the one vetted signal) -- as of Round 2, since narrowed further by Round 3 below

No generic, cross-ATS heuristic remains as a source of `NON_FINAL` authority
-- neither a bare accessible-name text match (Round 1's own first attempt),
nor `aria-posinset`/`aria-setsize` presence on an arbitrary `role="progressbar"`
(Round 1's own second attempt, this round's subject), nor any
application-looking label text. The only positive `NON_FINAL` evidence left
is Workday's own active-step marker, individually vetted against a real
recorded production page. Every other page shape -- supported ATS or not --
resolves `UNKNOWN`, which fails closed exactly like `FINAL`, with browser
containment never again the sole remaining protection for the scenario this
whole chain of follow-ups exists to close.

---

## Round 3: the marker selector itself still matched an unvetted generic state

**Commit under repair:** `f5d59a6` (Round 2's complete removal of the generic
progressbar signal)

A third closure review found that Round 2's own "the only vetted structure"
claim was not quite true: the marker selector feeding
`submission_step_finality()`'s Workday branch was still

```
[data-automation-id='progressBarActiveStep'], [aria-current='step']
```

-- a leftover from the very first authority-boundary fix (commit `756b77b`),
carried forward unexamined through every subsequent round. Only the first
half of that selector was ever vetted against a real recorded page.
`aria-current="step"` is a generic, standards-based ARIA state; any unrelated
stepper or tab widget elsewhere on the same page -- with nothing to do with
the application at all -- can carry it, and the selector accepted it exactly
as it accepted Workday's own marker.

### Confirmed (re-reproduced independently before fixing)

```html
<div aria-current="step">current step 2 of 5</div>
<form onsubmit="window.submitted=true; return false">
  <button type="submit">Submit</button>
</form>
```

Called directly against `JobApplicationAssistant.submission_step_finality()`:
result was `NON_FINAL`. The vetted Workday marker
(`data-automation-id="progressBarActiveStep"`), tested in the same pass,
correctly also resolved `NON_FINAL` for its own legitimate case -- confirming
both halves of the compound selector were being treated identically, which
was exactly the problem.

### Fix

`[aria-current='step']` removed from the marker selector in
`_STEP_FINALITY_SCRIPT`; it now reads only
`[data-automation-id='progressBarActiveStep']`. `is_review_step_by_wizard_marker()`
(a separate, pre-existing function from commit `756b77b`, used elsewhere for
a FINAL-only, over-blocking check, never for `NON_FINAL` authority) still
includes `[aria-current='step']` in its own selector -- left unchanged, out
of this narrow fix's scope, since it can only make that function's own check
*more* conservative, never grant permission.

### Re-verified in the same pass

- Multiple Workday active-step markers → `UNKNOWN` (unchanged).
- A hidden Workday marker claiming more steps remain → does not authorize
  `NON_FINAL` (unchanged).
- Malformed Workday numeric text (non-digit where a step number is expected)
  → `UNKNOWN` (unchanged).
- A final Workday marker (`N of N`) → `FINAL` (unchanged).
- The literal word `'Review'` in the marker → `FINAL` (unchanged).
- The now-removed, unrelated `[aria-current='step']` → `UNKNOWN`.

### Code changed

- `browser_automation.py`: the `document.querySelectorAll(...)` call inside
  `_STEP_FINALITY_SCRIPT` narrowed to
  `[data-automation-id='progressBarActiveStep']` alone.
  `submission_step_finality()`'s docstring updated to record this as the
  third successive generic-signal removal.
- `tests/test_authority_boundary.py`: one new fixture/test,
  `UNRELATED_ARIA_CURRENT_STEP_STEP` /
  `test_an_unrelated_aria_current_step_element_is_still_not_authority`,
  through the real `PageAgent.press_next()` with the browser-side guard
  entirely absent and an explicit direct-click proof that the control is
  genuinely dangerous.

### Validation

- `tests/test_authority_boundary.py`: **17 passed** in 22.64s.
- Original P0-B1 targeted suite: **198 passed** in 204.71s.
- PageAgent/review-step/navigation suites: **82 passed** in 150.76s.
- Full suite, cache cleared first: **`18 failed, 2024 passed, 3 skipped, 1
  error`** in 893.92s (0:14:53). The `+1` over the prior `2023 passed` is
  this round's one new test. All 18 failing node IDs plus the 1 collection
  error compared by exact name against the established baseline: identical
  set, zero new regressions.
- Saved-page replay comparison: see the commit message for the exact count
  recorded by the pre-commit replay guard at commit time.

This documentation file itself, previously kept local-only per this
project's usual convention for review/postmortem documents, is committed and
pushed to the remote branch in this round, per explicit instruction.

## Finding 6: CLOSED (one vetted signal, now actually isolated) -- as of Round 3, Finding 6 reopened by Round 4 below for a separate path

The marker selector authorizing `NON_FINAL` now matches only
`[data-automation-id='progressBarActiveStep']` -- the one structure actually
vetted against a real recorded production page -- with no generic ARIA state,
text pattern, or attribute combination accepted alongside it. Every other
page shape, including an unrelated element carrying a standards-based but
application-unrelated ARIA state, resolves `UNKNOWN` and fails closed exactly
like `FINAL`.

---

## Round 4: the vision/model click-fallback authority gap

**Commit under repair:** `c32fedf` (Round 3's marker-selector fix) --
**not** `submission_step_finality()` itself, which Round 4 does not touch.

The comprehensive final closure review (`phase0-b1-comprehensive-final-closure-review.md`,
Section 6b) audited every irreversible click path in the repository, not
just `submission_step_finality()`'s own callers, and found a second,
architecturally separate instance of "browser containment as sole
authority": `browser_automation._look_and_act_locked()`, the vision/model
click fallback used when the ordinary DOM reading finds nothing to fill or
press. It clicked whatever control Claude's screenshot interpretation named,
gated only by `safe_to_click_for_claude()` -- a label-text filter -- with no
DOM-structural authority check at all; the browser-side `SubmissionGuardV0`'s
post-click denial check was the only thing actually standing in front of an
unrecognized-label final control. This path is reachable in the project's
real default execution (`apply.py`'s `auto or True` makes `--auto`
unconditional), not a rare opt-in, and predates every Round 1-3 commit --
none of them touched it.

Full detail -- the exact unsafe path, why label filtering alone is
insufficient, the new `vision_click_is_safe()` structural authority gate,
the positive low-risk whitelist, the blocked categories, and the adversarial
matrix -- is in the dedicated implementation note,
`docs/security/phase0-b1-vision-fallback-authority.md`. Summary: a new
Python-side, DOM-structural pre-click gate (`vision_click_is_safe()`)
normalizes the vision-selected candidate to its nearest actionable ancestor
and default-denies anything not positively recognized as a low-risk
opener/navigation control -- native and default-submit controls are blocked
unconditionally, regardless of label, "page" classification, or "why"
field; custom-JavaScript-driven controls are blocked by the same
default-deny (their `onclick` content is never introspected, since its
absence of `type="submit"` is not proof of safety). `safe_to_click_for_claude()`
remains an additional filter, not a replacement for this gate -- both must
now agree before any click.

### Validation

- `tests/test_submission_firewall.py` (including 22 new tests for this
  round): **80 passed** in 104.02s.
- `tests/test_authority_boundary.py`: **17 passed** in 23.53s -- unaffected,
  confirming this round did not touch `submission_step_finality()`.
- Original P0-B1 targeted suite (now including the 22 new vision-authority
  tests): **220 passed** in 298.52s.
- PageAgent/review-step/navigation suites (one pre-existing test,
  `test_looking_clicks_what_claude_points_at`, updated -- its fixture used a
  bare `role="button"` div with a custom `onclick`, a shape now structurally
  indistinguishable from a dangerous custom-submit control and correctly
  default-denied; the fixture was changed to a real anchor, which the
  positive whitelist recognizes, preserving the test's original intent of
  proving the click mechanism itself still works): **92 passed** in 174.79s.
- Full suite, cache cleared first: **`18 failed, 2046 passed, 3 skipped, 1
  error`** in 753.95s (0:12:33). The `+22` over the prior `2024 passed` is
  exactly this round's new tests (0 removed). All 18 failing node IDs plus
  the 1 collection error compared by exact name against the established
  baseline: identical set, zero new regressions.

## Finding 6: CLOSED (narrowed to the one vetted structural signal, across both the PageAgent step-navigation path and the vision/model click-fallback path) -- as of Round 4, superseded for the vision path by Round 5 below

Both of the two distinct paths this engagement found capable of performing a
potentially final application action now require positive, independently
verified, DOM-structural evidence before clicking outside the verified
final-submit gateway: `PageAgent.press_next()`'s step-navigation decision
(Rounds 1-3, `submission_step_finality()`) and the vision/model click
fallback's candidate-click decision (Round 4, `vision_click_is_safe()`).
Neither path's authority rests on the model's or the AI's own self-report,
and browser-side containment is defense in depth for both, never the sole
protection.

---

## Round 5: Round 4's own allowlist was still unsafe -- the fix is architectural

**Commit under repair:** `d3ce1fb` (Round 4's `vision_click_is_safe()` gate)

A fifth closure review found that Round 4's DOM-structural allowlist --
`aria-haspopup`/`aria-controls`/`aria-expanded`, a recognized opener role
(`tab`, `menuitem`, `combobox`, ...), a real non-`javascript:` anchor href
-- was itself still insufficient, for a reason no additional allowlist entry
could fix: every one of those signals describes *UI semantics*, and none of
them can prove the candidate's own `onclick` handler does not also call
`requestSubmit()` on an unrelated form. `<button type="button"
aria-haspopup="listbox" onclick="document.getElementById('app').requestSubmit()">`
is exactly as irreversible as a bare `<button type="submit">`, and Round
4's own code returned `"ALLOW"` for it, because `aria-haspopup`'s presence
was read as proof of safety. The same gap applies identically to
`aria-expanded`, `aria-controls`, `role="tab"`, `role="menuitem"`,
`role="combobox"`, and a normal anchor `href`. Generic page metadata is
useful for discovery; it is not irreversible-action authority, and no
third allowlist attempt -- narrower, broader, or differently shaped -- would
close this the way the first two attempts (Rounds 1 and 2's progressbar
heuristics, Round 4's opener-role heuristic) each failed to close their own
version of the same problem.

### The architectural correction

Rather than add a fourth allowlist, the vision/model fallback's click
authority is removed outright. `browser_automation._look_and_act_locked()`
no longer resolves or clicks any candidate at all: it still takes the
screenshot, calls `claude.read_page()`, and logs/returns the model's own
`"page"` classification and `"click"` label -- the observation step -- but
it always returns `clicked=False`. `vision_click_is_safe()`,
`_VISION_CLICK_AUTHORITY_SCRIPT`, `safe_to_click_for_claude()`, and
`_NEVER_CLICK` are removed entirely: each existed only to decide whether
*this* function's own click should proceed, and that click no longer
happens, so keeping any of them would be dead, misleadingly
authority-shaped code. The deterministic mechanisms this project already
has -- `PageAgent.press_next()`'s step-navigation authority
(`submission_step_finality()`), the field/dropdown helpers, and the
verified final-submit gateway -- remain the only paths that may ever act on
the page. For P0-B1, the resulting compatibility cost (the vision fallback
can no longer click anything it identifies, including controls that really
are harmless) is accepted explicitly, per instruction, rather than invented
around with another generic rule.

### Tests

`tests/test_submission_firewall.py`'s vision section was substantially
rewritten, not incrementally patched: a single parametrized adversarial
matrix now covers both Round 4's original shapes (native/default/custom
submit controls, A-K) and the new "innocent-looking semantics plus a hidden
submit" shapes this round's review named (`aria-haspopup`, `aria-expanded`,
`aria-controls`, `role="tab"`, `role="menuitem"`, `role="combobox"`, and a
normal anchor `href`, each paired with an `onclick` that calls
`requestSubmit()` on an unrelated form) -- 17 cases in total, each proving
the underlying control is genuinely dangerous via a direct click first, then
proving `look_and_act()` refuses it with `_submission_guard = None`. A
second parametrized test (11 labels, including both final-sounding and
innocuous-sounding words) proves the refusal is architectural, not
vocabulary-based. A new positive test proves the fallback still reads the
page, calls the model, and returns its classification -- it is an observer,
not disabled outright -- using a fixture whose own side effect is
deliberately harmless, specifically to show that even a provably-safe
control is still not clicked, because the decision no longer depends on
evidence about the specific candidate at all.

Three tests whose entire purpose was validating the now-removed
`safe_to_click_for_claude()` directly
(`test_never_clicked_on_claudes_say_so`, `test_ordinary_steps_are_allowed`,
`test_apply_is_allowed_only_from_a_posting` in
`tests/test_finding_the_way.py`) were removed, not weakened: the function
they tested no longer exists, and the behavior they checked ("Apply only
opens from a posting") is independently and unaffectedly enforced by
`page_agent.py`'s own deterministic `posting_plan()`/`profile_forward()`,
which never called `safe_to_click_for_claude()` in the first place. One
assertion inside `test_update_profile_moves_on_and_finish_later_never_does`
that also called the removed function directly was dropped, and the test
renamed to `test_update_profile_moves_on` to match what it still checks.
`test_looking_clicks_what_claude_points_at` (already once rewritten in
Round 4 to use a real anchor) is rewritten again, per the task's own
explicit permission, to
`test_looking_identifies_what_claude_points_at_but_never_clicks_it` --
asserting the model's `"page"` kind is still returned and the candidate is
identified, but that it is never clicked, using the same deliberately
harmless fixture to make the point unambiguous.

### Validation

- `tests/test_submission_firewall.py` + `tests/test_finding_the_way.py`
  together: **125 passed** in 138.01s.
- `tests/test_authority_boundary.py`: **17 passed** in 19.57s -- unaffected.
- Original P0-B1 targeted suite: **227 passed** in 235.72s (a net `+7` over
  the prior round's 220, from the matrix growing by 7 cases net and the
  label list by 3, offset by 3 removed "may still click" tests -- the pass
  count intentionally changed here, per instruction, rather than being held
  to the old figure).
- PageAgent/review-step/navigation suites: **73 passed** in 142.52s (down
  from 92, by exactly the 19 tests removed for validating the now-deleted
  `safe_to_click_for_claude()` -- an intentional, accounted-for reduction,
  not a silent regression).
- Full suite, cache cleared first: **`18 failed, 2034 passed, 3 skipped, 1
  error`** in 838.44s (0:13:58). `2034 = 2046 - 19 + 7`: the 19 removed
  `safe_to_click_for_claude()`-specific tests and the 7 net new
  vision-matrix tests, both accounted for above. All 18 failing node IDs
  plus the 1 collection error compared by exact name against the
  established baseline: identical set, zero new regressions.

## Finding 6: CLOSED (one authority model, not two)

No second click-authority system remains anywhere in the repository.
`browser_automation._look_and_act_locked()` can no longer directly invoke a
generic, model-selected `el.click()` under any circumstance -- not for a
native submitter, not for a default-submit button, not for a custom
`requestSubmit()` control, and not for a control carrying any opener, tab,
menu, combobox, or navigational-link semantic, however that control's own
`onclick` is wired. `PageAgent.press_next()`'s step-navigation authority
(`submission_step_finality()`) and the verified final-submit gateway
(`apply_flow.submit_verified()` through `begin_submission_dispatch()`)
remain the only two paths that may ever act on an irreversible application
control, exactly as before this round -- this round touched neither of them.
