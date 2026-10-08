# Phase 0-B2 — Discovery: Account / Authentication / Verification Architecture

**Branch:** `fix/account-auth-verification-state-hardening`
**Base:** `fix/submission-replay-and-containment-hardening` @ `9e71a638`
(the owner had not yet merged P0-B1 into `main` or `codex/architecture-reliability`
at the time this branch was created; per explicit instruction, P0-B2 branches
from the B1 tip rather than waiting, so P0-B1's submission-authority work is
actually present in this base.)

## Headline finding

`docs/AGENT_ARCHITECTURE.md` is explicit that it is "the product contract and
incremental architectural direction, not a claim that every capability below
is already implemented," and Section 50 specifically warns: "Before changing
this area, inspect current repository state and existing regression work. Do
not redo work that is already implemented." Discovery confirms this is not a
formality here. The account/authentication/verification layer the
architecture document describes is **substantially already built**, as one
coherent, evidence-driven, deterministic system -- not the scattered
collection of unrelated special cases the document's framing might suggest to
someone who had not yet read the code. The real work for this phase is
narrower than "build a state layer": it is auditing what exists against the
document's full list, closing the genuine gaps, and documenting the result
honestly.

---

## 1. The existing architecture, module by module

### `account_state.py` -- the one state model and one decision table

This module already **is** the "one explicit account/authentication state
model" the task asks for, built 28-30 September 2026 after a real incident
(KBI Biopharma: "signing in was decided in pieces, each reading the page its
own way... so a page one piece misread sent the run down the wrong road").

- `AccountState` (frozen dataclass): `kind`, `why` (the page's own words that
  decided it), `google_offered`, `password_boxes`, `form_error`.
- Twelve `kind` values: `SIGNED_IN`, `LOCKED`, `WRONG_PASSWORD`,
  `ACCOUNT_EXISTS`, `CODE_ENTRY`, `VERIFY_EMAIL`, `CREATE_FORM`,
  `SIGN_IN_FORM`, `EMAIL_FIRST`, `CHOOSER`, `LOADING`, `NONE`.
- `read_state(snapshot, password_boxes=None) -> AccountState`: entirely
  deterministic, regex-over-accessibility-snapshot-text evidence, in a fixed
  precedence order documented in its own docstring ("most telling first: what
  the site SAYS... outranks what the form looks like"). **No AI/model call of
  any kind appears anywhere in this function.**
- `Memory` (dataclass): per-run, in-process tracking of what has already been
  tried on a given host (`google_tried`, `created`, `signed_in_tried`,
  `email_given`, `account_exists`, `reset_tried`, `refused_before`,
  `verify_tried`, `held`).
- `next_step(state, memory) -> Step`: **the one decision table**. Fourteen
  `action` values (`GOOGLE`, `CREATE`, `SIGN_IN`, `GIVE_EMAIL`, `ENTER_CODE`,
  `VERIFY_BY_LINK`, `RESET_PASSWORD`, `OPEN_SIGN_IN`, `WAIT`, `FOR_OWNER`,
  `NOTHING`). Each branch is a single, auditable `if`, with the owner's
  standing rules spelled out in comments at the point they apply (Google
  first; a known account's refused password is reset per CLAUDE.md §5; a
  create-form still showing after `memory.created` is already `True` is
  handed to the owner rather than retried -- this **is** the architecture
  document's `OUTCOME_UNKNOWN` concept, just not under that name).

### `login_guard.py` -- durable, cross-run attempt accounting

Exactly the "centralize attempt accounting" the task's Section 8 asks for,
already built, already durable (file-backed: `data/_login_attempts.json`,
atomic write via a `.tmp` + `replace`), keyed by `(host, email)`, with a
24-hour sliding window: `may_sign_in`, `may_create_account`, `may_read_code`,
`may_request_reset`, plus `record_*` writers and `refused_before`/
`clear_hold`/`owner_resumed` for the hold-until-the-owner-acts semantics.
Caps: 2 failed sign-ins, 2 account creations, 1 reset request, 6 code reads
per 24 hours per account -- documented against real Workday lockout
behavior (researched 25 September 2026, cited in the module's own docstring).

### `emailed_codes.py` -- the one rule for reading the owner's mail

Exactly the task's Section 9/10 "one rule in one place": `why_not(profile,
url, captcha, email)` is the single gate every code/link reader must pass
through (allowed-to-read-mail, employer-site-only via
`safety.password_allowed`, no-CAPTCHA, `login_guard` budget not spent).
`verification_link_ok(link, text, site_url)` validates https, same-site
(`_site_of`, last-two-labels comparison), same-tenant on a shared ATS host
(checks the tenant subdomain appears in the link), verify-wording present,
reset-wording absent -- directly implementing the task's Section 9 "correct
account/employer/tenant... not stale/unrelated" bar. `unwrap()` handles
Gmail's own `google.com/url?q=` redirect wrapper.

### `field_requirements.py` -- optional-account field scoping

Exactly the task's Section 5. `account_password_fields(snapshot)` walks the
accessibility snapshot maintaining a heading/container scope stack, excludes
any password-looking label that is actually an OTP/code/PIN
(`_CODE_PASSWORD`), resolves unnamed inputs via a preceding password-shaped
label within the same container (`_PASSWORD_LABEL`), and tags each field
`optional_account=True` only when a heading or named container in its own
scope matches `optional_account_section()` (contains "account" and
"optional", not "not optional"/"non-optional"). `account_state.read_state()`
subtracts optional fields from the password count before deciding
`CREATE_FORM` vs `SIGN_IN_FORM` vs `NONE`.

### `page_agent.py:sign_in_step()` (~lines 1468-1643) -- the orchestrator

This is the de facto `AuthManager` the architecture document's Section 30
describes wanting, assembled from the pieces above plus live browser calls,
not a literal class with those method names, but functionally equivalent:

1. Reads `account_state.read_state()` from the current snapshot.
2. Cross-checks the ATS adapter's `candidate_account_state()` (Workday has a
   real implementation; `sites/base.py`'s default returns `""` for every
   other adapter) to upgrade a reading to `VERIFY_EMAIL` when the adapter's
   own account-home page says so.
3. Checks `self.assistant.account_on_record()` for a durably-known existing
   account.
4. Assembles `Memory` from a mix of in-process sets (`_created_at`,
   `_signed_in_at`, `_emailed_in`, `_account_known`, `_reset_asked`,
   `_verify_asked`) and durable `login_guard.refused_before()`.
5. **Explicit mixed-form guard** (directly answering the task's Section 5
   "mixed forms... handled conservatively"): "Until they can target a
   specific form, do not let a mixed page fill the optional account's
   password while trying to complete a required sign-in" -- any page with
   both an optional-account field and a required sign-in/create/reset action
   stops for the owner rather than guessing which password box is which.
6. **Job-board exclusion**: never attempts an account step on a job board
   domain (`job_sources.job_board`); the owner applies on the employer's own
   site.
7. Dispatches on `step.action`, with explicit, bounded retry handling: `WAIT`
   caps at 3 settles before handing off; `CREATE`/`SIGN_IN` failure re-derives
   a fresh `WRONG_PASSWORD` state and re-consults the **same** decision table
   to decide reset-vs-handoff, rather than a separate ad hoc rule.
8. `_note_account_state()` logs every state transition
   (`"ACCOUNT_STATE: %s -> %s%s"`) and, when a job directory exists, saves a
   screenshot and the account-step snapshot text under `<job_dir>/account/`.

### `browser_automation.py` -- the actions `sign_in_step` calls

`handle_auth_gate`, `account_on_record`, `recover_rejected_sign_in`,
`verify_account_by_email_link`, `_sign_in_with_google_step`,
`_give_email_first`, plus the `emailed_codes`/`login_guard` call sites listed
below. Google sign-in is attempted first whenever offered and not already
tried/refused this run (the owner's standing instruction); LinkedIn, Indeed,
Dice, Apple, Microsoft, Facebook are hard-blocked (`BLOCKED_LOGIN_DOMAINS`,
`safety.password_allowed`) regardless of anything in this layer.

### ATS adapters (`sites/*.py`)

`sites/base.py` declares the extension point
(`candidate_account_state(page) -> str`, default `""`) exactly matching the
architecture document's Section 31 "Portal Adapters ... portal-specific
browser behavior." **Only `sites/workday.py` overrides it.** Greenhouse,
Lever, Ashby, SuccessFactors, Amazon, and Eightfold have no ATS-specific
account-state evidence function at all; they fall through to the generic,
page-text-based `account_state.read_state()` only.

---

## 2. Full entry-point / search-term audit

| Term | Where it actually lives | Notes |
|---|---|---|
| `account` | `account_state.py` (state model), `field_requirements.py` (`account_password_fields`), `page_agent.py` (`sign_in_step`, `_account_seen`/`_account_known`/`_account_waits`), `job_sources.job_board` (never account-step a job board) | Core domain module name |
| `login` / `sign_in` / `signin` | `login_guard.py` (attempt accounting), `browser_automation.py` (`handle_auth_gate`, `_sign_in_with_google_step`), `page_agent.sign_in_step` | No separate "signin" spelling in production code |
| `password` | `field_requirements.py` (`_PASSWORD`, `_PASSWORD_LABEL`), `safety.py` (`password_allowed`), `.env`/`config.py` (`ats_email`, ATS password) | See credential-source table below |
| `ATS_PASSWORD` | `.env` (not committed), read via `config.py`; never literal in Python source | Confirmed: `grep` for the literal string finds only `.env.example`-style references and `config.py`'s attribute name, never a hardcoded value |
| `login_guard` | see above | |
| `emailed_codes` | see above | |
| `passcode` | `browser_automation.py` (`passcode_from_gmail`, the function `emailed_codes.why_not` gates) | |
| `verification` / `verify` | `account_state.py` (`VERIFY_EMAIL`, `_VERIFY`, `_UNVERIFIED`), `emailed_codes.py` (`verification_link_ok`, `_VERIFY_WORDS`), `page_agent.py` (`verify_account_by_email_link`, `_verify_asked`) | |
| `otp` | `account_state.py` (`_CODE` regex includes `\botp\b`), `field_requirements.py` (`_CODE_PASSWORD` includes `otp`) | No dedicated OTP-entry module; code entry is generic (`CODE_ENTRY` state, `ENTER_CODE` action) regardless of whether the site calls it "OTP," "verification code," or "security code" |
| `magic` | **not found anywhere** | No code distinguishes a "magic link" (verify + authenticate in one) from an ordinary verification link; see Gap G3 below |
| `captcha` | `safety.captcha_visible()`, checked in `PageAgent.run()` **before** `sign_in_step()` is ever reached (confirmed: `page_agent.py:2709` precedes the `sign_in_step` call at `page_agent.py:2746` in the same loop iteration), and again inside `emailed_codes.why_not()` | CAPTCHA during an account/auth flow is already handled upstream, generically, for every page kind -- not a gap specific to this layer |
| `reset` | `login_guard.py` (`may_request_reset`, `record_reset_request`), `page_agent.py` (`_reset_refused_password`), `browser_automation.py` (`recover_rejected_sign_in`) | Scoped exactly to CLAUDE.md §5's "employer ATS domains, once per site per run, never a generated or different password" |
| `authenticated` | No literal `AUTHENTICATED` state name exists; the closest concept is `account_state.SIGNED_IN` ("past the account step: the application itself") | Naming gap, not a behavior gap -- see the state-mapping table below |
| `create_account` | `account_state.CREATE`, `account_state.CREATE_FORM`, `page_agent._created_at` | |
| `optional` | `field_requirements.optional_account_section()`, `account_state.read_state()`'s optional-field handling | |
| `required` | Implicit: everything not matched as optional remains required by default (fail-closed in the safe direction) | |

**Genuinely absent** (confirmed by repository-wide search, not merely
unfamiliar naming): `mfa`, `totp`, `authenticator`, `sms` (as
authentication-MFA, not as an application question), `magic` link handling.
`two-factor`/`2-step verification`/`authenticator app` text appears exactly
once, in `browser_automation.py:_identity_checks()` -- a **pre-submission
validation warning** (feeds `evaluate_auto_submit()`'s report), not an
account-state classification, and not consulted by `sign_in_step()` at all.

---

## 3. Account-state detection path: current vs. architecture-document names

| Architecture doc's suggested state | Current equivalent | Gap? |
|---|---|---|
| `NO_ACCOUNT` | Implicit: `account_state.NONE` conflates "not an account page" with "no evidence of an account here" | Minor naming ambiguity, not a behavior gap (see Section 4) |
| `ACCOUNT_CREATION_STARTED` | `page_agent._created_at` (in-process set) + `login_guard.record_account_attempt` (durable) | Present, split across two places by design (session-local vs. durable) |
| `VERIFICATION_REQUIRED` | `account_state.VERIFY_EMAIL` | Present |
| `ACCOUNT_CREATED` | No standalone state; inferred by the *absence* of `CREATE_FORM` on the next read | Present by construction (see Section 4), not a named state |
| `SIGNED_OUT` | `account_state.SIGN_IN_FORM`/`CHOOSER`/`EMAIL_FIRST` (the many shapes a signed-out page can take) | Present, more granular than the doc's single name |
| `SIGN_IN_REQUIRED` | `account_state.SIGN_IN_FORM` | Present |
| `SIGNING_IN` | Implicit: the moment between `handle_auth_gate()` being called and its result | Not a durable state; transient, in-flight only |
| `AUTHENTICATED` | `account_state.SIGNED_IN` | Present, different name |
| `PASSWORD_RESET_REQUIRED` | `login_guard`/`page_agent._reset_refused_password`'s `RESET_PASSWORD` action -- scoped to the owner's already-approved reset exception, not a general "user must reset" state | Present for the one case this project supports |
| `AUTHENTICATION_FAILED` | `account_state.WRONG_PASSWORD` | Present |
| `ACCOUNT_LOCKED` | `account_state.LOCKED` | Present |
| `AUTHENTICATION_STATE_UNKNOWN` | No single name; produced implicitly whenever `read_state()`'s evidence does not match any pattern (`AccountState(NONE)`) | See Gap G1 |
| `OUTCOME_UNKNOWN` | `next_step()`'s `memory.created` + still-`CREATE_FORM` branch -> `FOR_OWNER`; no equivalent for an interrupted sign-in whose outcome is unclear | See Gap G2 |

---

## 4. Why re-derivation from the live page makes several architecture-document asks already true by construction

`read_state()` is called **fresh, from the actual current page**, on every
single `sign_in_step()` invocation -- never from cached/remembered state.
This one property already satisfies several of the architecture document's
asks without any new code:

- **Section 17 ("verify authenticated state... do not mark successful merely
  because the login button was clicked")**: `handle_auth_gate()`'s own
  success/failure aside, the *next* page read independently re-derives
  `state.kind` from what the page now actually shows. If the click did not
  really authenticate, the next read still sees `SIGN_IN_FORM`/
  `WRONG_PASSWORD`, not `SIGNED_IN`.
- **Section 24 ("after a magic link, reconcile... verify login")**: because
  state is always re-derived, following any link and reloading lands on
  whatever `read_state()` actually finds next -- if that link happened to
  also authenticate, the next read naturally shows `SIGNED_IN` (or no
  account-step at all) and `next_step()` returns `NOTHING`; no separate
  reconciliation code is needed for this to be safe, only for it to be
  *observable* (see Gap G3).
- **Section 37 ("resume: inspect actual state, do not blindly replay")**:
  true for every call within one long-running process. `apply_flow.py`'s
  "Continue" button is the owner un-blocking `assistant.wait_for_signal()`
  inside the **same** process's `while True:` loop (confirmed:
  `apply_flow.py:1013`/`1794`/`1802` call `wait_for_signal` from inside loops
  started at `apply_flow.py:939`/`1618`) -- the same `PageAgent` instance,
  same in-process `Memory` sets, survive a pause/Continue cycle intact.

---

## 5. Genuine gaps (not already covered, confirmed by the searches above)

**G1 -- No distinct `AUTHENTICATION_STATE_UNKNOWN`/"unsupported MFA" states.**
A page showing SMS verification, an authenticator-app prompt, or a magic-link
message that `account_state.read_state()` cannot parse into one of its
twelve existing kinds falls through to `AccountState(NONE)` -- the *same*
value used for "this is not an account page at all." `next_step()` then
returns `Step(NOTHING)`, meaning `sign_in_step()` returns `False` and the
page is handled by the **ordinary** PageAgent flow (treated as a normal
application-form page to read and answer), not as an account/auth blocker
needing a precise handoff. In practice this usually self-corrects (an
unrecognized MFA page has no normal application fields to fill, so the
ordinary flow likely stalls and hands off anyway via the existing
`MAX_READS_PER_PAGE`/circuit-breaker machinery) -- but the handoff message in
that case is generic ("this page keeps asking the same things"), not the
precise "SMS verification is required for the X account" the architecture
document's Section 40 asks for by name. **This is the confirmed, real target
for this phase's implementation.**

**G2 -- No explicit `OUTCOME_UNKNOWN` for an interrupted sign-in.** The
create-account case already has one ("the new-account form is still showing
after the account was created" -> `FOR_OWNER`, via `memory.created`). The
analogous sign-in case has no equivalent: if `handle_auth_gate()` is
interrupted (an exception, a crash) between submitting credentials and the
page settling, the *next* read of `state.kind` governs entirely, with no
explicit "an attempt may be in flight" signal. In the overwhelmingly likely
case this is harmless (the next read sees whatever the page actually shows:
still `SIGN_IN_FORM` -> tries again, correctly, since nothing happened yet;
or `WRONG_PASSWORD` -> handled correctly, via the existing table; or
`SIGNED_IN` -> correctly stops trying). The one narrow, low-probability
window this does not cover: a process-level crash/restart landing on a
reloaded page that still shows the pre-submission form *even though the
submission itself was already processed server-side* (the common failure
mode would be the site's own duplicate-prevention catching it on retry --
`ACCOUNT_EXISTS` -> `OPEN_SIGN_IN` already exists for the account-creation
instance of this; sign-in has no duplicate-prevention-triggered equivalent
to recover through, because signing in twice is not usually
duplicate-rejected by a site the way creating two accounts is). Documented
as a residual risk, not fixed in this pass -- see "Deferred" below for why.

**G3 -- No observable distinction between a verification link and a magic
link.** Both are handled by the same `VERIFY_BY_LINK` path today. Section 4
above shows this is already *safe* (re-derivation), but it is not
*observable*: nothing in the logs or the owner-facing handoff distinguishes
"the account is verified, but you still need to sign in separately" from
"that link also signed you in." Low-severity (a cosmetic/observability gap,
not a correctness one) -- addressed in this pass via documentation and a
small logging improvement, not a behavior change.

**G4 -- ATS-adapter evidence beyond Workday.** Confirmed only
`sites/workday.py` overrides `candidate_account_state()`. Building equivalent,
individually-vetted adapters for Greenhouse, Lever, iCIMS, UKG,
SuccessFactors, Taleo, SmartRecruiters, ADP, and arbitrary custom portals
would require real recorded markup for each, the same way the Workday
wizard-marker evidence in P0-B1 required a real recorded page before it could
be trusted (and even then took several rounds to get right). **No such
recorded markup exists in this repository's saved-page corpus for any
non-Workday ATS's account/sign-in step specifically** (confirmed in Section 6
below). Fabricating adapters without that evidence would repeat exactly the
mistake P0-B1 spent five rounds correcting -- guessed, unvetted structural
assumptions. This is explicitly deferred, not fixed, and documented as such.

**G5 -- No authorized SMS/TOTP code-entry mechanism.** Confirmed: no SMS
gateway integration, no TOTP secret storage, no owner-facing configuration
for either, anywhere in this repository. Per the architecture document's own
Sections 22-23 ("use an explicitly authorized integration if the project
supports one... otherwise hand off") and this project's consistent policy of
never inventing a credential-adjacent mechanism without the owner's explicit
authorization (CLAUDE.md §5's precedent for exactly this kind of gating),
building a new SMS/TOTP integration is out of scope for this pass. The gap
this phase *can* and does close is making the hand-off for these cases
precise (G1), not inventing a way to bypass needing one.

---

## 6. Saved-page corpus: what account/auth evidence actually exists

Searched the full saved-page corpus (`output/*/pages/*.txt`,
`output/*/account/*.txt`, `runs/*/axtree_dump.json`, plus the raw
`output/**/evidence_*/page.html` captures used in the P0-B1 saved-page audit)
for account/sign-in-shaped content, and separately for MFA/SMS/TOTP/magic-link
wording. Full counts and the manual inspection of every `SIGNED_IN`/
`ACCOUNT_EXISTS`-classified page are in
`docs/security/phase0-b2-account-auth-verification.md`, Section "Saved-page
audit" (produced after the state-model changes below, so the classifier
version matches what is actually being shipped). The headline finding
relevant to scoping gap G4: every account-step page found in the corpus that
carries site-specific structural markers (`data-automation-id`-style
attributes) is from Workday; no non-Workday account/sign-in page in the
corpus carries comparable adapter-worthy structural evidence, confirming
there is nothing to build G4's adapters *against* yet, even if this phase
attempted to.

---

## 7. Credential sources (Section 7's audit)

Confirmed by reading every production read of a password-shaped value:

- `config.py` reads `ATS_PASSWORD` from `.env` (via the standard
  `os.environ`/dotenv loading path every other secret in this project uses);
  never generated, never guessed, never derived from any other value.
- `safety.password_allowed(url)` is the single gate checked before any
  password is typed anywhere (`browser_automation.handle_auth_gate` and
  every caller downstream of it) -- hard-blocks LinkedIn, Indeed, Dice,
  Google, Microsoft, Apple, Facebook domains outright, regardless of any
  account-state reasoning in this layer.
- The one-time-reset exception (CLAUDE.md §5) resets a rejected password to
  that *same* `ATS_PASSWORD` value -- confirmed by reading
  `page_agent._reset_refused_password`/`browser_automation.recover_rejected_sign_in`
  -- never a newly generated password, never a different one, gated by
  `login_guard.may_request_reset` (once per site per run) on top of
  `safety.password_allowed`.
- No password, OTP, or verification-link token appears in any production
  `logger.*()` call in `account_state.py`, `login_guard.py`,
  `emailed_codes.py`, or the `sign_in_step`/`handle_auth_gate` call chain
  (confirmed by reading every log statement in these paths; `why`/`state.why`
  values logged are the *site's own message text*, e.g. "Wrong email address
  or password," never the credential itself). `tests/test_secrets_out_of_snapshots_and_account_proof.py`
  already exists specifically to regression-test this property.

This fully satisfies Section 7's "document the exact allowed sources" ask; no
code change was needed or made here.

---

## 8. Scope decision for this phase

Given the above, this phase implements:

1. **G1** -- explicit MFA/SMS-verification/authenticator(TOTP) detection in
   `account_state.py`, with its own `kind` values and precise,
   architecture-document-Section-40-style `why` text, wired through the
   existing `next_step()` table to `FOR_OWNER` (reusing all existing
   machinery: `login_guard`, `Memory`, re-derivation-on-resume). No new retry
   logic is needed -- the existing "the owner acts, then Continue re-reads
   the page" loop already resumes automatically once the owner clears an
   `FOR_OWNER` hold, which is exactly Section 40's "after the user completes
   the step: detect the new state and continue automatically."
2. **G3** -- a small, additive logging/observability improvement
   distinguishing a verification-link outcome that also authenticated from
   one that did not, without changing any decision logic (the behavior is
   already correct; only its visibility is improved).
3. Full adversarial test coverage for the new detection (G1), using the same
   `form()`-builder and real-fixture conventions `tests/test_account_state.py`
   already establishes.
4. A full accounting of G2, G4, and G5 as explicitly deferred, with the
   reasoning recorded here and in the completion document, rather than
   either silently skipping them or fabricating unvetted work to appear to
   close them.

This is a genuinely scoped "architectural reliability pass," not a rewrite:
it extends one existing, well-tested module along its own existing seams,
touches no credential-handling code, and does not alter any P0-B1
submission-authority path (`account_state.py`, `login_guard.py`,
`emailed_codes.py`, and `field_requirements.py` have zero overlap with
`browser_automation.submission_step_finality()`, `page_agent._press_next_locked()`,
or any file in the P0-B1 verified-submit gateway chain -- confirmed by `grep`
across both domains finding no shared call sites).
