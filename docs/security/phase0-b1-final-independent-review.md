# Phase 0-B1 — Final Independent Security and Correctness Review

**Base branch:** `codex/architecture-reliability`
**Review branch:** `fix/submission-replay-and-containment-hardening`
**Diff reviewed:** 16 files, 2989 insertions, 70 deletions, 5 commits
(`54fd57b`, `7957044`, `48ac241`, `d0e362f`, `b828042`)

This review reads the complete diff and re-derives its own conclusions
rather than citing prior implementation or review notes as evidence. Every
claim below is backed by either a fresh reproduction run during this review
(shown inline) or a direct reading of the code at its current, committed
state. No production code was modified. The PR was not merged. P0-B2 was
not started.

---

## 1. Submission replay protection

**VERIFIED.**

- A dispatched identity cannot be dispatched again through any key that
  resolves to it (direct key, alias, multi-hop sub-alias, or a root
  annexed under a still-higher root): re-confirmed by direct reproduction
  against the committed code, not re-citing earlier runs --
  `begin_submission_dispatch` on a second alias of an already-`DISPATCHED`
  identity raises `RuntimeError`, and raw SQL inspection of
  `submission_effects` shows exactly one row throughout.
- Survives retry: an `UNCERTAIN` state blocks a fresh
  `begin_submission_dispatch` directly, and `apply_flow.refuse_submission_replay()`
  independently reaches the same conclusion through the durable-state
  read, confirmed by a fresh run against the committed code.
- Survives "process restart": two independent `JobTracker` instances
  opened against the same SQLite file, with concurrent
  `begin_submission_dispatch` calls for the same key, produce exactly one
  `DISPATCHED` and one blocked result -- each instance holds its own
  connection, so this exercises SQLite's actual file-level locking, not
  merely in-process state.
- **Alternate submission paths:** grepped the entire production tree for
  every call site of `begin_submission_dispatch`. There is exactly one:
  `submission_guard.py`'s `submit_verified()`. No site adapter, no other
  module, calls it. This matches CLAUDE.md's "keep irreversible action
  authority centralized" requirement and confirms this PR did not add a
  second path with dispatch authority.
- **No fail-open on missing/unavailable/malformed/reset state:**
  - `submission_effects.state` is protected by a SQL `CHECK` constraint
    (`state IN ('AUTHORIZED','DISPATCHED','CONFIRMED','UNCERTAIN')`) --
    malformed values cannot even be inserted through ordinary SQL, let
    alone the public API.
  - Deleting the database file mid-run, and separately dropping the
    `submission_effects`/`submission_identity_aliases` tables entirely
    (simulating a corrupted or reset store), both raise
    `sqlite3.OperationalError` from `begin_submission_dispatch`, which
    propagates to `submit_verified()`'s own exception handler.
  - Ran the *actual* gateway (`SubmissionGuardV0.submit_verified()`, not
    just the tracker method) against a real Playwright page with the
    `submission_effects` table dropped after setup: it returned `False`,
    logged `SUBMISSION_BLOCKED: verified submit gateway failed closed`,
    and the button was never clicked (`window.submitted` stayed unset).
  - Empty string and `None` as `dedup_key` both raise `ValueError`
    immediately, before any transaction begins.

No finding in this section rises above VERIFIED.

---

## 2. Browser-side submission containment

**VERIFIED**, with one **RESIDUAL RISK** carried forward from the PR's own
disclosure (not newly found here).

- Click, Enter-key implicit submission, native `form.submit()`/
  `requestSubmit()`, and the new structural default-deny path for
  unrecognized-label/no-native-type controls are all covered -- confirmed
  by reading `finalControl()`/`firstSubmitter()`/`finalForm()` and by
  re-running the existing and new Playwright-based tests against the
  committed branch.
- **Listener removal cannot *permanently* disable protection, but there is
  a bounded window where it is actually off.** `ensureListeners()` is
  idempotent (`addEventListener` discards a duplicate registration of the
  same function reference), confirmed by five consecutive `activate()`
  calls producing exactly one denial for one click -- not five. It is
  only re-invoked when `activate()` is called again, which in production
  happens on every `PageAgent` loop iteration via `protect_submission()`.
  Reproduced directly: activating, then replacing the document's content
  (the exact mechanism `page.set_content()` uses), then clicking
  *without* a further `activate()` call, is genuinely unguarded
  (`denials == []`). Calling `activate()` once more repairs it before the
  next click. **RESIDUAL RISK, not a defect**: a click that lands in the
  narrow window between a listener-stripping replacement and the next
  loop iteration's `activate()` call is not covered. This is the same
  property the PR's own hardening-followup report already discloses; this
  review independently re-confirms it rather than taking that disclosure
  on faith, and finds no way to close it further without a continuous
  liveness probe, which the PR did not attempt and was not asked to.
- **Missing guard objects are correctly restored**, independently verified
  with a construction this review built specifically to rule out the
  property's own tamper-resistance from producing a false pass: the guard
  object is defined `configurable: false`, so a page-side `delete
  window.__jaaSubmissionGuardV0` silently no-ops (confirming that specific
  guarantee, incidentally) and does not exercise the fallback path. A
  genuine "object never existed" case was constructed instead -- a
  *different* browser context the guard's init script was never injected
  into -- and `_activate_frame()`'s fallback correctly re-injected the
  full script and the subsequent click was denied.
- **No duplicate listeners or duplicate denial behavior**: confirmed
  directly (five activations, one denial) rather than inferred from the
  idempotency argument alone.

---

## 3. Python/browser authority boundary

**VERIFIED for the paths this review could trace a concrete authority
decision for; one architectural nuance documented as RESIDUAL RISK that
the implementation notes did not state as precisely as this review found
it.**

- The verified auto-submit gateway (`click_verified_submit` ->
  `submit_verified`) is unambiguously Python-authoritative: the durable
  `begin_submission_dispatch` transaction commits *before* the browser
  click is authorized or attempted, confirmed by the fail-closed
  reproduction in Section 1. A browser-side-only bypass cannot reach this
  path at all without first passing Python's structured
  `AutoSubmitDecision` validation and the durable dispatch commit.
- For `PageAgent`'s ordinary navigation path, `submit_gate(page, controls)`
  (called with its default `last_step=True`) has exactly two return
  values when the current step is genuinely the application's last step
  -- "the tailored resume is not attached" or "automatic submission is
  off -- the application is ready for you to submit" -- and no code path
  in that branch returns an empty string. So when `_press_next_locked`
  correctly classifies the current control as the true final step, Python
  unconditionally refuses to click it itself, independent of the browser
  guard. This matches CLAUDE.md's "never implement a code path that
  clicks Submit outside `apply_flow.hand_over()`'s existing review gate"
  invariant and was not weakened by this PR.
- **The nuance**: for a control Python classifies as an *intermediate*
  submit (`submit_word` true, but `last_step=False` -- e.g. a step that
  saves progress rather than sending the whole application, the
  documented Schwab case), `submit_gate(..., last_step=False)` does *not*
  unconditionally refuse; it runs CAPTCHA/legal-conflict/attestation
  checks and, if none fire, allows the click. That classification (is
  this really not the last step?) depends on Python's own step-counter/
  review-heuristic reading of the page. If that reading is wrong -- the
  control is actually the true final submit, but Python's heuristics
  believe otherwise -- the *only* remaining protection for that specific
  click is the browser-side guard; there is no second, independent
  Python-side check for this specific misclassification case. This is an
  existing architectural property, not introduced or hidden by this PR --
  if anything, this PR's Finding 1 structural default-deny rule narrows
  the set of controls that could slip through in exactly this scenario,
  by not depending on the control's label at all. It is reported here
  because the review was asked to verify "a browser-side bypass alone
  must not permit submission," and that statement is only unconditionally
  true for the two paths above, not for every classification Python might
  reach.

---

## 4. Retry and recovery behavior

**VERIFIED.**

- A retry from `UNCERTAIN` cannot be converted into a fresh authorization:
  confirmed both by calling `begin_submission_dispatch` directly (raises)
  and by calling `apply_flow.refuse_submission_replay()` (returns
  `"UNCERTAIN"`, not `None`), against the committed code.
- Navigation/redirect/DOM-replacement: the identity-alias mechanism
  (Section on submission identity, verified extensively in the
  implementation's own prior passes and spot-re-checked here via the
  single-call-site grep in Section 1) ties a bound application's identity
  across host/path changes; the listener-liveness repair in Section 2
  covers DOM-replacement-class navigation specifically. Both were
  re-exercised against the committed branch in this review, not assumed
  from memory.
- Exceptions do not cause fail-open behavior: every exception path
  exercised in Section 1 (deleted file, dropped tables, invalid keys)
  resulted in the gateway refusing to click, not in silently proceeding.

---

## 5. Concurrency / race conditions

**VERIFIED**, with one assumption made explicit.

- `begin_submission_dispatch()`'s canonical-identity resolution and its
  state read/write happen inside the same `BEGIN IMMEDIATE` SQLite
  transaction (read directly from the committed `job_tracker.py`). There
  is no window between "resolve who owns this identity" and "check/write
  its state" for another writer to interleave.
- Two threads dispatching through two different aliases of the same
  identity: exactly one `DISPATCHED`, one blocked, exactly one row in
  `submission_effects` -- reproduced against the committed code in this
  review.
- Two *separate* `JobTracker` instances (each with its own SQLite
  connection, the closest practical approximation to two worker
  processes sharing one database file) dispatching the same key
  concurrently: exactly one succeeds -- reproduced in this review.
- **Atomicity assumption made explicit**: this guarantee rests on SQLite's
  `BEGIN IMMEDIATE` providing a real write lock at the file level. The
  production flow is confirmed (via `db.py`'s
  `_local_submission_tracker()` delegation, read directly) to *always*
  route durable submission-effect state through local SQLite, even when
  application metadata uses PostgreSQL -- so this assumption currently
  holds for every real deployment configuration this code supports. It
  would need independent re-verification if the durable submission-effect
  store were ever moved off local SQLite onto a different backend; this
  PR does not do that and was not asked to.

---

## 6. Test quality

**VERIFIED** for the properties asked; two **TEST GAP** items noted.

- All three new/touched test files
  (`test_submission_effect_state.py`, `test_submission_firewall.py`,
  `test_safety_rules.py`) use a real Playwright-launched Chromium browser
  and a real `JobTracker` against a real temp-file SQLite database --
  confirmed by grep: no mocking framework (`unittest.mock`, `Mock`,
  `MagicMock`) appears anywhere in any of the three files.
- The previously-vulnerable cases are reproduced, not merely asserted
  against after the fact: the exact annexed-sub-alias scenario, the exact
  `set_content()`-class listener-wipe scenario, and the exact
  `display:none` heading scenario each have a dedicated test that fails
  against the pre-fix code and passes against the committed code (traced
  by reading each test against the corresponding fix in its own commit).
- Re-ran the full set of targeted suites the PR reports against the
  actual committed branch state in this review (not assumed from the PR
  description): `198 passed` -- matches the reported figure exactly.
- **TEST GAP (LOW):** no test exercises two durable-dispatch writers in
  genuinely separate OS processes (only threads and separate in-process
  tracker instances were used, here and in the implementation's own
  tests). The separate-instance test is a reasonable proxy since each
  `JobTracker` opens its own file handle and the lock is enforced by
  SQLite itself, not by anything in-process, but a literal
  multiprocessing-based test would be strictly stronger evidence.
- **TEST GAP (LOW):** no test exercises the Section 3 nuance directly --
  an intermediate (`last_step=False`) control that Python's heuristics
  wrongly believe is not the final step, with an unrecognized label, to
  confirm the browser guard alone still blocks it in that specific
  classification state. The existing Finding 1 tests cover the browser
  guard's behavior in isolation (sufficient to conclude the guard itself
  would still block such a control), but not the full `PageAgent`
  decision path reaching that exact state.

---

## 7. Regression scope

**VERIFIED.**

- The diff contains exactly five feature areas, matching the five
  commits: the degree-by-name test fix, durable submission-identity
  resolution plus tracking-parameter/login-host consolidation, browser
  containment hardening, confirmation-detection hardening, and
  documentation. No file in the diff falls outside one of these areas.
- Existing safety gates preserved: `begin_submission_dispatch()`'s core
  state-machine body (`AUTHORIZED -> DISPATCHED`, blocking on
  `DISPATCHED`/`CONFIRMED`/`UNCERTAIN`, raising on an invalid persisted
  state) is textually unchanged except for operating on a resolved
  canonical key instead of the literal argument; `submit_gate()`'s
  unconditional `last_step=True` refusal is untouched;
  `SubmissionGuardV0`'s existing label- and structural-type-based checks
  are untouched, with the new rule added as a third, independent `return`
  branch.
- Changed default behavior, disclosed rather than hidden: more controls
  are now blocked by default than before this PR (the structural
  default-deny rule, and the widened `SUBMIT_LABEL_RE` minus "done"/
  "complete"). This is a behavior change in the safe direction
  (more containment), and the one false-positive collision it created
  during development (`interaction.commit_draft_cards()`'s own "Done"
  button) was found and fixed within the same development effort, with a
  regression test proving the fix, before this PR was opened.
- **RESIDUAL RISK (compatibility, LOW):** the new "sole remaining
  candidate in its form" default-deny rule could, on a real ATS DOM shape
  not covered by any fixture in this test suite, misclassify a legitimate
  non-final control whose label is not in `SAFE_UTILITY_LABEL_RE` as a
  possible final submit. The consequence of that misclassification is a
  denied click and a `PageAgent` stop requiring owner review (confirmed
  safe failure direction: `_press_next_locked` already checks
  `denial_count` before/after its click and returns `"stop"` rather than
  reporting false success) -- not a safety violation, but worth the
  owner's awareness as a possible source of new "needs you" stops on an
  ATS this test suite has not seen.

---

## Summary of findings

| # | Finding | Classification | Severity |
|---|---|---|---|
| 1 | Replay protection across keys/retries/restarts/corrupted-store exceptions | VERIFIED | -- |
| 2 | Single call site for `begin_submission_dispatch` | VERIFIED | -- |
| 3 | Listener-liveness repair is correct but only engages on the next `activate()` call | RESIDUAL RISK | LOW |
| 4 | Missing-guard-object fallback | VERIFIED | -- |
| 5 | Idempotent repeated activation, no duplicate denials | VERIFIED | -- |
| 6 | Python is unconditionally authoritative for the gateway and the true-final-step ordinary path; the browser guard is the sole protection for an intermediate-step misclassification | RESIDUAL RISK | MEDIUM |
| 7 | Concurrency/TOCTOU inside `begin_submission_dispatch` | VERIFIED | -- |
| 8 | Atomicity depends on the durable store always being local SQLite | RESIDUAL RISK (assumption, currently always true) | LOW |
| 9 | No genuinely-separate-process concurrency test | TEST GAP | LOW |
| 10 | No test of the Section 3/Finding 6 misclassification path through the full `PageAgent` decision logic | TEST GAP | LOW |
| 11 | New default-deny rule could misclassify an untested ATS's non-final control | RESIDUAL RISK (compatibility) | LOW |

No **DEFECT** was found. No finding in this review identifies a path to a
second real-world submission for the same application, a fail-open
condition, or a weakened existing safety gate.

## Recommendation

**READY FOR P0-B1 MERGE**

This is a recommendation, not an instruction to merge: per the review's
own constraints, the PR was not merged and P0-B2 was not started. The
residual risks above (items 3, 6, 8, 11) and test gaps (items 9, 10) are
disclosed for the owner's review alongside this recommendation, not
resolved by it.
