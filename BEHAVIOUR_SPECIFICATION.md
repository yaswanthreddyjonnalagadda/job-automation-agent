# BEHAVIOUR_SPECIFICATION.md - State Machine & Recovery Rules

This document describes the DOM-interaction state machine used while
filling a single application. `BEHAVIOUR.md` remains the governing
document for consent, attestations, credentials, and submission -- this
flow must never bypass it, and where the two ever seem to disagree,
`BEHAVIOUR.md` wins.

---

## 1. Primary Execution Flow

1. **Launch** -- open the configured browser (real Chrome by default) with
   its own persistent profile, so forms render as they do for the user.
2. **Pre-Flight Sweep** -- detect and clear cookie banners and non-essential
   modal popups.
3. **Authentication** -- if login is required, wait for the human to log in;
   the session persists in the browser profile afterward. Never automate
   login for Google, Microsoft, Apple, LinkedIn, Indeed, or Dice. An ATS
   account may use the configured credentials, but a broken login is
   surfaced to the human, not auto-recovered (no credential vault, no
   generated passwords). One owner-approved exception: when the page says an
   account already exists for the owner's own email, sign in with the existing
   `ATS_PASSWORD`, and if the site rejects it, reset the password to that same
   value with the one-time code emailed to the owner (see `BEHAVIOUR.md`,
   "An account that already exists").
4. **Compliance Audit** -- scan the posting for non-sponsorship language
   against the profile's visa-sponsorship requirement; stop and record the
   job as skipped on a conflict, before spending any tokens on tailoring.
5. **Form Execution**
   - Fill fields via `fill_and_dispatch` (the full synthetic event
     sequence).
   - Resolve custom comboboxes via wrapper `mousedown` and the detached
     option portal.
   - Attach the tailored resume (and cover letter, when the form requires
     one) generated earlier in the pipeline.
6. **Pre-Progression Sweep** -- commit any open Work/Education draft cards
   before advancing.
7. **Progression** -- click Next / Save & Continue between wizard steps
   only, never a Submit-labelled control, and wait for network idle and any
   loading spinner to clear.
8. **Self-Healing** -- on a visible validation error, re-scan and correct up
   to 3 times before treating the field as blocked and reporting it.
9. **Circuit Breaker** -- if the state fingerprint
   (`url + step indicator + visible-input count`) repeats 3 times in a row,
   stop and report a blocked loop rather than continuing to retry.
10. **Hand-Over** -- once nothing required is outstanding, run the existing
    validation and evidence capture (screenshot, HTML, field/answer
    summary) and stop for human review, exactly as `apply_flow.hand_over()`
    already does. The only path to an actual Submit click is the separate,
    opt-in, strictly-verified auto-submit gate in
    `safety.evaluate_auto_submit()` -- this state machine does not add a
    second one.
