# Phase 0-B1 — Remediation Pass 4: Independent Review

## Review method

This review did not accept the Pass 4 implementation report's claims on
trust. For each finding, the actual production code was read, then an
independent reproduction was built and run -- separately from, and in
several cases going beyond, the regression tests the implementation report
cites -- including raw SQL inspection of durable state (bypassing the
tracker's own accessor methods), adversarial Playwright probes using
fixtures not in the existing test suite, and exact failing-node-ID
comparison for the full-suite claim rather than arithmetic reconciliation
alone. No production code was modified during this review. P0-B2 was not
started.

Two genuine residual gaps were found that the implementation report did not
disclose, in Findings 3 and 6. Both are reported in full below, including
why they are real and why they are assessed as not blocking.

---

## Finding 2 — Alias annexation permitting a second dispatch

**Classification: VERIFIED FIXED**

### Evidence inspected

`job_tracker.py`'s `_resolve_root`, `_identity_group`,
`record_submission_identity_alias`, and every submission-effect accessor
method, read line by line against the diff.

### Independent reproduction performed

1. **The exact named sequence**, with raw SQL inspection instead of relying
   on the tracker's own read methods: built `B_alias -> B`, annexed `B`
   beneath `A`, dispatched `A`. Queried `submission_effects` and
   `submission_identity_aliases` directly via `sqlite3.connect()` -- exactly
   one row in `submission_effects` (`root-A`, `DISPATCHED`), exactly two
   alias rows, matching the expected graph. Called
   `begin_submission_dispatch("B_alias")` directly: raised
   `RuntimeError: Submission replay blocked by durable state DISPATCHED`.
   Re-inspected `submission_effects` via raw SQL afterward: still exactly
   one row -- the blocked call did not create a second one. `A`, `B`, and
   `B_alias` all read back `DISPATCHED` via the public accessor.
2. **Dispatch through a middle member before further annexation**: built a
   four-level chain (`alias3 -> alias2 -> alias1 -> root`) via direct row
   insertion (bypassing the public API, to test the resolver against an
   arbitrary stored topology), dispatched through `alias2` (not the root),
   and confirmed all four members read `DISPATCHED`. Then attempted to
   annex that already-dispatched `root` beneath a new `root2`: refused
   (`own_history` check), `root2`'s group stayed `{root2}`, and the
   original four-member group was unaffected -- no partial/asymmetric
   corruption from a refused merge.
3. **Merge-order independence**: built the same group in reverse order
   (annex first, add descendants after). Every new alias flattened directly
   onto the then-current root; the resulting group was complete and
   consistent regardless of construction order.
4. **Two identities that already both have durable state**: dispatched two
   independent keys, then attempted to merge one into the other. Refused;
   raw SQL confirmed zero alias rows were created; both groups remained
   `{appX}` and `{appY}` respectively.
5. **Alias insertion after dispatch** (e.g., a redirect discovered during
   post-dispatch confirmation): recorded a new alias under an
   already-`DISPATCHED` root. The new alias immediately read back
   `DISPATCHED` and was itself refused a dispatch.
6. **A wide tree** (one root, three direct children, one grandchild hung off
   a child): every member's group resolved to the complete, identical
   five-member set, confirmed from five different starting points.
7. **A manually constructed cycle** (`x -> y -> z -> x`, inserted via raw
   SQL -- not reachable through the public API given its existing guards,
   but tested as a defensive backstop): `_resolve_root`/`_identity_group`
   did not hang, recurse unboundedly, or crash; `begin_submission_dispatch`
   wrote exactly one row and did not duplicate state. This path is not
   reachable through normal operation; it was tested only to confirm the
   hop-bounded walk fails safe rather than fails open or fails hard if the
   table is ever corrupted by something outside this code.
8. **Concurrency**: two threads called `begin_submission_dispatch` on
   `concA` and `concB` (an established alias pair) simultaneously. Exactly
   one succeeded, the other was blocked, and exactly one row existed in
   `submission_effects` afterward -- the `BEGIN IMMEDIATE` transaction
   correctly serializes canonical resolution and the state write together,
   even when two callers use different literal keys for the same identity.

### Result

Every scenario above produced the correct, safe outcome. The required
invariant -- *every member of one merged submission identity observes and
mutates exactly one canonical durable submission-effect state* -- held in
every test, including ones materially more adversarial than the
implementation report's own regression tests (concurrent dispatch via
different aliases, a manually-forced cycle, merge-order reversal, and raw
multi-row SQL inspection).

### Residual risk

None identified beyond the already-disclosed, inherent limitation that
identity continuity depends on the system having actually observed and
recorded the relevant URL -- a URL variant never visited by any run remains
its own, uncorrelated identity (by design; the alternative requires fuzzy
matching, explicitly out of scope).

### Blocks P0-B2

No.

---

## Finding 1 — Unrecognized-label submit bypass

**Classification: VERIFIED FIXED**

### Evidence inspected

`submission_guard.py`'s `finalControl`, `unclassifiedFinalCandidate`,
`soleRemainingAction`, `opensSomething`; `safety.py`'s `SUBMIT_LABEL_RE` and
`SAFE_UTILITY_LABEL_RE`.

### Independent reproduction performed

A fresh Playwright script (not the existing test suite) exercised, each on
its own page/context combination (to avoid a `set_content()` reuse artifact
discovered and controlled for during this review -- see Finding 3):

- An unrecognized English label ("Wrap Up My Application") on
  `type="button"` with a custom `onclick` handler: blocked.
- An untranslated label in a language with no vocabulary entry at all
  ("Invia", Italian for "Send"): blocked -- confirming the structural rule,
  not the vocabulary, is what catches this.
- `type="submit"` with a recognized label (sanity check): blocked.
- No `type` attribute at all (defaults to `submit` per the HTML spec) with
  an unrecognized label: blocked.
- A legitimate `type="button"` "Next": allowed.
- "Save and Finish Later": allowed.
- A dropdown/disclosure control (`aria-expanded`): allowed.
- Bare "Done" as the sole candidate inside an actual `<form>`: blocked --
  confirming the structural rule still catches a genuine final use of a
  word deliberately kept out of `SUBMIT_LABEL_RE` for the Done/Complete
  regression fix (see that section below).
- A non-`<button>` element (`<div role="button">`) with an unrecognized
  label: blocked.
- A disabled control: could not be clicked at all (Playwright-level), so
  the question of guard behavior does not arise.

No alternate structure was found that reaches a final submission without
being caught: the three paths checked (recognized label, structural
submitter type, and the new sole-remaining-candidate default-deny) together
cover every button/role=button variant tried.

### Result

Matches the implementation report. All adversarial probes behaved
correctly; no bypass found.

### Residual risk

Icon-only or otherwise unlabeled controls remain uncovered by design (a
pre-existing, disclosed limitation, not newly introduced): the default-deny
rule requires a non-empty accessible label, specifically to avoid sweeping
up icon-only utility buttons (confirmed safe in the implementation report's
own testing). This is an acknowledged gap, not a silent one.

### Blocks P0-B2

No.

---

## Finding 3 — Inactive guard on new tabs/popups

**Classification: PARTIALLY FIXED -- one residual gap found, not disclosed
in the implementation report**

### Evidence inspected

`submission_guard.py`'s `SubmissionGuardV0.__init__`, `activate`,
`_activate_new_page`, and the guard script's `state` object and event
listener installation.

### Independent reproduction performed

**The realistic, named scenario (popup opened via `window.open()`, then a
real cross-origin navigation -- exactly how every real ATS external-link
popup behaves) was reproduced successfully, confirming the fix:** a guard
constructed before the popup existed, a popup opened via `window.open()`
with no explicit `activate()` call anywhere in the test, a real navigation
to a routed cross-origin URL, and a submit click -- correctly blocked, one
denial recorded. The same was confirmed for a page that already existed
and had already navigated *before* `SubmissionGuardV0()` was even
constructed.

**While probing further** (per this review's instruction to confirm the
guard is "actually active on the resulting document, not merely that an
event listener exists in the Python code"), a page whose content was set
via `page.set_content()` -- rather than a real navigation -- was tested
with the exact same setup (guard constructed first, page created after,
relying on the same auto-activation path Finding 3 added). The click was
**not** blocked, despite `window.__jaaSubmissionGuardV0` existing on the
page and `activate()` returning without error when called both
automatically and a second time explicitly, immediately before the click.

This was tracked down to a real, verified mechanism, not a test artifact:
`page.set_content()` does not create a new `window` object (confirmed with
a custom marker property that survives the call) and does not re-trigger
`add_init_script`'s injection (confirmed: the identical setup via
`page.goto()` with a routed response blocks correctly; via `set_content()`
on the same guard/page construction it does not). What it evidently does
is clear the window's existing event listeners -- consistent with the
`document.open()`-family behavior invoked internally -- while leaving
properties added via `Object.defineProperty` (such as
`window.__jaaSubmissionGuardV0` itself) intact. The result is a guard
object that still answers `activate()`/`authorize()` calls (so a check that
only confirms the object or its API calls succeed would wrongly read this
as "active"), but whose `window.addEventListener("click", ..., true)`
capture-phase listener -- the thing that actually intercepts a click -- is
gone, because nothing re-injects `self._script` on this path. Confirmed
directly with a raw `dispatchEvent` check: `defaultPrevented: False`, the
form's own `submit` listener fired, and `denialCount()` read `0`.

**This gap is not unique to Finding 3's new code.** The pre-existing
`protect_submission()` call site (`browser_automation.py`, unchanged by
this pass) has called `guard.activate(page)` on every `PageAgent` loop
iteration since Pass 1; `activate()` only ever calls `.activate(token)` on
whatever guard object currently exists, never `self._script` again. If a
real page content-replacement technique ever strips the listener the same
way `set_content()` does, neither the pre-existing mechanism nor this
pass's new auto-activation would detect or repair it -- both read "the
object answers" as "the page is protected," which this review's probing
showed is not the same fact.

### Residual risk and why it is assessed as not blocking

The realistic attack surface Finding 3 was written to close -- a popup or
pre-existing tab that then **navigates** to a real ATS page -- is fixed and
independently confirmed. The gap found requires content to arrive via a
`document.open()`-equivalent replacement (what `page.set_content()` uses)
rather than a browser-level navigation. Modern ATS SPAs update their DOM
through framework reconciliation (React/Vue re-rendering), not through
`document.write()`-style replacement, which is a legacy, largely deprecated
technique; this review found no evidence any of this project's own site
adapters or test fixtures rely on it for real page content. The practical
likelihood of a real ATS triggering this specific path is assessed as low,
but it is a demonstrated, reproducible gap, not a theoretical one, and it
is a pre-existing architectural property this pass inherited rather than
introduced (`protect_submission()`'s identical weakness predates Pass 4).

### Blocks P0-B2

No, but **recommended for a dedicated follow-up**: either have `activate()`
re-inject `self._script` unconditionally rather than only calling
`.activate()` on whatever may or may not still be listening (which requires
first addressing that the script's own `if (window.X) return;`
self-guard would currently block re-injection even when the listener is
gone but the property survives), or add an independent liveness check
(e.g., dispatch a synthetic, non-rendered probe click periodically and
confirm it is seen) rather than trusting object presence.

---

## Finding 4 — Tracking-parameter identity fragmentation

**Classification: VERIFIED FIXED**

### Evidence inspected

`jd_analyzer.py`'s `is_tracking_query_param`, `_TRACKING_QUERY_PREFIXES/
SUFFIXES/NAMES`, and its call sites in `job_tracker.py`/`db.py`.

### Independent reproduction performed

Tested `trk`, `fbclid`, `gclid`, `msclkid`, and two `*clid`-family
parameters not explicitly named anywhere in the implementation (`ttclid`,
`yclid`) -- all six collapsed to the same effect key as the bare URL,
confirming the suffix rule generalizes beyond the enumerated list as
claimed. Checked for over-normalization: two different Greenhouse
requisition IDs (`gh_jid=555` vs `gh_jid=556`) stayed distinct; an
unrecognized, non-tracking parameter (`candidate_ref=abc123`) was correctly
*not* stripped and produced a distinct key; `gh_src` stripping was confirmed
intentional (Greenhouse's own tracking-source parameter, not a job
identifier).

### Result

Matches the implementation report; no over-normalization found.

### Blocks P0-B2

No.

---

## Finding 5 — Login-host alias skipping

**Classification: VERIFIED FIXED**

### Evidence inspected

`apply_flow.py`'s `remember_progress` and `_remember_submission_identity_alias`.

### Independent reproduction performed

Built a full four-stage chain (posting -> a login/sign-in host URL -> an
ATS application URL -> a review URL) through `remember_progress()` calls,
exactly as a real run would make them. The login-host URL was present in
the bound identity's alias group, alongside the other two hops. Separately
confirmed `last_page_url` (the Resume/last-page UX mechanism) correctly
stayed on the review URL, not the login URL -- the two gates are
independently applied, not accidentally reunified. A later invocation
started directly from the login URL's own key was confirmed to find the
bound `DISPATCHED` state through `refuse_submission_replay()`.

Checked the instruction to "look for other URL filters that could still
silently omit identity-relevant transitions": `worth_returning_to()` no
longer gates alias recording at all (only `submission_effect_key_for_url`'s
own validation does, which rejects non-HTTP(S)/insufficiently specific
URLs such as `about:blank`). This means URLs `worth_returning_to()`
previously also excluded for other reasons (`/error`, `accounts.google.com`,
`/logout`) are now also recorded as aliases. Checked the
`accounts.google.com` OAuth case specifically: its state-token query
parameter is per-request-unique in practice, so it is not expected to
collide across different applications' OAuth hops; if it ever did, the
existing `already_aliased` first-binding-wins rule would refuse the second
one safely rather than mis-merge it.

### Result

Matches the implementation report.

### Residual risk

Minor, disclosed here rather than found as a gap: alias recording is now
broader than before (unconditional rather than filtered), which is the
intended fix, but it does mean more rows accumulate in
`submission_identity_aliases` over a run's lifetime (including for pages
like OAuth hops that will likely never be revisited) than before this
pass. This is a storage-growth observation, not a safety concern.

### Blocks P0-B2

No.

---

## Finding 6 — Confirmation false positives

**Classification: PARTIALLY FIXED -- one residual gap found, not disclosed
in the implementation report**

### Evidence inspected

`browser_automation.py`'s `submission_confirmed`,
`_CONFIRMATION_HEADING_PHRASES`, `_CONFIRMATION_HEADING_RE`.

### Independent reproduction performed

Both named adversarial examples ("Application received after 5pm will be
reviewed next day", "Status inquiry for previously submitted application")
were confirmed rejected. Two additional adversarial variants constructed
for this review were also rejected: an unrelated page mentioning
"applications submitted by other candidates" in ordinary prose, and a
policy-notice heading ("Important: application submitted documents cannot
be changed after review") that contains the phrase but is not confirming
this run. Real confirmations were confirmed recognized across several
structures: `<h1>`, `<h2>`, and `[role=status]`.

**One genuine gap was found while testing "whether restricting evidence to
`h1, h2, h3, [role=alert], [role=status]` creates unsafe behavior
elsewhere"** (this review's explicit instruction): a heading with
`style="display:none"` containing exact confirmation wording was **still
recognized as a confirmation** (`submission_confirmed()` returned `True`).
This was verified directly against Chromium's actual `innerText` behavior
in this environment (not assumed from the specification): `innerText` on a
`display:none` element returned its text content rather than the empty
string the specification describes for non-rendered content. A
non-semantic `<div>` with the same wording but no heading/alert/status role
was correctly *not* recognized (a false negative, which is the intended,
safe fallback direction) -- so the gap is specific to hidden elements that
do carry one of the five selected tags/roles, not a general weakening of
the restriction.

This matters because conditionally-rendered "success" templates --
present in the DOM but hidden via CSS until an API call resolves -- are a
common pattern in modern single-page applications (toggling visibility
rather than mounting/unmounting). A job-application SPA that pre-renders
its own success state this way, encountered by `submission_confirmed()`
before an actual submission occurs, could produce a false `CONFIRMED`
through this path, which is the same class of harm Finding 6 was raised to
close, by a different mechanism than the one originally reported.

### Residual risk and why it is assessed as not blocking

Per this project's own standing design (recorded in Pass 2 and unchanged by
this review's recommendation), a false `CONFIRMED` is a data-quality/audit
problem, not a resubmission-safety one: it does not grant any dispatch
authority, does not touch `submission_effects`, and cannot cause a second
click. The existing, broader finding that confirmation-evidence matching
was "not independently characterized against false-positive synthetic
pages" (Pass 2) already flagged this class of risk as open; this review
narrows it to one concrete, reproducible mechanism rather than closing it.

### Blocks P0-B2

No, but **recommended for a dedicated follow-up**: either check
`getComputedStyle(element).display !== 'none'` (and ideally
`visibility`/opacity) explicitly before accepting a heading's text, or
switch to `textContent` with a separate, explicit visibility check rather
than relying on `innerText` to encode visibility reliably in every
environment this runs in.

---

## Review of the Done/Complete regression and its fix

**Classification: VERIFIED FIXED**

Independently confirmed `safety.is_submit_label("Done")` and
`safety.is_submit_label("Complete")` both now return `False` (restoring
`interaction.commit_draft_cards()`'s legitimate per-entry "Done" click,
re-run directly against a card-shaped fixture with no `<form>` wrapper --
the click fired and the card committed). Independently confirmed, via a
fresh Playwright reproduction using real cross-origin navigation (not the
existing parametrized test), that a genuinely final "Done" button and a
genuinely final "Complete" button -- each the sole labelled candidate
inside a real `<form>` -- are still blocked by the structural default-deny
rule from Finding 1. The fix removes the ambiguous words from the
vocabulary layer without removing the structural layer's independent
coverage of a genuine final use of either word.

---

## Test accounting

Verified independently, not accepted on the implementation report's
arithmetic alone:

- Cleared `.pytest_cache/v/cache/lastfailed` before a fresh, isolated full
  run (`python -m pytest -q -rs -n auto`), so the cache could not contain
  accumulated entries from earlier, unrelated sessions -- a known failure
  mode of this cache observed and worked around in earlier passes of this
  engagement.
- Result: `18 failed, 1991 passed, 3 skipped, 1 error` in 742.84s, matching
  the implementation report's claimed final numbers exactly.
- Read the freshly-populated cache file directly and compared every one of
  the 18 failing node IDs plus the 1 collection error, by exact name, against
  the specific 12 `tests/test_page_agent.py` names and 6
  `tests/test_failures_catalogue.py` parametrized names recorded as the
  pre-existing dirty-worktree baseline throughout Passes 1-3 of this
  engagement. All 18 names, and the one collection error
  (`tests/test_ant_select.py` importing the absent `_somewhere_profile`),
  matched exactly -- not merely in count.

### Classification of every remaining failure/error

| Node | Classification |
|---|---|
| `tests/test_ant_select.py` (collection error) | PRE-EXISTING BASELINE |
| 6x `tests/test_failures_catalogue.py::test_every_test_an_entry_names_exists[...]` | PRE-EXISTING BASELINE |
| 12x `tests/test_page_agent.py::test_*` (listed in the cache dump above) | PRE-EXISTING BASELINE |

No entry required a NEEDS INVESTIGATION or PASS 4 REGRESSION classification
in the final state. (The one actual Pass 4 regression, the Done/Complete
vocabulary collision, was found and fixed within the same pass, before this
independent review began, and is covered in its own section above.)

### Targeted tests re-run independently

`tests/test_submission_effect_state.py`,
`tests/test_submission_firewall.py`, `tests/test_safety_rules.py`,
`tests/test_phase2_primitives.py::test_commit_open_sections_preserves_safety_against_submit_label`
were each re-run during this review; all passed, consistent with the
implementation report.

---

## Review of production diffs

Every file the implementation report lists as changed
(`job_tracker.py`, `apply_flow.py`, `jd_analyzer.py`, `db.py`, `safety.py`,
`submission_guard.py`, `browser_automation.py`) was read in full diff form
(or, for the untracked `submission_guard.py`, read in full) for this
review.

- **Weakened safety checks**: none found. `SUBMIT_LABEL_RE`'s change is a
  net widening minus two words moved to rely on the (also new, strictly
  additive) structural layer instead -- not a removal of coverage.
- **Site-specific workarounds masquerading as general fixes**: none found.
  No company, site, or ATS-vendor name appears in any of this pass's
  production diffs; the closest thing, the Workday-specific selectors in
  `is_review_step()`, predates this pass and was not touched by it.
- **New independent submission authority**: none found. Every change in
  this pass is either a data-layer bookkeeping change (`job_tracker.py`,
  `apply_flow.py`), a pure string/regex function
  (`jd_analyzer.py`/`db.py`/`safety.py`), or additional *denial-side* guard
  logic (`submission_guard.py`) -- none of them add a new path that clicks
  or submits anything. `browser_automation.py`'s change is read-only
  detection, not a new effect.
- **Unexpected changes outside authorized scope**: the diffs touching
  `interaction.py` and `page_agent.py` that `git status` shows are confirmed
  to predate this pass (Passes 1-3); this review's own session made no
  edits to either file, and the diffs match what Pass 3's own
  documentation already describes.
- **Behavior only safe because of current test fixtures**: the one
  instance found of behavior being safe only incidentally -- `set_content()`
  masking the Finding 3 gap in most quick manual checks because the
  project's own `test_submission_firewall.py` fixtures construct the guard
  *after* `set_content()` rather than before -- is exactly Finding 3's
  residual gap above, not a second instance.
- **Concurrency/transaction issues**: tested directly for Finding 2 (see
  above); none found. No other change in this pass introduces concurrent
  state access.
- **Fail-open behavior**: none found in the area most at risk of
  reintroducing it (Finding 2's redesign, since the original bug was itself
  a fail-open). Finding 3's gap is a *failure to ever engage* the
  containment on one narrow content-delivery path, which is a different,
  narrower failure mode than a check that engages and then wrongly permits
  the effect; it is documented above rather than minimized.

---

## Summary

| Finding | Classification | Blocks P0-B2 |
|---|---|---|
| 2 -- Alias annexation (confirmed blocker) | VERIFIED FIXED | No |
| 1 -- Unrecognized-label submit bypass | VERIFIED FIXED | No |
| 3 -- Inactive guard on new tabs/popups | PARTIALLY FIXED (realistic case fixed; `document.write()`-class gap found, pre-existing and narrow) | No |
| 4 -- Tracking-parameter fragmentation | VERIFIED FIXED | No |
| 5 -- Login-host alias skipping | VERIFIED FIXED | No |
| 6 -- Confirmation false positives | PARTIALLY FIXED (named cases fixed; `display:none` heading gap found) | No |
| Done/Complete regression (self-caught in Pass 4) | VERIFIED FIXED | No |
| Test accounting | VERIFIED by exact node-ID match | No |
| Production diffs | No weakened checks, no new authority, no fail-open behavior found | No |

Two residual gaps were found during this review that the implementation
report did not disclose (Finding 3's `document.write()`-class listener
loss, Finding 6's `display:none` heading match). Neither enables a second
real-world submission, neither was introduced by this pass specifically
(Finding 3's gap is inherited from pre-existing `protect_submission()`
behavior; Finding 6's gap is a different mechanism within an
already-flagged-open class of risk), and both come with a concrete,
actionable recommendation for a follow-up pass. The confirmed blocker
(Finding 2) was independently and extensively verified, including against
scenarios more adversarial than its own regression tests, and held in
every case.

## P0-B2 started

NO.

## Final recommendation

**READY FOR HUMAN P0-B1 APPROVAL**

This is a recommendation to proceed to human review, not a claim that no
further work remains: the two residual gaps above, and the recommended
follow-ups for each, should be part of what the human approver sees before
deciding.
