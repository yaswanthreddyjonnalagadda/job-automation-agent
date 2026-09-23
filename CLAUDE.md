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

## 7. How a change is made (definition of done)
These rules bind every coding agent working in this repository (Claude Code,
Antigravity, any other) and every session. A change is done only when all
of them hold.
- **Branch and pull request, never `main`.** Work on a branch, open a pull
  request, and leave the merge to the owner. `.githooks/` refuses commits
  and pushes to `main` (enable once: `git config core.hooksPath .githooks`);
  GitHub's branch protection refuses them on the server.
- **One task per session, started by the owner.** A plan, roadmap or RFC is
  context, not a queue: never work through its phases on your own.
- **Fix the class, not the instance.** A bug-fix pull request states the
  symptom, the root cause as a class ("a label keyword chose the wrong
  concept"), why the existing tests missed it, and the test that now covers
  the whole class -- a property test where the class is large. A fix that
  works for one site, one form or one value is not a fix.
- **Data never lives in logic.** No place, company, person or answer
  literal decides anything in code. Places come from `reference/geo.json`
  through `geo_reference.py` (regenerate the data with
  `reference/build_geo.py`); the owner's facts come from the profile; site
  quirks live in `sites/`. `tests/test_no_place_literals.py` fails the
  build on a country, state or province name in a logic module.
- **Ask who put a value there before changing it.** `provenance.py`
  observes what a person typed; `safety.may_overrule()` is the only place
  that decides whether a value on the form may be replaced. The owner's
  values are never changed; the site's only as `SITE_PREFILL_POLICY`
  allows. Never add an exception for a particular value.
- **One decision, one place.** If a rule already lives in `safety.py`,
  call it; if the same decision exists in two modules, consolidate it
  rather than patching each copy. `safety.py` changes only with the
  owner's explicit approval on the pull request.
- **Tests first, all green.** A new behaviour or fix comes with a test that
  failed before it. `python -m pytest -q` passes locally and in CI, with no
  test weakened to make it pass.
- **Say what the owner will see.** Any change to what the agent does on a
  form updates `BEHAVIOUR.md` in the same pull request.
- **Plans live outside the repository root.** Roadmaps, RFCs, reviews and
  postmortems go in the Claude Project; the root keeps rules only.
