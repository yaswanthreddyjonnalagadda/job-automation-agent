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

## Your profile comes first

Anyone can run the agent on their own computer (the owner's decision of 29 September
2026). Until a profile is saved, the dashboard opens **Set up your profile** instead
of the applications list: upload a resume, check the profile drafted from it, save.
The draft takes only what the resume plainly says (name, email, phone, LinkedIn, city,
years); with an AI key it also copies the work history and education, told to copy
and never infer. Nothing is guessed, and first, middle and last names are shown split
for you to correct.

Every application then answers in this order: **your profile, your saved answers, the
AI, and only then you.** A blank box in the profile means "ask" -- the agent never
fills it with a guess. Any question it has to leave for you appears under **Your saved
answers** on the dashboard; answer it once there and it is used on every form that
asks it. Resumes and cover letters are named after you (`Jane_Doe_Resume_Acme.pdf`).
Everything stays on your computer, in `data/`.

## What it learns from you

Anything **you** type or choose on the form is your answer: it is never
overwritten, and it is remembered (`form_answers`, marked `answered_by=user`).
On the next application the same question -- however that employer words it --
is answered from what you gave last time, before Claude is asked for anything.
An answer you typed outranks one the agent drafted, and a remembered answer is
only reused when it is among the options the new form actually offers. What
the site filled in by itself (a resume parser's guess) is not learned as
yours.

A general question you answer in a few words -- "Are you willing to work night
shifts?", "What is your notice period?" -- also joins **Your saved answers**, so every
employer's form gets it and you can see and change it on the dashboard. What is kept
there: not an answer about the one employer ("Have you worked for Acme?", "for us",
"here", referrals), not an essay, not a legal, visa or work-authorization answer (those
come from your profile every time), not a signed statement, and never over an answer
you saved yourself. Yes and No answers are learned too; they used to be skipped.

"Have you worked at <company>?" -- however it is worded: as an employee, a
contractor, an intern or a consultant, "provided services to", "a former employee of",
"for us", "here" -- is answered from your own work history: **No**, unless that company
is one you worked for, which your profile and your resume say (then **Yes**). A
question that names no one in particular ("a government agency", "a competitor") is
not answered this way. An answer you saved yourself still comes first.

When the agent stops in the middle and you answer on the form, then press Continue,
each answer you gave is kept at once -- not only at the Review page -- under the same
rules: a general one joins **Your saved answers**, one about that employer is kept for
that employer, and legal, visa and signed answers are not kept. It then leaves the
dashboard's "Waiting for your answer" list.

### The answer bank: answered once, answered on every portal

Before, an answer the agent (or the AI) gave was never used again, and one of yours
only on the same site in nearly the same words -- so a question answered on a Workday
form was a new question on Greenhouse. Now every question that was answered on an
application **you sent** -- by the AI, or by the agent and read back on the page --
and every answer **you** gave, anywhere, is kept in one answer bank
(`data/_answer_bank.json`, on your computer only) and reused on every portal. It is
rebuilt when a run starts and after each time you press Continue.

It is asked **last**: your profile, your saved answers and your own earlier answers on
that site always come first, so changing your profile is never hidden behind an old
copy. It keeps only questions your profile cannot answer, and never: a declaration or
signature, a legal, visa or sponsorship question, an essay, a question that names the
employer, a box of one job or one degree ("Company", "School"), a date (a start date
goes stale), a two-word label ("Month", "Location"), or what older runs recorded that
was not an answer (a press such as "open it"). When a question has several answers,
yours wins over the agent's and the newest over older ones. To stop an answer being
reused, put its question under `forgotten` in that file.

The answer keeps its meaning, not one portal's words: a Yes or No stays Yes or No
("No, I do not have a disability" is kept as No), a choice as chosen, several ticks as
a list. It goes into whatever the next form draws -- a text box, a list, a
type-to-search list, Yes/No buttons, tick boxes -- through the same matcher as every
other answer, so "Masters Degree" still finds "Master's".

**Dates** go into every date box one way: in the format the box shows (a date picker,
a month picker, "MM/DD/YYYY", "dd.mm.yyyy", "YYYY-MM-DD", "MM/YYYY"), whatever form the
answer is in ("December 2022", "12/15/2022", "2022-12-15"). "Immediately", "2 weeks" or
"30 days notice" are counted from today. An answer that is not a date is no longer
written into a date box as today's date: the box is left for the AI or you.

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

You make two separate choices on **Settings**, under "Which AI does what". They
are separate because answering the forms takes many small requests on every
application, while writing the resume takes a few large ones once per job; one
never uses the other's models, so it cannot use up the other's allowance.

**1. Answering the forms** (`FORM_ANSWER_MODE`):

* Profile only (`profile`) -- no AI: every answer comes from your saved profile
  and saved answers;
* Google Gemini (`gemini`), Anthropic Claude (`claude`) or OpenAI (`openai`) --
  that provider reads each page and answers what your profile and saved answers
  do not. With `claude`, `AGENT_BRAIN=session` still hands the pages to the
  Claude Code session instead.

For the provider you pick you choose its models, in order, in two lists: one
for whole pages and written answers (stronger models do these better), one for
quick choices and short questions (lighter models with bigger allowances are
enough). The first in a list answers; when it runs out, the next takes over.
The dropdowns list only models that write text (no embedding, voice, image or
video models), as your key offers them -- the agent asks each provider, so a new
model appears and a retired one disappears without a code change. Each shows
whether it has been used today, is spent, or cannot be used with your key.

**2. Writing the resume and cover letter** (`RESUME_WRITER`,
`RESUME_WRITER_FALLBACK`): a first choice of provider and model, and one to use
if the first cannot write it. With no choice, the providers with a key write it
in the order Claude, Gemini, OpenAI. A choice whose key is missing is skipped
and the log says so.

Saved choices apply to the next application without restarting the dashboard:
each run now starts from `.env` as it is, not as the dashboard read it when it
started.

A job gets one tailored resume. It is saved in the database the moment it is
written, before the browser opens, so a run that fails or is stopped anywhere
afterwards does not lose it: Resume, a retry and a restart all attach that same
resume without asking any AI again ("REUSED_RESUME" in the log). Only when
tailoring failed and your generic resume went out is tailoring tried again on
the next attempt.

The run's log says who wrote the resume ("Tailored resume written ... via Gemini
(gemini-3.8-flash)"). Only when every writer you chose fails is your generic
resume attached. Whichever writes it, what your resume does not show is taken out
before it is sent. Only the providers you chose, and have a key for, are sent your
resume and the job posting. OpenAI is reached over its web API directly, so no
extra package is needed.

A page your profile answers completely is filled and moved on without asking the
AI at all, which saves credits. A job posting whose way on is a plain "Apply" is
opened without asking the AI, even when it also has a job search box or a
language picker -- nothing on a posting is yours to answer. A field a site hides
to catch programs ("This input is for robots only") is never filled and never
sent to the AI. The page still goes to the AI when there is no
plain Next/Continue to press (for example only "Add Experience"), or when a
dropdown still shows "Choose an option" or "Select". The agent reads the page's
step counter itself ("Step 1 of 2"), so a "Submit" button on a step with more
to come only saves that step. It reads only the step the page is *on*: a progress
bar that lists finished steps ("completed step 1 of 5 ... current step 5 of 5")
is read as step 5 of 5, and when the page does not make clear which step it is
on, a "Submit" is treated as the last one and the agent stops for you. On a step
about your resume ("Select your resume") a required upload is taken for the
resume even when its box never says "resume" -- Avature's "From Device" with a
"Choose another file" button, for example. (On 29
September Aristocrat's Review page was read as "step 1 of 5" and its final
Submit was pressed -- the application went without your review. That can no
longer happen this way.) It also finds a plain resume upload box by itself.

What leaves your computer: every page the agent reads, together with the facts it
answers from (your profile, the text of your resume and the job posting -- the
first 5,000 characters), goes to the provider you chose for answering; the resume
and the job posting go to the writer you chose. Nothing else changes: the agent's
own rules -- attestations, sponsorship, CAPTCHAs, submitting only when every check
passes -- decide what is done with an answer, whoever proposed it. Keys travel in
request headers, never in an address, and are never written to a log. If the
Gemini key is missing the run says so before it starts; if it does not look like
a Google key (`AIza` and 39 characters, or `AQ.` and longer) it says that too,
without printing it.

How a list of models is used, for every provider: when a model says its
allowance is spent, the next one answers, and the spent one is not asked again
until it can answer -- Google's free allowances reset at midnight Pacific time; an
account out of credit (Anthropic or OpenAI) is looked at again after an hour.
That holds for later applications too. A per-minute limit moves straight to the
next model; the agent waits only when every model is paused for the minute. A
model your key cannot use, or one with no free allowance on your key, is skipped
for a week, and the log says so. The same question asked twice in one run is sent
once. Settings shows each model's calls today and whether it is resting. On
Google's free tier each Flash model allows about 20 requests a day and each Flash
Lite about 500, so the defaults put Flash first for whole pages and Flash Lite
first for quick choices. When no model can answer, a page is handed to you as
before.

The AI that answers a form is given the job posting as well as your profile and
resume, so an answer fits what the employer is asking about. It is told the posting
describes the job, never you: a skill, tool or number of years counts only if your
resume or profile shows it. Legal and visa questions are still never sent to it. On
Google's free tier, what is sent may be used by Google to improve its products; a
project with billing turned on is not.

When the AI cannot be asked -- its daily allowance is spent, it is busy, or there is
no credit -- a page is not a reason to stop if every required question on it (marked
*) is answered: the optional ones are left blank, the resume is attached, the agent
presses Next, and the hand-over lists what it left blank. A required question still
open stops the run for you, as before.

## An account that already exists

When an employer's page says an account already exists for your email, the
agent signs in with your existing `ATS_PASSWORD`. If the site rejects it, the
agent resets the password to that same `ATS_PASSWORD`: it uses the site's
forgot-password step, reads the one-time code the site emails you from the
Gmail this browser is signed in to (a separate tab, closed straight after),
and enters it and your existing password. It never generates or chooses a
different password.

The same is done when a sign-in is refused on an account the site knows: the
account is on record, or the site took your email and then asked for that
account's password (iCIMS signs in this way). Before, only an "account already
exists" message led to the reset; a refused sign-in stopped the run and asked you
for the username and password (Mutual of Enumclaw, 29 September). A "wrong email
or password" on an account the site has not shown it knows is still yours: it may
mean there is no account there.

A refusal is remembered across runs, so a refused password is never typed again.
When a later run reaches that site's password page with the refusal on record, the
agent goes straight to the reset instead of stopping with "sign-in paused" as it
did before. If the reset cannot finish -- the site emails a link rather than a code,
no code step appears, or no code reaches your Gmail -- the dashboard says which, and
what to do: set the password on the site to the one in Settings, then press Continue.

It happens only on employer ATS domains (never Google, Microsoft, Apple,
LinkedIn, Indeed, Dice or the other blocked sites, per `safety.password_allowed`),
once per site per run whichever way it got there, and only for an account the site
has shown it knows.
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

## The account step, one state at a time

Before it does anything on an account page, the agent reads which step the page is
at: a new-account form, a sign-in form, a sign-in that asks for the email first, a
choice of ways in, a page still loading, a box for an emailed code, a message saying
to verify the email, that the email already has an account, that the password was
refused, or that the account is locked -- or already signed in. What the site says
counts before what the form looks like. Then one table decides:

- **Sign in with Google** when the site offers it and it has not been tried here yet.
- **Create the account** on a new-account form -- once per site per run; if the email
  already has an account there, it goes to sign-in instead of making another.
- **Sign in** on a sign-in form, once per site per run, with the password from
  Settings; give the email first where the site asks for it on its own.
- **Enter an emailed code** under the rules below.
- **Reset a refused password** of an account the site knows, to the same password,
  with the code emailed to you -- once per site per run (see "An account that already
  exists").
- **Stop and tell you** when only you can act: open the verification email and click
  its link, a refused password the reset did not cure (or on an account the site has
  not shown it knows), a locked account, or sign-in held back to protect the
  account. The run says exactly what to do; press Continue when it is done. It no
  longer treats a sign-in page it could not get past as a form to fill in.

Every change of state is written to the run's log (`ACCOUNT_STATE`) with a
screenshot and the page's text in the job's `account/` folder, so a sign-in that
did not work shows what the site said.

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
is saved as an account then: the agent signs in (see below).

The order is always: your email, then the password and its retype, then Create
Account. The email box is found by what it is labelled ("Email Address", however
the page attaches that label), not by how it is coded, and the agent checks the box
really holds your email before it types a password -- and again right before it
presses Create Account, since Workday redraws the form as it fills. If it finds no
email box, or the box will not keep your email, it does not press Create Account:
the passwords are never sent alone. Sign-in works the same way: email first, checked,
then the password.

After creating the account it signs in once with your email and `ATS_PASSWORD` (your
decision of 28 September 2026). If the site then emails a one-time code, the agent
reads it from your Gmail under the rules in "When a one-time code may be read from
your mail". If the site rejects the sign-in -- which on Workday can simply mean the
new account's email is not verified yet -- that counts as a rejected sign-in (next
section): it stops and tells you to verify the account, then press Continue.

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
* after it creates an account it signs in once; that sign-in counts like any other,
  so if the site rejects it the agent tells you to verify the account from the email
  it was sent, then press Continue;
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

Your profile says "Springfield, IL"; a form's own search may offer "Springfield, Illinois,
United States" and find nothing for "VA". The agent treats them as one town -- the
same name, the same state in any spelling, a country left out or named on both
sides in agreement -- and clicks that row. Another Springfield (a different state, or
"Springfield Gardens") is never taken for it. Where a list cannot be read and the agent
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

## Questions it answers in your words

Questions such as "Why are you interested in joining us?" or "Give an example from
your experience that fits our values" are written for you by the AI that answers the
form, from your resume and profile, and from what the job posting says about the
company -- nothing else. Your decision of 28 September 2026: plain, simple English
that a person who speaks English as a second language understands the first time,
with no AI wording.

- Short sentences and everyday words. No lists, dashes, semicolons or exclamation
  marks. One real example or one or two real reasons, named from your resume.
- Words that sound written by AI ("passionate", "leverage", "thrilled", "great fit"
  and others) are never used. The list is `reference/plain_english.json`: add or
  remove words there.
- The limit the form sets is kept: words ("max 150 words", "between 100 and 200
  words") or characters ("0/500", the box's own limit). The answer is cut at the end
  of a sentence, never in the middle, and never goes over. With no limit it aims for
  about 50-100 words for a "why" question and 80-150 for an example.
- A draft that breaks these rules goes back once with the problems named. Anything
  still wrong is noted in the run's log.
- Every answer written this way is listed at hand-over as "written for you -- read
  before you submit". It is in your name: read it, and change it if it is not how
  you would say it.

## Questions the job site publishes

Greenhouse publishes each application's questions -- the words, whether each is
required, and every choice a list offers. The agent reads them with the job, before
the form is opened. When a list on the form shows no choices until it is clicked,
your answer (from your profile, then your saved answers) is matched to the exact
choice Greenhouse published for that question, and the AI, when it is asked, is
given the same list. Nothing is sent to Greenhouse: the list is public.

## When the form will not go on

When pressing Next (Save and Continue) leaves the page where it was, the agent reads
what the page says is wrong, in its own words ("The field Upload a file is required",
"Please select a state"). The first time it acts on it -- a message about a file makes
it attach the file again -- and tries once more. If the page says the same thing after
that, it stops and shows you those messages, instead of pressing again. Files are put
into a drop zone's own file box when its "Select files" button opens no file window
(Workday's), for a cover letter as for a resume.

## When you press Continue

Pressing Continue on the dashboard gives the agent its three tries at moving on
afresh. Before, a page that had tripped the loop guard tripped it again on the
first press after your Continue, whatever you had put right in between. The guard
still stops a page that will not move on after three presses, and reports it.

When the run stops for a CAPTCHA, you do not need to press Continue: the agent
watches the page for the whole wait and carries on by itself once the CAPTCHA has
been seen and is then gone. Before, it looked once as the wait began; a puzzle that
redraws itself (hCaptcha's) could be missed at that moment, and the run then waited
for a Continue after you had solved it (Mutual of Enumclaw, 29 September). It never
carries on for a CAPTCHA it has not seen.

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

The agent that works the pages never presses an application's last Submit, whatever
`.env` says: the old `AUTO_SUBMIT` setting is ignored everywhere (on 29 September it
still let the page agent press Secunetics' 'Submit Application' itself). At the last
step it stops and hands the application to you; only the verified check below, run at
that hand-over, may ever send it. If a run stops on an error while the page already
says the application was received, it is recorded as submitted by you.

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
  value. Near-misses are refusals: "Springfield County" never matches "Springfield";
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

## Only you drive the dashboard

The dashboard answers only requests addressed to this computer (127.0.0.1 or
localhost), and a form is accepted only when it comes from the dashboard's own page
with the token it was given -- a new one each time the dashboard starts. A website you
happen to have open cannot start an application, change your saved answers or delete
anything through it.

## Progress

The dashboard's **Progress** page shows how far applications get: started, reached
Review, submitted, replied, interviews -- overall and for each job site -- and the
most common reasons runs stopped before Review. After you submit, set what happened
on the application's page (**Interviewing**, **Rejected** or **Offer**) and the last
steps fill in. It reads only what the tracker already keeps.

## Statuses

`prepared` → `form_filled` → `ready_to_submit` → `submitted`,
with `needs_user_review` whenever a person has to look, and `skipped` for a
job that was declined.

Once an application has gone -- `submitted`, then `interviewing`, `offer` or
`rejected` -- a run never moves it back to an unfinished status; you can still
change any status yourself on the dashboard, and your change stands. Marking the
application a run is working on as submitted (or interviewing, offer, rejected,
skipped) ends that run: it has nothing left to do. **Close browser** on the
dashboard ends the run too (it used to be read as "carry on").

When a site says the application was received ("We've received your
application", "Your application has been submitted"), the agent records it as
submitted, even when the same page offers to make an account to track it. A page
that says something is still left to finish the application is not a
confirmation.

Besides reading the page the way a screen reader does, the agent takes an
inventory of every field on it -- in every frame and inside components that hide
their fields (Shadow DOM) -- by what each field is, not by what it is called. The
resume goes in the file input that sits under the page's resume section, whatever
its button says ('Attach', 'Choose File*', 'From Device'), and never in an
'autofill from resume' upload. A list drawn as a button ('State –Select–') is
answered by the exact item ('Virginia', never 'West Virginia'). A field that is
hidden, or says to leave it blank, or is named like a honeypot, is never filled.

Each kind of list is filled the way its portal builds it, and counts as answered
only when the box then shows the answer: a plain list is set directly; a searchable
list (Greenhouse's country, a school) is opened with a real click, the answer typed,
and the matching row clicked once the rows stop changing -- again if the list
redraws; a location the site suggests (Lever, Rippling) is picked from its
suggestions, not left as typed text; a menu with its own search box (BambooHR's
state) is searched and its item clicked; "check all that apply" ticks each of your
answers. Enter is never pressed in a list: it takes whichever row happens to be
first. The row chosen must be your answer: the same words; the same place however
the site spells it ("United States +1" for United States); the same answer in the
site's own words ("Master" for "Masters of Science", "Graduated" for "Completed" --
the pairs are in `reference/answer_equivalents.json`); or your whole answer followed
only by more after a comma or bracket ("No, I do not have a disability" for No). Never
a row that merely contains it ('Virginia' is not 'West Virginia'), and when two rows
fit equally well, neither is picked and the question is yours. A date goes in the
way its box takes it: a date picker, "mm/dd/yyyy", or "MM/YYYY".

Questions drawn one straight after another as Yes/No buttons, 1–5 ratings or tick
boxes are each read as their own question, by the question written above them.
Before, a run of them was read as one question, so answering the first (Paylocity's
"background check") made the next five look answered, and the agent pressed Next
until the loop guard stopped it (29 September). Tick boxes under one "select any
that apply" question are that question's choices: every one your answer names is
ticked, and a choice that only resembles your answer never is. The same question asked
twice is kept as two. An answer is looked for only among its own question's choices,
never in another question's. "(required)" written after a question counts as
required, and when the page will not move on, the stop names each required question
still blank.

"Do you currently reside in the United States?" is answered from where your profile
says you live, and a box asking for "City, State" gets both ("Springfield,
Illinois"), not the state alone.
Before the last press, every visible required field still empty is named to you,
so a page is never called complete with something required left out. How each
portal builds its fields is in `reference/ats_fields/`.

On a form that repeats the same boxes for each job or each degree ("Company,
Position title, Start date, End date ..." again and again), every box is answered
from *that* job's or degree's record in your work history -- never from one value
for all of them. An entry that already shows a company (or degree), perhaps put
there by the site's own resume reader, takes that company's record; the others take
the remaining jobs in order. Your current job's end date is left blank, and if the
site filled one in, the agent tells you to clear it. What your records do not hold
-- the country of a school, a degree's status -- is left for the AI or you, not
copied from another box. The same holds when the AI plans the page: for a box in a
job or degree entry, the entry's record wins over the AI's proposal.

An application's page lists its documents with the one **In use** first;
files from an earlier attempt are marked **Earlier attempt** -- the employer
received only what was attached when the application was sent. A stored resume is
reused however small it is, as long as it is a whole PDF.

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
| `tracking.py` / `job_tracker.py` / `db.py` | which tracker (one place), SQLite (no server), Postgres |
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

It only ever runs where you can see it. On Windows, a program opens its windows on
the desktop it was started on, and an AI assistant in an IDE runs its terminal on a
private desktop of its own, so a dashboard started from there opened every browser
where you could not see it. The dashboard now refuses to start anywhere but your
own desktop (`WinSta0\Default`): it prints why and exits. The browser is refused
the same way, so `python apply.py` started from such a terminal stops before any
browser opens. Start the dashboard yourself: double-click `Start Dashboard.bat`,
or run `python web_ui.py` in a terminal you opened.

## Operational detail

* **Retries** — flaky page actions are retried (`with_retries`, `action_retries`).
* **Progress is saved** — the form's own Save button is clicked before handing
  over, so a part-finished application survives a reload.
* **Reload code** — a waiting run takes up edited code without closing its browser.
  Every part of the agent it uses is reloaded, each after the parts it relies on;
  before, only a few were, so new code could meet an old helper and stop the run.
  If any file has a syntax error, the run keeps the whole version it started with.
  Who answers the form pages is decided the same way as when the run started (Settings'
  form-answering choice); before, a reload could move them to the Claude Code session.
* **Structured logs** — each run also writes `logs/run_<timestamp>.jsonl`.
* **No secrets in logs** — every log line passes through `safety.redact()`,
  which masks API keys, passwords, email addresses and phone numbers. The page text
  the agent saves and hands to Claude (or to this session) hides what is typed into a
  password, passcode, one-time-code or PIN box: it reads `[hidden]`.
* **Dashboard** — each application's page shows progress, what is still
  outstanding, the field-by-field comparison, evidence links and the full event
  history. It is served on 127.0.0.1 only.
