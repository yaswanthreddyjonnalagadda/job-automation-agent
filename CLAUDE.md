# CLAUDE.md - Core Project Operational Rules

## 1. Project Overview & Philosophy
- **Objective:** a human-reviewed job application co-pilot across ATS
  platforms (Workday, Greenhouse, Lever, Ashby, SuccessFactors, and similar
  portals). It fills and validates applications and stops for human review
  before submission. `BEHAVIOUR.md` is the authority on consent,
  attestations, credentials, and submission -- nothing in this file
  overrides it.
- **Primary runtime:** `apply.py` -> `apply_flow.py`. `main.py` is
  deprecated.
- **Core standard:** accessibility-first perception (roles and labels) and
  synthetic event dispatching, over volatile CSS selectors.

## 2. Universal Perception & DOM Standards
- Query interactive elements by accessibility roles (`role`, `aria-label`,
  `aria-expanded`) or their associated `<label>`/`<legend>` text -- not by
  generated CSS class hashes.
- Traverse shadow DOM and iframe contexts when a control isn't found in the
  main document.
- If an input has no accessible label, fall back to proximity to the
  nearest preceding label/legend/heading rather than guessing from position
  alone.

## 3. Interaction & Virtual DOM Primitives
- **Universal event bubbling (`fill_and_dispatch`):** React/Vue/Ant Design
  controlled components can discard a raw value injection. Every fill
  sequences: focus -> `fill(value)` -> dispatch `input` -> dispatch
  `change` -> blur, each bubbling.
- **Detached dropdowns** (Ant Design `rc-select` and similar): trigger the
  wrapper via `mousedown` (the native input is often
  `pointer-events: none`), wait for the option list mounted at
  `document.body`, match by visible text, click, and verify the portal
  detaches.
- **Pre-progression card commits:** before clicking a wizard's Next/
  Continue, sweep for any inline Work/Education entry still in edit mode
  and click its own Save/Update/Done first (see `sites/workday.py` for the
  existing implementation).

## 4. Barrier & Modal Handling
- Sweep for modals and cookie banners on page settle and right after
  navigation clicks.
- For a long policy/terms modal, scroll its internal viewport to the bottom
  before looking for a confirm button.
- Any checkbox that reads as a legal attestation, e-signature, or consent
  per `safety.is_attestation()` is handled exactly as `BEHAVIOUR.md`
  specifies -- never asserted automatically outside that gate.

## 5. Authentication
- Login is the human's step. Never type a password for Google, Microsoft,
  Apple, LinkedIn, Indeed, or Dice -- `safety.password_allowed()` enforces
  this in code; do not work around it.
- ATS account credentials (Workday/Greenhouse/Lever/etc. accounts the user
  created themselves) come only from the configured `.env` values. Do not
  build automated password-reset, OTP-interception, or credential-vaulting
  flows. If a login is broken, surface it to the user rather than
  automating around it.

## 6. Safety, Compliance & Circuit Breakers
- **Sponsorship guardrail:** scan for non-sponsorship language; if the
  profile requires sponsorship and the posting excludes it, stop and record
  the job as skipped -- never proceed to fill the form.
- **Circuit breaker:** track a fingerprint of
  `(url, step indicator, visible-input count)`. If identical across 3
  consecutive progression attempts, stop and report the loop rather than
  retrying indefinitely; capture a screenshot, the HTML, and an
  accessibility snapshot for debugging.
- **Submission:** never implement a code path that clicks Submit outside
  `apply_flow.hand_over()`'s existing review gate, or the strictly-verified
  auto-submit path in `safety.evaluate_auto_submit()`. See `BEHAVIOUR.md`.
