# PROJECT_PLAN_WBS.md - Work Breakdown Structure & Roadmap

## Executive Summary
Roadmap to consolidate, secure, and clean up the job application pipeline.
This plan keeps the project's existing human-in-the-loop safety model (see
`BEHAVIOUR.md`) as non-negotiable. Nothing here authorizes autonomous
submission, autonomous credential recovery, or blind consent assertion --
those were removed from an earlier draft of this plan on purpose.

---

## Phase 1: Security Hardening, Hygiene & Entry-Point Consolidation (2-3 Days)
- **Task 1.1: Personal Data Decoupling**
  - Move real personal data (address, phone, EEO answers, citizenship/visa
    status, salary range, current employer) out of `config.py`'s
    `UserProfile` defaults and into `data/profile.json` (already gitignored).
    Keep the `UserProfile` dataclass as field definitions with safe,
    non-identifying defaults.
  - Verify `data/profile.json` and `data/profile_answers.json` stay
    gitignored, including in any commits already made.
  - Add basic validation on load so a malformed `profile.json` fails loudly
    instead of silently falling back to defaults.
- **Task 1.2: Entry-Point Unification**
  - Deprecate `main.py`: have it print a message pointing to `apply.py` and
    exit non-zero, rather than silently running the older, less complete flow.
  - Route all real usage through `apply.py` -> `apply_flow.py`.
- **Task 1.3: Repository De-Cluttering**
  - Delete `Claude outputs/browser_automation_fixed.py` and its README --
    confirmed stale (~2,000 lines diverged from the current
    `browser_automation.py`), and its own instructions would undo later
    fixes if ever followed.
  - Decide `agent_v2`'s fate deliberately: either migrate
    `storage/encryption.py` into the live path to encrypt
    `data/applications.db` and stored answers at rest, or archive `agent_v2`
    clearly as an inactive prototype. Don't leave it idling, unreferenced,
    indefinitely.

---

## Phase 2: Core Browser Primitives & Seam Separation (1-1.5 Weeks)
- **Task 2.1: Universal Synthetic Event Dispatcher**
  - Add a shared `fill_and_dispatch(locator, value)` helper for text, date,
    and spinbutton inputs: focus -> fill -> dispatch `input` (bubbling) ->
    dispatch `change` (bubbling) -> blur.
  - Target: the class of "site's JS never noticed the value" bugs behind a
    large share of the one-off per-site fixes already in the git history.
- **Task 2.2: Architectural Seam Extraction**
  - Split `browser_automation.py` into a perception layer (reading and
    normalizing DOM state: labels, roles, current values) and an
    interaction layer (clicking, typing, selecting) -- as an incremental
    refactor of the existing, working logic, not a rewrite.
- **Task 2.3: Ant Design & Detached Combobox Resolver**
  - Formalize the existing dropdown handling into a reusable resolver:
    `mousedown` on the wrapper trigger, wait for the detached root-mounted
    option portal, match and click the option by its visible text.
- **Task 2.4: Pre-Progression Draft Card Committer**
  - Generalize the Workday "save open cards before Next" pattern (already
    in `sites/workday.py`) into a shared pre-progression sweep other
    multi-step ATS wizards can reuse.

---

## Phase 3: Form-Filling Robustness & Compliance Checks (1-1.5 Weeks)
- **Task 3.1: Modal & Cookie Sweep Hardening**
  - Extend the existing modal/cookie-banner dismissal to scroll long policy
    text to the bottom before looking for a confirm button, and to handle
    dialogs mounted in shadow DOM.
  - Any consent or attestation checkbox continues to go through
    `safety.is_attestation()` and `profile.sign_attestations` exactly as
    `BEHAVIOUR.md` already specifies. This phase does not add automatic
    assertion of anything.
- **Task 3.2: Login-Wait UX Improvements**
  - Improve the "waiting for you to log in" step: a clearer on-screen
    prompt, and detecting when the login form has actually been replaced by
    the app instead of polling blindly.
  - Explicitly out of scope: automated password reset, OTP interception, or
    a credential vault. Login stays the human's, exactly as
    `safety.password_allowed()` already enforces for
    Google/Microsoft/Apple/LinkedIn/Indeed/Dice.
- **Task 3.3: Sponsorship & Legal Guardrail Coverage**
  - Extend the existing non-sponsorship-clause detection
    (`jd_analyzer.py` / `safety.no_sponsorship_statement`) with more
    phrasing patterns as they're found, each with its own regression test.

---

## Phase 4: Production Hardening & Verification (1-1.5 Weeks)
- **Task 4.1: State Fingerprint Circuit Breaker**
  - Track a fingerprint of `(url, step indicator, visible-input count)`. If
    unchanged across 3 consecutive progression attempts, stop and report a
    blocked loop instead of retrying indefinitely.
- **Task 4.2: Automated Forensic Failure Dumper**
  - On an unhandled error, extend the existing evidence capture
    (screenshot, HTML, `validation.json`) with a structured accessibility
    snapshot, to speed up debugging new ATS quirks.
- **Task 4.3: Resume/Cover-Letter Generation Review**
  - Revisit, as a deliberate product decision rather than a default: is
    always tailoring the resume via Claude still the right trade-off, or
    does a local-master-resume mode make sense for some applications? Not a
    safety question -- worth a conscious choice either way.
