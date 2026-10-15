# Phase 0-B1 — Hardening Follow-up (post Pass 4 independent review)

## Scope

A narrowly scoped follow-up, authorized after human approval of Phase 0-B1
based on the Pass 4 implementation report and its independent verification.
Covers only the two residual gaps the independent review found and the
Pass 4 implementation report did not disclose:

1. Submission guard listener liveness after document replacement.
2. Hidden confirmation elements.

No P0-B2 work was started. No unrelated refactor was made. The canonical
submission-identity redesign (Pass 4's Finding 2 fix) was not touched --
no regression made that necessary.

---

## Residual 1 — Submission guard listener liveness after document replacement

### Root cause

`SubmissionGuardV0`'s injected script attached its click/keydown/submit
interception as inline closures passed directly to
`window.addEventListener(...)`, once, the first time the script ran on a
document. A `document.open()`-class content replacement -- confirmed
directly, not assumed, to be what Playwright's `page.set_content()` uses
internally -- removes event listeners registered this way, while leaving
`window.__jaaSubmissionGuardV0` (a plain property added via
`Object.defineProperty`, not a listener) and its closed-over `state` object
fully intact. The guard's own Python-side `activate()` only ever called
`.activate(token)` on whatever guard object currently existed; it never
re-attached anything. The result: the guard object kept answering
`activate()`/`authorize()` calls correctly, which gave every surface
signal of being "active," while the one thing that actually intercepted a
click -- the attached listener -- was gone. Object/API presence was not
proof of active containment.

### Implementation approach

Restructured `submission_guard.py`'s `_guard_script` so the three
listeners are named, stable function references (`onClick`, `onKeydown`,
`onSubmit`) wrapped by one `ensureListeners()` function, rather than
anonymous closures passed inline to `addEventListener`. `ensureListeners()`
is called once during first-time setup and is also called by `activate()`
itself on every invocation. This is safe to call repeatedly by
construction: `addEventListener` silently discards an attempt to register
the same function reference with the same capture-phase flag twice, so
calling it whether listeners are already live (no-op) or were silently
stripped (repair) is always correct and never creates a duplicate
listener or a duplicate denial event for one click.

The script's own idempotency guard changed from "if the object already
exists, do nothing" to "if the object already exists, call its persisted
`__ensureListeners` and return" -- `__ensureListeners` is exposed on the
frozen `api` object specifically so a later run of the same script (e.g.
triggered by Python re-evaluating it) can repair the *existing* closure's
listeners rather than attempting to create a second, independent guard
instance.

On the Python side, `_activate_frame()` now falls back to a full
`frame.evaluate(self._script)` re-injection if calling `.activate(token)`
throws (the rarer case: the guard object is missing from this frame
entirely, e.g. a frame the init script never reached) -- defense in depth
alongside the primary repair path (`activate()`'s own `ensureListeners()`
call), which covers the common case verified in this hardening (object
present, listeners stripped).

Verified, not assumed, before deciding what needed repairing: a diagnostic
script confirmed that `HTMLFormElement.prototype.submit`/`requestSubmit`
monkey-patches and plain object properties survive `page.set_content()`
unchanged; only `addEventListener`-registered listeners are removed. The
fix therefore touches only listener (re)attachment, not the prototype
patches, which needed no change.

### Files changed

`submission_guard.py` (`_guard_script`'s listener installation and
`activate()`; `SubmissionGuardV0._activate_frame()`'s fallback).

### Regression tests added (`tests/test_submission_firewall.py`)

- `test_document_replacement_after_activation_strips_the_listener` --
  confirms the vulnerability is real and reproducible: activation, then a
  `set_content()`-class replacement, with no further activation call,
  leaves the next click unguarded. This is the measured baseline for the
  next test.
- `test_reactivation_after_document_replacement_repairs_the_listener` --
  the actual fix: the same `activate()` call `protect_submission()`
  already makes on every `PageAgent` loop iteration repairs the stripped
  listener before the next interaction.
- `test_repeated_activation_creates_no_duplicate_listeners_or_denials` --
  five consecutive `activate()` calls followed by one click produce
  exactly one denial, not five.
- `test_a_genuine_final_control_is_still_blocked_after_listener_repair` --
  the Finding 1 structural default-deny rule (not just a
  label-matched/structural-type path) is confirmed still correct after a
  repair cycle.
- `test_normal_real_navigation_is_unaffected_by_the_liveness_repair` --
  the common case (a real navigation never strips listeners in the first
  place) is confirmed unaffected by this change.

Items 1-3 of the originally requested coverage list (normal real
navigation, popup created after guard construction, pre-existing page)
were already covered by Pass 4's own `test_popup_opened_after_the_guard_is_constructed_is_still_guarded`
and `test_page_that_already_existed_before_guard_construction_is_guarded`,
re-run and confirmed still passing rather than duplicated.

### Reproduction: before and after

Before this fix (the independent review's exact finding):

```
guard = SubmissionGuardV0(context)
page = context.new_page()
guard.activate(page)
page.set_content('<form><button type="submit" id="sb">Submit Application</button></form>')
page.locator("#sb").click()
# denials: []   <-- NOT BLOCKED
```

After this fix, the identical sequence with one additional `activate()`
call where `protect_submission()` already makes one in production:

```
guard.activate(page)   # the repair
page.locator("#sb").click()
# denials: [{'sequence': 1, 'kind': 'click', 'label': 'Submit Application'}]
```

### Targeted result

`tests/test_submission_firewall.py` (new tests only, via `-k`): `5 passed`.
Full file: `58 passed`. Combined with
`tests/test_submission_effect_state.py`, `tests/test_auto_submit.py`,
`tests/test_safety_rules.py`, `tests/test_confirmation_needs_evidence.py`,
`tests/test_phase2_primitives.py`, `tests/test_repeated_entries.py`:
`228 passed`.

---

## Residual 2 — Hidden confirmation elements

### Root cause

`submission_confirmed()`'s heading scan relied on `element.innerText` to
both extract text *and* implicitly encode visibility (the common
assumption being that `innerText`, unlike `textContent`, returns an empty
string for non-rendered content). Verified directly in this runtime,
rather than assumed from the specification: `innerText` on a `display:none`
element returned its full text content. A conditionally-rendered SPA
"success" template -- present in the DOM, hidden via CSS until a state
change reveals it, a common React/Vue pattern (`v-show`-style toggling
rather than mount/unmount) -- could therefore trigger a false `CONFIRMED`
before any real submission occurred, through the exact wording match
alone, independent of the originally-reported misleading-prose mechanism.

### Implementation approach

Added an explicit visibility filter before accepting any heading/alert/
status element's text, decided and documented rather than guessed:

- **Rejected** (self or an ancestor): `display: none` (detected via
  `element.getClientRects().length === 0`, which is empty precisely when
  the element or any ancestor has no layout box -- this already matches
  the pattern this file's own job-title card scan uses a few lines below
  the changed code), the HTML `hidden` attribute (`element.closest('[hidden]')`,
  covering an ancestor carrying the attribute as well as the element
  itself), `visibility: hidden`/`visibility: collapse` (via
  `getComputedStyle`, which correctly resolves CSS inheritance for this
  property), and full transparency (`opacity: 0`).
- **Explicitly not attempted** -- a documented policy decision, not a
  silent gap: off-screen positioning (e.g. `position:absolute;
  left:-9999px`) and zero-size-but-technically-laid-out elements.
  `getClientRects()` returns a non-empty, positioned rect for off-screen
  content (it has a real box, just off the visible viewport), so
  distinguishing "legitimately positioned real content" (a fixed banner,
  content inside a horizontally scrollable container) from "deliberately
  hidden off-screen" would require fragile, viewport/scroll-relative
  heuristics prone to false-negating real confirmations. `display:none`,
  the `hidden` attribute, and `visibility`/`opacity` toggling are the
  standard, simple mechanisms real SPA frameworks use for conditional
  visibility and are what this hardening targets; off-screen positioning
  remains an acknowledged, narrower residual limitation.

The safety direction is unchanged and was not weakened in either
direction: a missed real confirmation (e.g., one using an undetected
off-screen technique) still leaves the application `UNCERTAIN`, not
`CONFIRMED` -- the existing fail-closed fallback is untouched. No
confirmation phrase, heading selector, or matching logic was broadened to
compensate for the new filter; coverage was only narrowed on the
visibility axis.

### Files changed

`browser_automation.py` (`submission_confirmed()`'s heading-gathering
query).

### Regression tests added (`tests/test_safety_rules.py`)

- `test_a_hidden_confirmation_template_is_not_a_confirmation` (parametrized
  over nine cases): hidden `<h1>` via `display:none`, hidden
  `[role=alert]`, hidden `[role=status]`, a `display:none` *ancestor* with
  an otherwise-plain child, the HTML `hidden` attribute on the element and
  on an ancestor, `visibility:hidden`, `opacity:0`, and the specific SPA
  shape (a toggled ancestor hiding an explicitly `display:block` child
  template).
- `test_a_success_template_becomes_a_confirmation_once_actually_shown` --
  the SPA case this hardening targets directly: the identical element,
  `display:none` then toggled to `display:block`, is confirmed `False`
  before the toggle and `True` after, on the same page without a reload.

### Reproduction: before and after

Before this fix:

```python
page.set_content("<h1 style='display:none'>Thank you for applying!</h1>")
agent.submission_confirmed(page)  # True   <-- wrongly confirmed
```

After this fix, the identical page:

```python
agent.submission_confirmed(page)  # False
page.evaluate("document.querySelector('h1').style.display = 'block'")
agent.submission_confirmed(page)  # True  -- once genuinely shown, still recognized
```

### Targeted result

`tests/test_safety_rules.py` (new tests only, via `-k`): `10 passed`. Full
file combined with `tests/test_confirmation_needs_evidence.py`:
`70 passed`.

---

## Combined validation

### Targeted (all files touched or most exposed to either change)

`tests/test_submission_firewall.py tests/test_submission_effect_state.py
tests/test_auto_submit.py tests/test_safety_rules.py
tests/test_confirmation_needs_evidence.py tests/test_phase2_primitives.py
tests/test_repeated_entries.py`: **228 passed**.

### Collection

`2012 -> 2027` node IDs (`+15`, exactly the five Residual-1 and ten
Residual-2 tests added); the same one pre-existing collection error
(`tests/test_ant_select.py` importing the absent `_somewhere_profile`,
unrelated to this work and not modified by it).

### Full suite

`python -m pytest -q -rs -n auto`, cache cleared beforehand so the
failure list could not contain entries accumulated from earlier, unrelated
runs (the same precaution used throughout this engagement):

**`18 failed, 2006 passed, 3 skipped, 1 error` in 862.25s.**

### Comparison against the independently-verified Pass 4 baseline

Baseline: `18 failed, 1991 passed, 3 skipped, 1 error`.

`1991 + 15 (new tests, all passing) = 2006` passed -- exact. Failed (`18`),
skipped (`3`), and error (`1`) counts are unchanged.

Not accepted on arithmetic alone: the freshly-populated `lastfailed` cache
(cleared immediately before this run, so it cannot contain accumulated
entries) was read directly and every one of the 18 failing node IDs plus
the 1 collection error was compared, by exact name, against the specific
list the independent review itself verified for Pass 4. All 19 entries
match exactly -- the same 12 `tests/test_page_agent.py` behavior failures,
the same 6 `tests/test_failures_catalogue.py` parametrized reference
failures, and the same `tests/test_ant_select.py` collection error. No
entry disappeared, no new entry appeared, and no entry changed identity.

**New regressions introduced by this hardening pass: none.**

## P0-B2 started

NO.

## Stopping point

This hardening follow-up is complete: both residuals have a verified
root cause, a targeted fix, regression coverage proving the before/after
behavior, and a full-suite run showing no new failures against the
independently-verified baseline. Per the authorization, this stops here.
P0-B2 is not started and awaits explicit authorization.
