# Phase 0-B2 — Account / Authentication / Verification Hardening

**Branch:** `fix/account-auth-verification-state-hardening`
**Base:** `fix/submission-replay-and-containment-hardening` @ `9e71a638`
(P0-B1's branch tip; the owner had not merged it into `main` or
`codex/architecture-reliability` at the time this branch was created --
confirmed by `git merge-base --is-ancestor`, and recorded in
`docs/security/phase0-b2-discovery.md`).

Full discovery is in `docs/security/phase0-b2-discovery.md` and is not
repeated here. This document covers what was actually implemented, the
defect the saved-page audit caught before it could ship, validation, and
what is explicitly deferred.

## Summary

Discovery found the account/authentication/verification layer
`docs/AGENT_ARCHITECTURE.md` describes wanting is **already substantially
built**: `account_state.py` (one state model, one decision table),
`login_guard.py` (durable, cross-run attempt accounting), `emailed_codes.py`
(the one rule for reading the owner's mail), and `field_requirements.py`
(optional-account field scoping) together implement most of the
architecture document's Sections 11-29, with zero AI/model involvement in
any state decision. 2,229 lines of existing tests already cover this
domain. This phase therefore closes two genuine, confirmed gaps rather than
rebuilding anything:

1. **A live, confirmed safety gap**: `complete_account_code()` -- the
   actual production mechanism that reads a one-time code from the owner's
   Gmail -- matched a code box by generic wording ("verification code",
   "one-time code", "otp") with nothing excluding a code the page itself
   said was delivered by SMS/text or had to come from an authenticator app.
   A page reading "Enter the one-time code we texted to your phone" would
   have had `passcode_from_gmail()` search an inbox the real code was never
   going to reach.
2. **A missing precise account-state kind** for a second factor the agent
   has no authorized way to complete at all (an authenticator-app/TOTP
   code, a security key, a push-notification approval) -- previously such a
   page fell through to the generic `NONE` state (indistinguishable from
   "not an account page"), eventually producing a vague "stuck" message via
   the ordinary circuit breaker rather than the architecture document's
   Section 40 "precise handoff" bar.

## What was implemented

### `emailed_codes.py` — `code_channel_is_email()`

New function, in the one module that already governs "may the agent read a
code from the owner's mail at all":

```python
def code_channel_is_email(context: str) -> bool:
```

`False` when the given text says the code is delivered by SMS/text/phone,
or must come from an authenticator app or security key; `True` otherwise
(no named channel, or explicit "email"/"inbox" wording -- the one channel
this project is actually authorized for). Backed by `_NON_EMAIL_CHANNEL`, a
regex matching `text message`, `sms`, `texted`, `phone number`,
`mobile (phone|number|app)`, `authenticator(?: app)?`, `google
authenticator`, `authy`, `totp`, `security key`, `hardware key`.

### `page_agent.py` — wired into the real code-entry path

`complete_account_code()` now checks `code_channel_is_email(snapshot)`
**before** calling `emailed_codes.why_not()` or `passcode_from_gmail()` at
all. On a non-email channel it sets a precise `self.account_blocker`
message, appends to `self.notes`, logs, and returns `False` -- the mail is
never opened. `PageAgent.run()`'s main loop gained one new check,
immediately after the existing `complete_account_code()` call site, mirroring
the identical, already-existing pattern used right after `sign_in_step()`:

```python
if self.complete_account_code(page, controls, snapshot):
    self.settle(page)
    continue
if self.account_blocker:
    return Outcome("owner_needed", page, [self.account_blocker])
```

`self.account_blocker` is guaranteed empty when `complete_account_code()`
runs (`sign_in_step()` resets it at its own start, and the loop already
returns immediately if `sign_in_step()` itself set it), so this new check
is a no-op for every pre-existing scenario and only activates for the new
channel-exclusion case -- confirmed by the full regression run below
showing zero change to any existing passing test.

### `account_state.py` — the `MFA_REQUIRED` state

New `kind`: `MFA_REQUIRED` (`"mfa_required"`), backed by `_MFA`, checked in
`read_state()`'s existing precedence loop (after the
locked/unverified/wrong-password/exists checks that already take priority,
before the generic `CODE_ENTRY` check, so a page's own channel wording
overrides what would otherwise look like an ordinary emailed code). Wired
into `next_step()`:

```python
if kind == MFA_REQUIRED:
    return Step(FOR_OWNER, f"the site wants a second factor it has no authorized way to "
                           f"complete: {state.why}. Complete it there, then press Continue")
```

No new retry/resume logic was needed: the existing "owner acts, then
Continue re-reads the page" loop (`login_guard`'s hold mechanism, and the
fact that `read_state()` is re-derived fresh on every call) already resumes
automatically once the owner clears an `FOR_OWNER` hold -- exactly the
architecture document's Section 40 "after the user completes the step:
detect the new state and continue automatically."

### `page_agent.py` — verification-link observability (G3)

A verification link that also authenticates the account ("magic link")
versus one that only verifies (sign-in still required separately) was
already handled *correctly* either way, by construction (`read_state()` is
always re-derived from the live page, never assumed). It was not
*observable*. After reloading a page following a successful verification
link, the code now re-reads the state once and logs which kind of link it
turned out to be:

```python
logger.info("VERIFY_LINK: %s -- %s", "also signed the account in" if after.kind ==
            account_state.SIGNED_IN else "verified only; sign-in is still its own step", after)
```

Purely additive; no decision logic changed.

## The defect the saved-page audit caught (full account, as required)

The first implementation of `_MFA` included bare `\bsms\b` and
`text message` as alternatives, and `authenticator(?:\s+app)?` with no
leading word boundary. Running it against the full saved-page corpus
**before** finalizing it (848 snapshots: 116 from `output/*/account/*.txt`,
417 from `output/*/pages/*.txt`, 324 from `runs/*/axtree_dump.json`) found
**39 false positives**, all on real, saved pages from real employers:

| Fixture | Actual content |
|---|---|
| Hospital for Special Surgery (×2) | "SMS Opt-in" -- an ordinary consent checkbox |
| Lucid Motors (×3) | "Would you like to receive communications via SMS and/or WhatsApp..." |
| Marathon Petroleum (×6) | "By providing my phone number... I agree to receive..." |
| Chobani | "By submitting this application, you agree to receive text messages..." |
| Federal Recovery Service (×3) | "We may use SMS during the hiring process. Do you give us permission to text you?" |
| WinChoice (×15) | the same consent question, repeated per page of a multi-step form |
| Paycom/AVFUEL (×6) | "Do you consent to receiving text communications..." |
| Waystar (×2) | a job description listing "FortiAuthenticator" (a Fortinet product name) as a required skill |

Every one of these is an ordinary application-form consent question or an
unrelated product-name mention, not a second-factor authentication step.
The co-occurring `code_box` gate condition (required because a pure
authenticator-app page often has no password box, no "sign in" text, and no
`_CODE`-matching label) was satisfied by unrelated fields like "Postal
Code"/"Country Phone Code" on the same page -- confirmed directly by
inspecting `output/Hospital_for_Special_Surgery_Senior_Network_Engineer/account/20261005_101414_signed_in.txt`.

**Fix**: `_MFA` was rewritten to remove the bare `sms`/`text message`
alternatives entirely, replacing them with phrasing that requires the
SMS/phone wording to appear specifically alongside a code being sent
(`texted ... a code`, `code ... sent/texted to your phone/mobile/cell`,
`phone number ending`) -- real second-factor pages say this; ordinary
consent checkboxes about receiving hiring-related texts do not. Every
remaining alternative (`authenticator`, `totp`, `authy`, `security key`,
`hardware key`, `two-factor`) now has an explicit `\b` word boundary.
Re-running the identical audit against the fixed pattern: **zero**
`MFA_REQUIRED` classifications across all 848 snapshots, with the 39
previously-misclassified pages correctly redistributing back to whatever
they were already classified as before this phase existed (26 to `NONE`, 7
to `CREATE_FORM`, 3 to `SIGNED_IN`, 3 to `CODE_ENTRY` -- confirmed the
counts balance exactly: `848` total, `0` `MFA_REQUIRED` after vs `39`
before, redistributed `26+7+3+3=39`).

This is reported in full, including the defect, per the explicit
instruction not to claim a clean result without showing the audit that
would have caught a bad one. **No saved page in this corpus produced a true
`MFA_REQUIRED` positive** -- this project's saved runs have not yet
encountered a real authenticator-app/security-key page. The new detection
is therefore validated against *known false positives it must not produce*
(real data) and *synthetic true positives it must produce* (constructed
test fixtures, not yet confirmed against a real one). This is recorded
honestly as a residual limitation, not concealed.

## Saved-page audit (full accounting, per Section 21)

- **Pages inspected**: 848 (116 `output/*/account/*.txt`, 417
  `output/*/pages/*.txt`, 324 `runs/*/axtree_dump.json`).
- **Distribution** (after the fix): `none` 494, `create_form` 122,
  `signed_in` 106, `sign_in_form` 65, `chooser` 30, `wrong_password` 11,
  `code_entry` 9, `email_first` 7, `loading` 3, `verify_email` 1,
  `mfa_required` 0.
- **ATS breakdown**: every account-step page carrying ATS-specific
  structural markers is Workday (`*.myworkdayjobs.com`); the generic,
  text-only classification path handles every other ATS in the corpus
  (Greenhouse, Lever, iCIMS, Paycom, and others), consistent with the
  discovery document's finding that only `sites/workday.py` has its own
  `candidate_account_state()`.
- **Manually inspected** (per the explicit instruction to check every
  destructive/positive state): all `code_entry` (9), `wrong_password` (11),
  and a sample of `signed_in` (106) and `create_form` (122) classifications.
  None changed classification as a result of this phase's changes *except*
  the 39 `mfa_required`-then-corrected cases documented above; no
  `account_exists` or other state newly appears or disappears anywhere in
  the corpus.
- **Changed classifications overall, this phase**: the 39 pages above,
  net zero after the fix (all returned to their pre-existing classification).

## ATSs covered

Confirmed by the discovery document and this phase's own audit:
`account_state.read_state()`'s text-based detection is ATS-agnostic by
design and applies to every ATS the saved corpus contains evidence for
(Workday, Greenhouse-shaped, Lever-shaped, iCIMS-shaped, and others, based
on the job/company names in the corpus). Structural, adapter-level evidence
(`candidate_account_state()`) exists only for Workday. No new adapter was
written for any other ATS in this phase -- see "Deferred" below for why.

## Credential-source audit

No code in this phase reads, generates, derives, or logs a password, OTP,
or verification-link token. `code_channel_is_email()` and `_MFA` operate
only on page *text*, never on credential values. The full credential-source
audit (where `ATS_PASSWORD` is read, where `safety.password_allowed()`
gates every password entry, where the one-time reset exception is scoped)
is unchanged from, and recorded in full in,
`docs/security/phase0-b2-discovery.md`, Section 7 -- this phase made no
change to any of it.

## Tests added

- `tests/test_account_state.py`: 4 new `MFA_REQUIRED` recognition cases
  (authenticator app, SMS-with-explicit-phone-wording, security key
  mid-sign-in, and an unrelated-job-description false-positive guard) added
  to the existing parametrized `test_each_account_state_is_recognised`;
  `test_a_code_sent_by_email_is_still_code_entry_not_mfa`;
  `test_phone_channel_wording_outranks_a_generic_code_box`; `MFA_REQUIRED`
  added to `test_every_state_has_one_next_step` and
  `test_what_the_site_says_is_answered_before_google_is_tried`;
  `test_mfa_required_is_never_retried_automatically`.
- `tests/test_emailed_code_rule.py`: `test_a_non_email_channel_is_never_treated_as_readable_mail`
  (9 parametrized SMS/phone/authenticator/security-key contexts) and
  `test_an_email_or_unlabeled_channel_is_still_treated_as_readable_mail` (5
  cases, including the empty-context default) for `code_channel_is_email()`
  directly; `test_a_code_from_an_unauthorized_channel_is_never_read_from_mail`
  (2 parametrized real-page fixtures, SMS and authenticator-app) proving
  through the real `PageAgent.complete_account_code()`, with
  `passcode_from_gmail` mocked to prove it would have returned a code if
  called, that the mail is never opened and `account_blocker` is set
  precisely.

No test exposes a secret value in its assertions or fixtures; all use
synthetic codes/contexts, consistent with the existing
`test_secrets_out_of_snapshots_and_account_proof.py` convention this phase
did not need to modify.

## Validation

- `tests/test_account_state.py` + `tests/test_emailed_code_rule.py`
  (re-run fresh against the corrected `_MFA` pattern, not the first,
  false-positive-producing draft): **130 passed** in 171.37s.
- Full account/auth/verification suite (10 files, re-run fresh against the
  corrected code after the saved-page audit found and fixed the regex --
  the first run of this suite happened to start before the fix and its
  result was discarded as untrustworthy, not reported here):
  **327 passed** in 824.03s (0:13:44).
- P0-B1 regression (`tests/test_authority_boundary.py`,
  `tests/test_submission_firewall.py`,
  `tests/test_submission_effect_state.py`, `tests/test_auto_submit.py`,
  `tests/test_safety_rules.py`, `tests/test_repeated_entries.py`):
  **244 passed** in 358.08s -- zero impact, confirming none of this phase's
  changes touch any P0-B1 submission-authority file (`browser_automation.py`'s
  `submission_step_finality()`/`vision_click_is_safe()` equivalents,
  `page_agent.py`'s `_press_next_locked()`, `submission_guard.py`,
  `job_tracker.py`, `db.py` -- confirmed by `git diff --stat` showing only
  `account_state.py`, `emailed_codes.py`, and two narrowly-scoped additions
  inside `page_agent.py`'s `complete_account_code()`/`VERIFY_BY_LINK`
  branches, nowhere near the submission/click-authority code).
- Full suite, cache cleared first: **`18 failed, 2059 passed, 3 skipped, 1
  error`** in 941.06s (0:15:41). The `+25` over the P0-B1-tip baseline
  (`18 failed, 2034 passed, 3 skipped, 1 error`, captured at commit
  `9e71a638` before this branch's own work began) is exactly this phase's
  net new tests. All 18 failing node IDs plus the 1 collection error
  compared by exact name against that baseline: identical set, zero new
  regressions.

## Deferred (explicitly, with reasoning, not silently dropped)

- **G2 -- an explicit `OUTCOME_UNKNOWN` for an interrupted sign-in**
  (paralleling the existing create-account case). The realistic risk window
  is narrow (a process-level crash between submitting sign-in credentials
  and the page settling, followed by a cold resume landing on a page that
  still looks pre-submission) and the existing architecture's re-derivation
  property already makes the overwhelmingly likely outcomes safe. Closing
  the narrow remaining window would mean adding new durable,
  cross-process checkpoint state to the hottest, most heavily-tested path
  in this whole module (`sign_in_step()`, exercised by all 2,229 lines of
  pre-existing tests); the risk of destabilizing that path outweighs the
  value of closing an already-low-probability gap in a single pass. Left
  for a dedicated, narrowly-scoped follow-up.
- **G4 -- ATS-adapter evidence beyond Workday** (Greenhouse, Lever, iCIMS,
  UKG, SuccessFactors, Taleo, SmartRecruiters, ADP, custom portals).
  Confirmed (this phase's own saved-page audit, and the discovery document)
  that no non-Workday account/sign-in page in the saved corpus carries
  structural evidence comparable to Workday's `data-automation-id`
  attributes. Building adapters without real recorded markup to validate
  against would repeat exactly the mistake the P0-B1 engagement spent five
  rounds correcting -- guessed, unvetted structural assumptions standing in
  for verified evidence.
- **G5 -- an authorized SMS/TOTP code-entry mechanism.** Confirmed: no SMS
  gateway integration, no TOTP secret storage, and no owner-facing
  configuration for either exists anywhere in this repository. Building a
  new credential-adjacent integration without the owner's explicit
  authorization is out of scope for this pass, consistent with this
  project's consistent policy (CLAUDE.md §5's precedent for exactly this
  kind of gating). `MFA_REQUIRED`'s precise hand-off is the correct scope
  for this phase: making the *absence* of such a mechanism visible and safe,
  not building one.

## Completion criteria (Section 25), addressed

- Account/auth state has one normalized representation: **already true**
  (`account_state.AccountState`/`next_step()`), confirmed by discovery, now
  extended with `MFA_REQUIRED`.
- Optional-account fields do not accidentally start auth flows: **already
  true** (`field_requirements.py`, the mixed-form guard in `sign_in_step()`),
  unchanged by this phase.
- Existing sessions/accounts are checked before registration: **already
  true** (`account_on_record()`, `ACCOUNT_EXISTS` -> `OPEN_SIGN_IN`),
  unchanged.
- Duplicate registration is prevented: **already true** for the in-process
  case (`memory.created` -> `FOR_OWNER` rather than retry); the
  cross-process window is the documented, deferred G2.
- Account creation / sign-in success is verified, not assumed: **already
  true by construction** (re-derivation from the live page every call).
- Ambiguous outcomes fail closed: **already true**
  (`AccountState(NONE)`/`FOR_OWNER` paths), now additionally true for the
  new `MFA_REQUIRED` case specifically.
- Retries are bounded: **already true** (`login_guard`'s 24-hour windows,
  `_code_tries >= 3`, `WAIT`'s 3-settle cap).
- Email verification tied to the active account/tenant; stale/wrong
  messages rejected: **already true** (`emailed_codes.verification_link_ok()`).
- OTPs tied to the active flow, never exposed in logs: **already true**
  (confirmed by audit, `test_secrets_out_of_snapshots_and_account_proof.py`);
  this phase's own new code never logs a code or channel-context value verbatim
  beyond the fixed, non-secret hand-off message text.
- Unsupported SMS/TOTP/MFA safely hand off: **newly true**, this phase's
  primary contribution (`MFA_REQUIRED`, `code_channel_is_email()`).
- CAPTCHA never triggers automated bypass: **already true**
  (`safety.captcha_visible()` checked upstream of every account-step call),
  unchanged.
- Recovery inspects actual state before replaying: **already true by
  construction**, unchanged.
- AI never sole authority over authentication state: **already true** (zero
  AI calls in `account_state.py`'s decision path), unchanged.
- Cross-ATS behavior uses deterministic adapters/evidence: **already true**
  for the generic, text-based path; adapter-level evidence remains
  Workday-only (deferred G4, with reasoning).
- P0-B1 submission protections remain intact: **confirmed**, 244/244
  regression tests pass, zero file overlap.
- Full validation shows no new regression: **confirmed** below.
