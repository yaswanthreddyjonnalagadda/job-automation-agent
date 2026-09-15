# What the assistant does, and what it never does

The agent prepares a job application end to end and then **stops before the
final Submit button**. Clicking Submit is the user's, always.

## What it may do

Read a job posting, tailor the resume, write a cover letter, open the
employer's application page, sign in, create an account, fill the form, upload
documents, answer questions its profile or resume actually support, validate
the result, and hand the finished application over.

## What it never does

| Rule | Where it is enforced | Test |
|---|---|---|
| Never clicks Submit | `browser_automation.py` has no `click_submit`; `refuse_to_submit()` answers a `submit` signal; wizard navigation skips any submit-labelled button; the web UI has no Submit button | `test_the_agent_has_no_way_to_click_submit`, `test_wizard_navigation_never_presses_a_submit_button` |
| Never accepts attestations or e-signatures | `safety.is_attestation()` guards every fill, checkbox and consent path; pending ones are listed for the user | `test_signature_fields_are_left_for_the_user`, `test_attestation_checkboxes_are_left_for_the_user` |
| Never guesses | answers come from `UserProfile` or the posting; an empty profile field means the question is left for the user; a dropdown answer must match an offered option | `test_people_managed_is_left_blank_when_the_profile_is_silent`, `test_field_of_study_is_never_swapped_for_another_subject` |
| Never overwrites the user | `safety.AgentValues` records what the agent wrote; anything else on the page is left alone | `test_a_users_answer_is_never_overwritten`, `test_site_prefilled_values_count_as_the_users` |
| Never types a Google password | `safety.password_allowed()` blocks Google/Apple/Microsoft and LinkedIn/Indeed/Dice; Google sign-in only picks the account | `test_passwords_are_refused_on_identity_providers` |
| Never bypasses a CAPTCHA | `safety.captcha_visible()` detects one, stops the sign-in/account step, and reports it | `test_captcha_is_detected_not_solved` |
| Never submits duplicates | `apply.already_submitted()` and `db.find_submitted()` match by URL (ignoring tracking parameters) and by company+title | `test_a_previously_submitted_job_is_refused` |
| Never claims unsupported facts | `safety.unsupported_claims()` rejects a tailored resume or letter that invents a figure, date or certification | `test_invented_numbers_and_certifications_are_caught` |

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

## Statuses

`prepared` → `form_filled` → `ready_to_submit` → `submitted`,
with `needs_user_review` whenever a person has to look, and `skipped` for a
job that was declined.

## Layout

| Path | Contents |
|---|---|
| `safety.py` | the rules above, in one place |
| `browser_automation.py` | reusable browser and form handling; no company names |
| `sites/` | per-platform selectors and workarounds (SuccessFactors, Workday, Eightfold, generic) |
| `job_sources.py` | reading a posting (Workday, Greenhouse, Lever, Ashby, Eightfold, schema.org, plain page) |
| `apply_flow.py` | one application from start to hand-over |
| `apply.py` / `web_ui.py` | entry points |
| `db.py` / `job_tracker.py` | tracker (Postgres, SQLite fallback) |
| `tests/` | the rules as tests: `venv\Scripts\python -m pytest tests -q` |

Legacy Workday wizard helpers (`fill_experience_section`, the searchable-input
and spinner helpers) still live in `browser_automation.py` and use Workday's
`data-automation-id` selectors through `sites/workday.py`. Moving them fully
into that adapter is the next cleanup.
