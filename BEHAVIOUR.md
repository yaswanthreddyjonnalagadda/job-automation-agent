# What the assistant does, and what it never does

The agent prepares a job application end to end and then **stops before the
final Submit button**. Clicking Submit is the user's — unless the user turns on
verified auto-submit (below), which is off by default and refuses on any
uncertainty.

## What it may do

Read a job posting, tailor the resume, write a cover letter, open the
employer's application page, sign in, create an account, fill the form, upload
documents, answer questions its profile or resume actually support, validate
the result, and hand the finished application over.

## What it never does

| Rule | Where it is enforced | Test |
|---|---|---|
| Never clicks Submit | `browser_automation.py` has no `click_submit`; `refuse_to_submit()` answers a `submit` signal; wizard navigation skips any submit-labelled button; the web UI has no Submit button | `test_the_agent_has_no_way_to_click_submit`, `test_wizard_navigation_never_presses_a_submit_button` |
| Signs attestations and e-signatures only when the owner allows it (`sign_attestations` in the profile; the owner's decision of 2026-09-17), last on the page and only when every other answer came from the profile | `safety.is_attestation()` finds them; `page_agent.PageAgent.sign()` signs or leaves them; pending ones are listed for the user | `test_signature_fields_are_left_for_the_user`, `test_attestation_checkboxes_are_left_for_the_user` |
| Never guesses | answers come from `UserProfile` or the posting; an empty profile field means the question is left for the user; a dropdown answer must match an offered option | `test_people_managed_is_left_blank_when_the_profile_is_silent`, `test_field_of_study_is_never_swapped_for_another_subject` |
| Never overwrites the user | `safety.AgentValues` records what the agent wrote; anything else on the page is left alone | `test_a_users_answer_is_never_overwritten`, `test_site_prefilled_values_count_as_the_users` |
| Never types a Google password | `safety.password_allowed()` blocks Google/Apple/Microsoft and LinkedIn/Indeed/Dice; Google sign-in only picks the account | `test_passwords_are_refused_on_identity_providers` |
| Never bypasses a CAPTCHA | `safety.captcha_visible()` detects one, stops the sign-in/account step, and reports it | `test_captcha_is_detected_not_solved` |
| Never submits duplicates | `apply.already_submitted()` and `db.find_submitted()` match by URL (ignoring tracking parameters) and by company+title | `test_a_previously_submitted_job_is_refused` |
| Never claims unsupported facts | `safety.unsupported_claims()` rejects a tailored resume or letter that invents a figure, date or certification | `test_invented_numbers_and_certifications_are_caught` |

## What it learns from you

Anything on the form the agent did not write is treated as **your** answer: it
is never overwritten, and it is remembered (`form_answers`, marked
`answered_by=user`). On the next application the same question -- however that
employer words it -- is answered from what you gave last time, before Claude is
asked for anything. An answer you typed outranks one the agent drafted, and a
remembered answer is only reused when it is among the options the new form
actually offers.

Fixed facts live in `config.py` (`UserProfile`) and are filled on every
application without asking: citizenship, clearance, years of experience,
salary range, education, EEO answers. A pay-band dropdown is answered with the
band that overlaps your range; a band outside it is left for you.

## Before it stops

`apply_flow.hand_over()` runs, in order:

1. **Validates required fields** (`validate_application`).
2. **Detects visible errors** shown by the form.
3. **Captures** a full-page screenshot, a field/answer summary, the page HTML
   and `validation.json`, all under `output/<Company>_<Title>/step_N/`.
4. **Sets the status**: `ready_to_submit` when nothing is outstanding,
   otherwise `needs_user_review` with the reasons.
5. **Brings the browser window to the front**.
6. **Prints a clear message** asking the user to review and click Submit; the
   web UI shows the same as a banner.

## After the user submits

The run keeps watching and records **submitted** only on evidence:

* a confirmation page ("Application Received", "Thank you for applying"), or
* the employer portal listing the job as applied (list reloaded every minute), or
* the employer's confirmation email (Gmail checked every 2 minutes).

If the application leaves the screen without any of that — the window is
closed, or the form is abandoned for 10 minutes — the status becomes
**needs_user_review**, never "submitted". Success is never assumed.

## Verified auto-submit (opt in, off by default)

Set `AUTO_SUBMIT_VERIFIED_ONLY=true` in `.env` to allow it. Even then, the
agent submits only when `safety.evaluate_auto_submit()` returns an eligible
`AutoSubmitDecision`, which requires **all** of:

* the job title, company and canonical URL match the tracked application
  (tracking parameters are not a difference);
* the attached resume and cover letter are the documents generated for this
  application, verified by SHA-256 against the stored copies, not by file name;
* every required field on the form **exactly** matches approved data — a value
  the agent wrote from your profile/resume, an explicitly approved answer from
  `data/_approved_answers.json`, or an existing value that equals a profile
  value. Near-misses are refusals: "Fairfax County" never matches "Fairfax";
* nothing uncertain is present: no blanks, form errors or warnings, no
  ambiguous dropdown choice, no unsupported custom question, no attestation,
  e-signature or consent checkbox, no CAPTCHA, no identity check.

Before submitting it writes, into `output/<Company>_<Title>/evidence_<time>/`:
a full-page screenshot, the page HTML, `comparison.json` (the field-by-field
report and every reason), and an audit row in `application_events`. The click
itself is re-checked for a CAPTCHA or attestation that appeared in between.
Afterwards the application is recorded **submitted** only once a confirmation
page, portal entry or confirmation email is found — clicking is not evidence.

Every decision, eligible or not, is written to the audit trail and shown on the
dashboard with its reasons.

## Statuses

`prepared` → `form_filled` → `ready_to_submit` → `submitted`,
with `needs_user_review` whenever a person has to look, and `skipped` for a
job that was declined.

## Layout

| Path | Contents |
|---|---|
| `safety.py` | the rules above, in one place |
| `browser_automation.py` | reusable browser and form handling; no company names |
| `sites/` | per-platform selectors and workarounds: Workday (its whole repeated-entry wizard), SuccessFactors, Eightfold, Greenhouse, Lever, Ashby, generic fallback |
| `job_sources.py` | reading a posting (Workday, Greenhouse, Lever, Ashby, Eightfold, schema.org, plain page) |
| `apply_flow.py` | one application from start to hand-over |
| `apply.py` / `web_ui.py` | entry points |
| `db.py` / `job_tracker.py` | tracker (Postgres, SQLite fallback) |
| `tests/` | the rules as tests: `venv\Scripts\python -m pytest tests -q` |

Workday's repeated-entry wizard (`fill_experience_section`, the date spinners,
searchable inputs and button dropdowns — about 760 lines) now lives in
`sites/workday.py`; `browser_automation.py` keeps thin wrappers that delegate
to whichever adapter handles the page. A few shared selector constants are
imported from the adapter module by generic code, which is intentional.

## The browser it uses

Real Google Chrome (`BROWSER_CHANNEL=chrome`), not a test build, so a form
renders and behaves as it does for you; the agent falls back to Playwright's
bundled browser if Chrome is missing. It runs in its own profile
(`./browser_profile`) rather than your everyday one, which keeps your normal
browsing, cookies and extensions out of an application run -- and lets you
carry on using Chrome while an application is open. Point
`BROWSER_PROFILE_DIR` at your own Chrome profile to reuse its logins instead;
Chrome must then be closed while the agent runs, since one profile cannot be
open in two browsers.

## Operational detail

* **Retries** — flaky page actions are retried (`with_retries`, `action_retries`).
* **Progress is saved** — the form's own Save button is clicked before handing
  over, so a part-finished application survives a reload.
* **Structured logs** — each run also writes `logs/run_<timestamp>.jsonl`.
* **No secrets in logs** — every log line passes through `safety.redact()`,
  which masks API keys, passwords, email addresses and phone numbers.
* **Dashboard** — each application's page shows progress, what is still
  outstanding, the field-by-field comparison, evidence links and the full event
  history. It is served on 127.0.0.1 only.
