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
| Never guesses | answers come from `UserProfile` or the posting; an empty profile field means the question is left for the user; a dropdown answer must match an offered option, found in the whole list (a long list is scrolled through, not judged by its first rows), and counts as given only once the list shows it | `test_people_managed_is_left_blank_when_the_profile_is_silent`, `test_field_of_study_is_never_swapped_for_another_subject`, `test_the_country_far_down_the_list_is_the_one_chosen`, `test_an_answer_the_list_does_not_offer_is_left_for_the_owner` |
| Never overwrites the user | `safety.AgentValues` records what the agent wrote and `provenance.py` observes what a person typed or chose; a value the owner entered is never changed. A value the *site* filled in is left alone too, except the owner's own details (name, country, state, city, and the sponsorship/authorization answers) where they contradict the profile -- see "Values the site filled in" below. `safety.may_overrule()` is the only place this is decided | `test_a_users_answer_is_never_overwritten`, `test_site_prefilled_values_count_as_the_users`, `test_the_owners_own_choice_is_never_overwritten`, `test_a_country_the_owner_typed_is_never_corrected`, `test_one_rule_decides_who_may_be_overruled` |
| Never assumes a place | countries and states come from the profile and `reference/geo.json`; no place name is written into the code, and a place the profile leaves empty is left for the user | `test_no_logic_module_spells_a_place`, `test_nothing_is_assumed_when_the_profile_names_no_place` |
| Never types a Google password | `safety.password_allowed()` blocks Google/Apple/Microsoft and LinkedIn/Indeed/Dice; Google sign-in only picks the account | `test_passwords_are_refused_on_identity_providers` |
| Never bypasses a CAPTCHA | `safety.captcha_visible()` detects one, stops the sign-in/account step, and reports it | `test_captcha_is_detected_not_solved` |
| Never submits duplicates | `apply.already_submitted()` and `db.find_submitted()` match by URL (ignoring tracking parameters) and by company+title | `test_a_previously_submitted_job_is_refused` |
| Never claims unsupported facts | `safety.unsupported_claims()` rejects a tailored resume or letter that invents a figure, date or certification | `test_invented_numbers_and_certifications_are_caught` |

## What it learns from you

Anything **you** type or choose on the form is your answer: it is never
overwritten, and it is remembered (`form_answers`, marked `answered_by=user`).
On the next application the same question -- however that employer words it --
is answered from what you gave last time, before Claude is asked for anything.
An answer you typed outranks one the agent drafted, and a remembered answer is
only reused when it is among the options the new form actually offers. What
the site filled in by itself (a resume parser's guess) is not learned as
yours.

Fixed facts live in `config.py` (`UserProfile`) and are filled on every
application without asking: citizenship, clearance, years of experience,
salary range, education, EEO answers. A pay-band dropdown is answered with the
band that overlaps your range; a band outside it is left for you.

## Questions it could not answer

Each required question the agent leaves for you is kept, once, in
`data/unanswered_questions.json`: the question as the form words it, why it was
left, the choices the form offered, every site and company it came up on, how
many times, and the line that answers it (`answer_key`). Add that line and your
answer to `data/profile_answers.json` and the agent answers it itself from then
on, on every form that asks it in any wording the pattern covers; the entry then
shows the answer. Both files are plain JSON kept on your machine (not committed),
so the whole set can be carried to the next project. Nothing personal is written
into the reasons (emails and phone numbers are masked), and a file that is
damaged or cannot be written never stops a run.

## Values the site filled in

Many forms arrive partly filled: a resume parser guesses a country, or a list
shows its first entry (an alphabetical country list starts with Afghanistan or
the Åland Islands). The agent tells these apart from your own answers by
watching the page: a small observer (`provenance.py`) records every control a
person really types into, changes or chooses from a list for, while the agent
is waiting for you -- the browser marks such input as trusted, and a site's
scripts cannot produce it.
So every value on the form is known to be empty, the agent's, **yours**, or
**the site's**; on a page the observer cannot see into, it is unknown.

* **Yours** is never changed, whatever it says.
* **The site's**, where it contradicts your profile's country, state or city:
  with `SITE_PREFILL_POLICY=correct` (the default) the agent puts it right from
  your profile, country first (the state list depends on it), and lists each
  correction at hand-over ("corrected 'Country' from 'Afghanistan' to ...").
  With `SITE_PREFILL_POLICY=leave` it keeps the site's value and lists it for
  you to fix.
* **Unknown** is left as it is and listed for you.
* Your name, and the sponsorship and work-authorization answers, are put right
  from your profile whenever the site's contradict them (your standing rule,
  whatever `SITE_PREFILL_POLICY` says), unless you set them yourself; each
  correction is listed at hand-over.
* A work or education entry's location belongs to that job or school and is
  never replaced with your home address.

Which question a dropdown asks is confirmed by what it offers: a list of
countries is a country question even when its label says "Region of
Residence", so a state is never offered to a country list.

## Which resume it attaches

`RESUME_SOURCE=tailored` (the default) attaches the resume tailored to this job
-- the one the verified auto-submit gate checks by SHA-256. `RESUME_SOURCE=master`
attaches your standard resume, `assets/master_resume.pdf`, instead.

A form that asks for a resume (an upload it recognises, or a "Resume/CV *"
label) and does not show the tailored resume at its last step is never handed
over as `ready_to_submit`, whether or not automatic submission is on: the run
stops as `needs_user_review` with "the tailored resume is not attached". A form
with no resume field is unaffected.

## Which model answers the form, and which writes the documents

`FORM_ANSWER_MODE` decides who works out the answers on each application page:

* `profile` -- nobody: every answer comes from your saved profile and answer
  library, and Claude only writes the documents;
* `claude` -- Claude plans each page, through the Anthropic API or, with
  `AGENT_BRAIN=session`, through the Claude Code session;
* `gemini` -- Google's Gemini plans each page (your decision of 25 September
  2026, after the Anthropic account reached its usage limit). `AGENT_BRAIN` is
  not consulted.

In every mode Claude writes the resume and the cover letter; Gemini never does,
and its client refuses to. Everything that answers a question goes to Gemini in
`gemini` mode: planning a page, reading a screenshot, matching a dropdown choice,
screening answers.

What leaves your computer: in `gemini` mode every page the agent reads, together
with the facts it answers from (your profile and the text of your resume), goes
to Google; the documents go to Anthropic. Nothing else changes: the agent's own
rules -- attestations, sponsorship, CAPTCHAs, submitting only when every check
passes -- decide what is done with an answer, whoever proposed it. The key
(`GEMINI_API_KEY`) travels in a request header, never in an address, and is never
written to a log. If it is missing, the run says so before it starts; if it does
not look like a Google key (`AIza` and 39 characters, or `AQ.` and longer) it says
that too, without printing it. If Google has retired the model (`GEMINI_MODEL`),
the message says so and where to change it. When Google refuses or is busy the agent waits and asks
again (it waits as long as Google asks for a per-minute limit), and a page it
still cannot get an answer for is handed to you as before.

## An account that already exists

When an employer's page says an account already exists for your email, the
agent signs in with your existing `ATS_PASSWORD`. If the site rejects it, the
agent resets the password to that same `ATS_PASSWORD`: it uses the site's
forgot-password step, reads the one-time code the site emails you from the
Gmail this browser is signed in to (a separate tab, closed straight after),
and enters it and your existing password. It never generates or chooses a
different password.

It happens only on employer ATS domains (never Google, Microsoft, Apple,
LinkedIn, Indeed, Dice or the other blocked sites, per `safety.password_allowed`),
once per site per run, and only after the page itself says the account exists.
It stops and leaves the login to you if the site sends a link instead of a code,
no code arrives, the code is refused twice, the site will not take the existing
password as the new one, or a CAPTCHA shows. The password and the code are never
written to the logs.

## When a one-time code may be read from your mail

One rule, in `emailed_codes.py`, decides every case where the agent reads a
one-time code from your mail -- the code step of a form, the code step of
sign-in or account setup, and the password reset of an existing account -- and
`passcode_from_gmail` asks it before it opens your mail, so no step can go
around it. The agent reads a code only when **all** of these hold:

* you have allowed it to read your mail (`check_gmail_for_confirmation` in your
  profile). Without it the agent does not read a code and does not even ask a
  site to send one (a reset is not requested);
* the site is an employer's, never Google, Microsoft, Apple, LinkedIn, Indeed or
  Dice (`safety.password_allowed`);
* no CAPTCHA is showing on the page: a challenge is yours to solve, and the
  hand-over says so;
* it has not already read six codes for that account in the last 24 hours -- a
  site that keeps refusing them needs you.

A code the site says is there "to confirm you're a human" (Greenhouse: "enter the
8-character code to confirm you're a human") is an emailed code like any other:
the agent reads it from your mail, types it, and carries on. Your decision of 25
September 2026: the code proves you control the mailbox, which you have let the
agent read, and stopping there left a Praxis application one step short of done.
The rule goes by whether a CAPTCHA is on the page, not by how the site words its
code step. If the code has expired the agent asks the site for a new one.

Each read is counted for that account, kept across runs, and forgotten after 24
hours. A code is never written to the logs. Before this the reset did not ask
whether you had allowed mail reads, and the code step of a form did not ask about
the site; they now all do.

## Creating an account

On an employer's create-account form the agent ticks two kinds of box and no
others: a privacy notice, and consent to creating the very account you asked it
to create ("I agree to creating this account to allow me to apply for positions
with ..."; your decision of 25 September 2026, decided in `safety.py`). Terms and
conditions, declarations, signatures, marketing opt-ins and anything that shares
your data stay unticked and are left for you. Some sites (Workday) draw the box
under an overlay so a normal click ticks nothing: the agent then forces the tick,
then uses the page's own click, and checks the box really is ticked. If it cannot
tick a box it must, it does not press Create Account: it stops and says which box.

An account counts as created only when the site shows Candidate Home or the
application. Landing on a Sign In page proves nothing (the site shows it when the
account was made, when it was not, and when it still needs verifying), so nothing
is saved as an account then, and the sign-in waits (see the next section).

## Signing in without locking your accounts

How Workday works, as researched on 25 September 2026: each employer's site
(`*.myworkdayjobs.com/<tenant>`) has its own candidate accounts, so the same email is
a separate account everywhere. Every wrong password counts towards a lock (NVIDIA's
support: 5 in a row, 30 minutes; some tenants 3). The Sign In page says the same
thing -- "You may have entered the wrong email address or password or your account
might be locked" -- for a wrong password, no account, an account not yet verified and
a locked one, so a rejection never tells the agent which. "Forgot your password" mails
a link (not a code) that lasts about two hours, with five requests allowed in 24 hours.
Whether a new account must have its email verified first is the employer's setting.

So the agent keeps count across runs (`data/_login_attempts.json`, on your machine):
* after a rejected sign-in it does not try again until you press Continue, and after
  two rejections on one account in 24 hours it stops for the day whatever you press;
* after it creates an account and the site does not sign it in, it does not try to
  sign in: it tells you to verify the account from the email it was sent, then press
  Continue;
* it never asks Workday for a password reset (it cannot follow the emailed link, and
  each request spends one of the five); elsewhere it asks at most once a day;
* it makes at most two attempts a day to create an account on one site.
Each time it holds back, the hand-over says why and what to do. Only the dashboard's
**Continue** lifts a hold: reloading the agent's code, refreshing or re-uploading a
resume say nothing about whether you looked at the account, so they leave it in place.

## Signing in with Google

Where a site offers "Sign in with Google", the agent uses it and only picks
your account on Google's chooser; it never types a Google password. The
sign-in counts as done only once the site stops offering Google -- pressing the
button is not evidence. A site whose button does nothing at all (ADP's is
sometimes dead for a whole page load) is answered by pressing it once more, then
by loading the page again, up to twice; after that the agent uses the site's own
sign-in and says so in the log ("did not react to its Google button").

## What is already in a box

A value already in a box -- one the agent typed, one you typed, or one the
site filled in -- is read as that box's value however the page draws it, so it
is not typed again. A phone box that holds only its country's dial code ("+1")
is still empty, and is filled. That dial-code picker is never "corrected" to your
country: a "+1" is the prefix your number takes, not where you live.

## A town the form spells another way

Your profile says "Fairfax, VA"; a form's own search may offer "Fairfax, Virginia,
United States" and find nothing for "VA". The agent treats them as one town -- the
same name, the same state in any spelling, a country left out or named on both
sides in agreement -- and clicks that row. Another Fairfax (a different state, or
"Fairfax Station") is never taken for it. Where a list cannot be read and the agent
has to type and press Enter as a last resort, the answer counts only if the box
still holds it after leaving the box, and a row that turns out to be a different
town is taken out again and left for you.

## Substitutes you have approved

The agent never swaps a fact for a near one on its own: a field of study on an
application is something you certify as true, so "Computer Technology" is not
"Computer Science" when a list happens to offer only the second, and the box is
left empty. If you decide a substitute is acceptable, you say so in your profile
(`answer_alternatives` in `data/profile.json`, for example
`{"Computer Technology": ["Computer Science"]}`). Then, and only when the exact value
is not offered, the agent tries your alternatives in your order and nothing else; the
hand-over lists each one it used ("'Computer Technology' is not offered, so your listed
alternative 'Computer Science' was chosen"). Your decision of 25 September 2026, for
Computer Technology only. A form that offers the exact value always gets the exact value.

## Dropdowns that show their choices only when opened

Some pages draw a required dropdown as a bare "Choose an option" button and list
its choices only once it is opened. The agent opens it, reads the whole list,
closes it without choosing, and gives those choices to Claude with the question,
so the answer comes from your profile and the choices offered -- not from a guess
at what the list might hold. If your profile settles no answer, or none of the
choices fits, the question is still left for you and listed at hand-over.

The same is done for any blank dropdown that carries its question as its own name
(Greenhouse's) when the agent could not answer it from your profile: a form asking
"Are you a former employee?" may offer "Never Employed by Praxis", not "No", and
the agent now gives Claude the form's own words before it answers. It opens at most
six dropdowns in one look at a page, leaves alone a list of more than thirty choices
(Schools, Countries -- those are typed into), and leaves Ant Design lists to their
own reader.

## Yes/No questions drawn as buttons

Some sites (Ashby's) draw a Yes/No question as two buttons under the question
instead of radio buttons. The agent reads such a row as that question's
choices and answers it from your profile like any other: 18 or over, allowed to
work, sponsorship, days in the office. It presses the answer once and then
checks that it stayed pressed -- pressing a chosen button again clears it on
these sites -- so a choice already showing is left alone, and one that did not
stay pressed is listed for you at hand-over rather than reported as answered.
A row of action buttons (Back / Next, Edit / Remove) is never taken for an answer.

A question that names a place only to say where it applies -- "Are you legally
authorized to work in the country in which the job is located?" -- is answered
as the work-authorization (or sponsorship) question it is, from your profile,
not as a question asking which country.

## When you press Continue

Pressing Continue on the dashboard gives the agent its three tries at moving on
afresh. Before, a page that had tripped the loop guard tripped it again on the
first press after your Continue, whatever you had put right in between. The guard
still stops a page that will not move on after three presses, and reports it.

## A job that will not sponsor

When your profile needs sponsorship, the agent reads each page for wording that says
the employer will not sponsor or is open only to citizens ("without the need for
sponsorship", "unable to sponsor", "no visa sponsorship", "U.S. citizens only") and,
finding it, stops with `DISQUALIFIED_POLICY_MISMATCH` before filling anything. An
inclusive question is not a refusal: "Are you authorized to work in the United States
(with or without sponsorship)?" (also "with and/or without") accepts candidates who
need sponsorship, so the agent goes on. A real refusal elsewhere on the same page still
stops it. Your decision of 25 September 2026, decided in `safety.py`.

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

A confirmation page has to say so in words only a confirmation uses ("Thank you
for applying", "we have received your application", "you've already applied") and
must ask for nothing more: a page that still shows a password box or a form to
fill in (three or more boxes) is not a confirmation, whatever it says. Politeness
is not evidence -- "Thank you for your interest in a career with ..." opens
employers' create-account pages, and once recorded an application as submitted
that had not got past account creation.

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
| `provenance.py` | who put each value on the form: the observer of a person's own input |
| `geo_reference.py`, `reference/geo.json` | countries and US states as data (ISO 3166, regenerated by `reference/build_geo.py`) |
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

The agent's window is brought in front of your other apps when a run starts and again
at every hand-over, so you can watch it (Windows keeps a background program from
taking focus, so it is restored, briefly put on top and asked for focus). It finds its
own window by the Chrome process running on its profile, never by "any Chrome window",
so your own Chrome windows are left alone, and the "Restore pages?" bubble Chrome
showed after a forced stop is turned off. If it cannot find the window it logs
"Could not find the agent's browser window" rather than failing silently.

## Operational detail

* **Retries** — flaky page actions are retried (`with_retries`, `action_retries`).
* **Progress is saved** — the form's own Save button is clicked before handing
  over, so a part-finished application survives a reload.
* **Structured logs** — each run also writes `logs/run_<timestamp>.jsonl`.
* **No secrets in logs** — every log line passes through `safety.redact()`,
  which masks API keys, passwords, email addresses and phone numbers. The page text
  the agent saves and hands to Claude (or to this session) hides what is typed into a
  password, passcode, one-time-code or PIN box: it reads `[hidden]`.
* **Dashboard** — each application's page shows progress, what is still
  outstanding, the field-by-field comparison, evidence links and the full event
  history. It is served on 127.0.0.1 only.
