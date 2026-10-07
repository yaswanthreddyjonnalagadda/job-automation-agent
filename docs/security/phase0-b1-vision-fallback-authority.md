# Phase 0-B1 — Vision/Model Click-Fallback Authority Fix

**Branch:** `fix/submission-replay-and-containment-hardening`
**Starting commit:** `c32fedfa9ae12ccac1fa4305fd56668a2be3a694`
**Finding closed:** the vision-fallback instance of Finding 6
(`phase0-b1-comprehensive-final-closure-review.md`, Section 6b)

> **Superseded, 7 October 2026.** The DOM-structural allowlist described
> below (committed as `d3ce1fb`) was itself found insufficient by a further
> closure review: `aria-haspopup`/`aria-controls`/`aria-expanded`, a
> recognized opener role, and a real anchor `href` each describe UI
> semantics, and none of them can prove the candidate's own `onclick`
> handler does not also submit the application elsewhere on the page. The
> fix is architectural, not another allowlist: the vision/model fallback no
> longer clicks any generic, model-selected candidate at all. See "Round 2:
> removing click authority outright" below, which is the current,
> superseding state of the code. The content immediately below this notice
> is kept as an accurate historical record of what the first round actually
> did and why; it no longer describes the code as it stands.

## The original unsafe path

`browser_automation._look_and_act_locked()` is used only when the ordinary
DOM-reading flow finds nothing to fill and nothing to press. It screenshots
the page, asks Claude what to click, and clicks whatever label Claude names:

```
Claude screenshot interpretation
  -> model returns {"page": ..., "click": "<label>", "why": ...}
  -> safe_to_click_for_claude(page, label)   # a label-text filter
  -> candidate located via get_by_role/get_by_text/get_by_label
  -> el.click(...)
  -> ONLY AFTER the click: SubmissionGuardV0 denial-count checked
```

`safe_to_click_for_claude()` is a vocabulary filter: it rejects a short
`_NEVER_CLICK` regex, attestation-looking labels, and labels matching
`safety.is_submit_label()` (unless the page is a job posting and the label
starts with "apply"). None of this inspects the resolved DOM candidate at
all -- it reasons entirely from the string Claude returned. The project's
own pre-existing test,
`tests/test_submission_firewall.py::test_vision_selected_control_cannot_bypass_the_guard`,
already demonstrated the consequence: force `safe_to_click_for_claude()` to
return `True` (simulating an unrecognized label on a real `type="submit"`
button) and the only thing left standing was the browser-side
`SubmissionGuardV0`'s post-click denial check.

This path is not a rare opt-in. `apply.py:run_one()` defaults
`auto: bool = True`; `apply.py:main()` computes
`run_one(url, auto=auto or True, ...)` -- `auto or True` is unconditionally
`True` regardless of whether `--auto` was passed. `web_ui.py` invokes
`apply.py <url>` with no flags, so every dashboard-triggered run reaches
`apply_flow.py`'s `main()` with `args.auto` set, which is the branch
containing the `look_and_act()` call. Per the owner's explicit instruction
for this fix, that `auto or True` expression itself is **not** changed here
-- the fix makes the reachable path safe, rather than trying to make it
unreachable.

## Why label filtering alone was insufficient

A label is not a reliable proxy for what a control actually does. The same
label ("Continue", "Finish", "Done", ...) can sit on a harmless navigation
link or on a `<button type="submit">` whose `onsubmit` sends the
application; the same is true in the other direction (an unusual label like
"Proceed" or "Finalize" could sit on either). `safe_to_click_for_claude()`'s
vocabulary list can always be defeated by a label it has not been taught,
and no vocabulary list can be exhaustive against every ATS's own wording.
The model's `"page"` classification and `"why"` field are equally
unreliable as authority -- they describe what the model *believes* it is
looking at, not what the DOM structurally is.

## The new Python-side pre-click structural authority

`browser_automation.vision_click_is_safe(page, locator) -> bool`, backed by
`_VISION_CLICK_AUTHORITY_SCRIPT`, runs entirely inside the browser against
the actual resolved candidate -- never the model's label, page kind, or why
field:

1. **Normalize** the candidate to its nearest actionable ancestor
   (`leaf.closest('button, input, a, [role="button"], [role="tab"], ...')`),
   so a `<span>`/icon/text node Claude's label happened to match is
   classified as the `<button>` (or other actionable element) that actually
   contains it, not as itself.
2. **Block unconditionally**, regardless of label or form association:
   - `<input type="submit">` / `<input type="image">`
   - `<button type="submit">`
   - a `<button>` with no explicit `type`, associated with a form (by
     nesting **or** by its own `form=""` attribute -- both already resolved
     correctly by the element's native `.form` property, so no manual
     attribute/ancestor walking is needed) -- the HTML-spec default-submit
     behavior.
3. **Allow** only when the normalized candidate positively carries one of a
   short, structural, text-independent set of signals:
   - `aria-haspopup`, `aria-controls`, or `aria-expanded` (opener semantics)
   - `role="tab"`, `"menuitem"`, `"menuitemradio"`, `"menuitemcheckbox"`,
     `"combobox"`, `"option"`, or `"switch"`
   - a real `<a>` with a non-empty, non-`javascript:` `href`
4. **Default-deny everything else.** A bare `<button type="button">`, a bare
   `[role="button"]`, a custom control wired to submit via its own
   JavaScript (`onclick="...requestSubmit()"` and any equivalent), and a
   detached control outside the form it targets are none of them positively
   recognized -- so all of them resolve to the same `"BLOCK"` the function
   returns on any exception. The function never reads or pattern-matches an
   `onclick` attribute's content at all; the absence of `type="submit"` is
   never treated as evidence of safety.

This is deliberately an allow-list, not a deny-list: new, unanticipated
custom-submit shapes are refused by default rather than requiring the deny
list to be taught every variant in advance.

## Integration

`_look_and_act_locked()` now calls `vision_click_is_safe()` immediately
after `safe_to_click_for_claude()`'s own check and before
`el.scroll_into_view_if_needed()`/`el.click(...)`. Both checks must pass --
an AND, not an OR -- and `safe_to_click_for_claude()` is otherwise
unchanged; it remains exactly what it always was, an early text-based
filter, never promoted to being the authority. `self.protect_submission(page)`
and the existing guard denial-count check after the click are both left in
place unchanged: `SubmissionGuardV0` remains defense in depth, now backing
up a Python authority decision instead of standing in for one.

## Allowed vs. blocked, concretely

| Candidate | Structural reason | Result |
|---|---|---|
| `<button type="submit" aria-label="Continue">` | native submitter | BLOCK |
| `<button>Continue</button>` inside a `<form>` | default-submits (no explicit type) | BLOCK |
| `<input type="submit">` / `<input type="image">` | native submitter | BLOCK |
| `<button form="app" type="submit">` (physically outside the form) | `form=""` association resolved via `.form`, native submitter | BLOCK |
| `<button type="submit"><span>Continue</span></button>`, vision resolves the `<span>` | normalized to the enclosing button | BLOCK |
| `<button type="button" onclick="...requestSubmit()">` | no positive opener/nav signal; `onclick` content never read | BLOCK |
| `<div role="button" onclick="...requestSubmit()">` | `role="button"` is not in the allow-list | BLOCK |
| A `type="button"` control outside the form it targets via `onclick` | same default-deny; detachment from the form changes nothing | BLOCK |
| An icon/SVG leaf inside a `type="submit"` button | normalized to the enclosing button | BLOCK |
| `<button aria-haspopup="listbox">` | positive opener signal | ALLOW |
| `<button role="tab">` | positive role | ALLOW |
| `<a href="/jobs">View Jobs</a>` | real, non-JavaScript href | ALLOW |

## Tests

All in `tests/test_submission_firewall.py`, through the real
`JobApplicationAssistant.look_and_act()`:

- `test_vision_selected_control_cannot_bypass_the_guard` (existing,
  assertion updated): the real, armed `SubmissionGuardV0` is still present,
  but Python's own `vision_click_is_safe()` now refuses the click before it
  is ever attempted, so the guard records no denial at all. This is
  strictly safer than the prior behavior (refusal moved from the browser
  layer to Python), not weaker -- the same shift already made once before in
  `test_page_agent_plan_does_not_authorize_submit` for the
  `submission_step_finality()` path.
- `test_vision_authority_refuses_with_no_browser_guard_at_all` (new): proves
  the exact same dangerous control is refused with `_submission_guard = None`
  and `protect_submission` a no-op -- no guard instance, no init script, no
  listeners, no denial counter -- after first proving a direct click on the
  same fixture really fires `window.submitted`.
- `test_vision_fallback_blocks_every_submission_capable_shape_with_no_guard`
  (new, 10 parametrized cases: A, B, C, D, E, F, G, H, I, K from the review's
  adversarial matrix -- native submit with a misleading label, default
  button in a form, `input[type=submit]`, `input[type=image]`, external
  `form=` association, a child `<span>` inside a submit button, a custom
  `type="button"` control calling `requestSubmit()`, a custom
  `[role=button]` doing the same, a detached outside-form trigger, and an
  SVG icon leaf inside a submit button): each proves the underlying control
  is genuinely dangerous via a direct click first, then proves
  `look_and_act()` refuses it with the guard entirely absent.
- `test_vision_fallback_structure_not_vocabulary_blocks_every_unusual_final_label`
  (new, 8 parametrized labels: Finish, Done, Complete, Proceed, Confirm,
  Continue, Send, Finalize, all on the same `type="submit"` control): proves
  the refusal is driven by structure, not by recognizing any particular
  word.
- `test_vision_fallback_may_still_click_a_safe_dropdown_opener`,
  `test_vision_fallback_may_still_click_a_safe_tab`,
  `test_vision_fallback_may_still_click_a_safe_navigational_link` (new):
  prove the fallback is not disabled outright -- each positively-recognized
  low-risk category still results in a real click.

One pre-existing test was updated, not weakened:
`tests/test_finding_the_way.py::test_looking_clicks_what_claude_points_at`
used a bare `<div role="button" onclick="...">` fixture -- a shape now
structurally indistinguishable from a control that submits via its own
JavaScript (case G/H above), and therefore correctly refused by the new
default-deny model. The fixture was changed to a real `<a href="#apply">`
(the positively-recognized navigational-link case), preserving the test's
original intent -- proving the basic "Claude names it, the agent finds and
clicks it" mechanism still works -- without relying on a now-ambiguous shape.

## Audit: other model/vision-driven click entry points

Searched the whole repository for `look_and_act(`, `_look_and_act_locked(`,
`read_page(`, and any OCR/recovery/visual-fallback click pattern. Exactly
one production call site of `look_and_act()` exists
(`apply_flow.py:1775`, inside `main()`'s `--auto`-mode loop), and exactly one
production call site of `claude.read_page()` exists (inside
`_look_and_act_locked()` itself). No other model- or vision-driven direct-click
path exists in the repository. This is the only entry point this fix needed
to cover, and the only one that exists to cover.

## Interaction with PageAgent's step-navigation authority

The vision fallback does not implement, duplicate, or need its own version
of Workday finality logic. `vision_click_is_safe()` blocks every
submission-capable shape unconditionally, regardless of whether the page
happens to carry a wizard marker -- so the vision fallback simply never
operates on the application's own submit-capable step-navigation controls
at all. `PageAgent.press_next()` and `submission_step_finality()` remain the
one authoritative decision path for "is this control the application's
final step," exactly as the comprehensive review's Section 11 preferred.

## Validation

- `tests/test_submission_firewall.py`: **80 passed** in 104.02s (58
  pre-existing + 22 new).
- `tests/test_authority_boundary.py`: **17 passed** in 23.53s -- unaffected,
  confirming `submission_step_finality()` was not touched.
- Original P0-B1 targeted suite (now including the 22 new tests):
  **220 passed** in 298.52s.
- PageAgent/review-step/navigation suites (including the one updated
  fixture): **92 passed** in 174.79s.
- Full suite, cache cleared first: **`18 failed, 2046 passed, 3 skipped, 1
  error`** in 753.95s (0:12:33). The `+22` over the prior `2024 passed` is
  exactly this round's new tests. All 18 failing node IDs plus the 1
  collection error compared by exact name against the established baseline:
  identical set, zero new regressions.

## Finding 6 (vision-fallback instance): CLOSED -- as of this round, superseded by Round 2 below

Model text cannot authorize a dangerous click; the model's `"page"`
classification cannot either (`vision_click_is_safe()` never receives it).
The browser guard is not required to stop native submitters, default form
buttons, or custom `requestSubmit()` controls -- each is refused with the
guard completely absent, proven fresh in this round's own tests. Nested
text/icon resolution cannot bypass classification (candidate normalization
resolves to the actionable ancestor before any decision is made). A
detached custom submit trigger is handled by the same conservative
default-deny, not by a special case. Low-risk UI controls (dropdown
openers, tabs, real navigational links) can still be clicked. No second
model/vision direct-click path exists anywhere in the repository. The final
submission gateway remains the one unchanged chain:
`apply_flow.submit_verified()` -> `assistant.click_verified_submit()` ->
`SubmissionGuardV0.submit_verified()` -> `begin_submission_dispatch()` ->
the guard's own authorized click -- confirmed unaffected, since this fix
does not touch `apply_flow.py`, `submission_guard.py`, `job_tracker.py`,
`db.py`, or `safety.py` at all.

---

## Round 2: removing click authority outright

**Commit under repair:** `d3ce1fb` (Round 1's `vision_click_is_safe()` gate)

### Why Round 1's own allowlist was still too permissive

A further closure review constructed:

```html
<form id="app" onsubmit="window.submitted=true; return false"></form>
<button type="button" aria-haspopup="listbox"
        onclick="document.getElementById('app').requestSubmit()">
  Country
</button>
```

and called `vision_click_is_safe()` directly against it. It returned
`"ALLOW"`, because the element carries `aria-haspopup` -- Round 1's own
positive signal for "this is a safe dropdown opener." But `aria-haspopup`
is an accessibility *state*, declared by whoever wrote the page's markup;
it says nothing at all about what the element's `onclick` handler actually
does, and a real `requestSubmit()` call sitting right next to it is
completely invisible to a check that only reads structural attributes.
The identical problem reproduces for `aria-expanded`, `aria-controls`,
`role="tab"`, `role="menuitem"`, `role="combobox"`, and a normal anchor
`href` -- every one of Round 1's positive signals, without exception. No UI
semantic -- however specific, however narrowly chosen -- can be made to
imply "and this element's own JavaScript does not submit a form," because
nothing stops a page's author from attaching exactly that JavaScript to an
element that also happens to look, structurally, like a safe opener. This
is not a gap in which attributes were chosen; it is a property of trying
to infer "has no submission side effect" from "has this other, unrelated
attribute" at all.

### The fix: remove the click, not the allowlist

There is no fourth or fifth list of attributes that closes this, because
the problem is categorical, not enumerable. The fix removes
`_look_and_act_locked()`'s ability to click a generic, model-selected
candidate entirely:

- `vision_click_is_safe()`, `_VISION_CLICK_AUTHORITY_SCRIPT`,
  `safe_to_click_for_claude()`, and `_NEVER_CLICK` are deleted outright --
  each existed solely to gate a click this function no longer performs, and
  leaving any of them in place, unused, would read as ongoing authority
  that no longer exists.
- `_look_and_act_locked()` keeps exactly its observation half: CAPTCHA
  checks, the screenshot, `claude.read_page()`, and logging/returning the
  model's own `"page"` kind and `"click"` label. It always returns
  `clicked=False`.
- No candidate is resolved, normalized, scrolled to, or clicked. There is
  nothing left in this function for a future attribute, role, or label to
  be checked against, because there is no click decision left to gate.

The deterministic mechanisms this project already has -- `PageAgent`'s own
control-matching and `submission_step_finality()`-governed step-navigation
authority, the field/dropdown helpers, and the verified final-submit
gateway -- remain the only paths that may ever act on the page. Per
instruction, this round does not build a new routing mechanism to hand a
vision-identified candidate to those deterministic paths; for P0-B1, when
the vision fallback is reached at all (meaning the deterministic reading
already found nothing to fill or press), it now simply reports what it
saw and takes no action, leaving the page for normal recovery or human
handoff. Restoring the lost compatibility for a specific, real control
shape is left to a future, individually-vetted ATS/UI-specific
deterministic adapter -- never another generic heuristic.

### Tests

See `docs/security/phase0-b1-finding6-closure-tightening.md`'s Round 5 for
the full test inventory. In summary: a single 17-case parametrized matrix
in `tests/test_submission_firewall.py` now covers Round 1's original
native/default/custom-submit shapes together with the new
semantics-plus-hidden-submit shapes this round's review named
(`aria-haspopup`, `aria-expanded`, `aria-controls`, `role="tab"`,
`role="menuitem"`, `role="combobox"`, and a normal `href`, each paired
with an `onclick` that calls `requestSubmit()`), every case proving the
control is genuinely dangerous via a direct click first, then proving
`look_and_act()` refuses it with `_submission_guard = None`. A second
matrix (11 labels, both final-sounding and innocuous-sounding) proves the
refusal is architectural, not vocabulary-based -- it does not matter what
the label says, because nothing is ever clicked regardless. A new positive
test proves the fallback still reads, classifies, and reports -- using a
fixture whose own side effect is deliberately harmless, so a pass proves
the point even for a candidate that really would have been safe to click.
Tests that existed only to validate the now-deleted
`safe_to_click_for_claude()` directly were removed, not weakened --
`tests/test_finding_the_way.py::test_looking_clicks_what_claude_points_at`
is rewritten to assert the new contract (observed and identified, never
clicked) rather than preserving the old "vision directly clicks it"
expectation the task explicitly authorized changing.

### Validation

- `tests/test_submission_firewall.py` + `tests/test_finding_the_way.py`:
  **125 passed** in 138.01s.
- `tests/test_authority_boundary.py`: **17 passed** in 19.57s -- unaffected,
  confirming `submission_step_finality()` was untouched by this round too.
- Original P0-B1 targeted suite: **227 passed** in 235.72s (a net `+7`,
  intentional, from the matrix growing and three now-invalid "may still
  click" tests being removed).
- PageAgent/review-step/navigation suites: **73 passed** in 142.52s (down
  from 92 by exactly the 19 tests that validated the deleted
  `safe_to_click_for_claude()` directly -- an intentional, accounted-for
  reduction).
- Full suite, cache cleared first: **`18 failed, 2034 passed, 3 skipped, 1
  error`** in 838.44s (0:13:58). All 18 failing node IDs plus the 1
  collection error compared by exact name against the established
  baseline: identical set, zero new regressions.

## Finding 6 (vision-fallback instance): CLOSED (architecturally, not by allowlist)

`browser_automation._look_and_act_locked()` can no longer directly invoke a
generic, model-selected `el.click()` under any circumstance. There is
exactly one model/vision click entry point in the repository (confirmed by
repository-wide search, unchanged from Round 1's audit), and it no longer
clicks anything. `PageAgent.press_next()`'s step-navigation authority and
the verified final-submit gateway remain the only two paths that may ever
act on an irreversible application control -- neither was touched by this
round.
