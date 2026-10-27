# What the assistant does, and what it never does

Labelled dropdown buttons with a nested placeholder (such as "State Select...") are recognised as unanswered questions. The agent opens the button itself through its guarded click helper, selects an offered option and verifies it; a denied click stops that attempt. Phone Type is resolved only when the primary phone uniquely matches an explicitly typed mobile, home or work number in the profile. A number without that evidence does not establish its type.

ADP asynchronous dropdowns use the active widget's internal filter and offered choices. Existing offered equivalents, such as Mobile and Cell, are matched before applying a text filter. Candidate Source and Application source use the saved how-did-you-hear answer.

When ADP displays an empty current-step region and disables Next while its fields load, the agent uses its existing bounded readiness wait before planning that page. Disabled navigation alone does not establish that a step is loading.

Explicit questions retain ownership of their radio choices when explanatory text becomes a group's accessible name. Adjacent fields and independently named questions keep separate scopes. Current-employee questions use the stated current employer, including when a past employer also appears in the saved history; absent current-employer evidence remains unanswered.

Highest completed, attained or achieved education questions use the saved degree and an offered matching option, just as highest-level-of-education questions do.

Submission-guard activation continues past a frame that is confirmed detached during navigation. An activation failure on a live frame still stops that operation; the submission rules are unchanged.

Named parent questions own checkbox choices even when rows separate the visible labels. Explicit complete owner/library answers may replace that group's stale selections only after every option matches; punctuation within option labels is retained and unrelated groups stay unchanged.

A named choice answer uses the question’s offered labels even when the planner requests a check action on a different option. Literal checked/unchecked instructions still address an individual box.

Selected dropdown answers are kept separate from question labels during classification. A race option's Hispanic/Latino qualifier does not turn a race question into a Hispanic-only question. Domestic or military-partner status is not inferred from relatives' employment status; required personal facts without an approved answer remain blocked for the owner.

Structured profile degrees remain available when resume extraction is absent or partial; distinct degrees at the same school remain separate. Repeated education date groups such as “To (Actual or Expected)” identify the completion year. Where Workday explicitly instructs Enter to search for a school, Enter submits that search only; the agent must then click and verify the matching offered university.

Workday nested prompts scan all virtual leaf rows within a category before matching an answer through the shared option matcher. Offered aliases can be selected when unambiguous; a competing label later in the list prevents selection. A bounded incomplete scan does not authorize a match. The clicked offered leaf must still pass the existing committed-value check.

Gmail readers open a mail tab when needed and confirm the exact applying email from the visible Google Account button before reading a code, account-verification link or application confirmation. A Gmail URL with an account hint is insufficient: Gmail can fall back to another signed-in mailbox. When the identity is missing or different, the agent leaves an add-account tab open for the owner to sign in. Google passwords remain a human step; authorized employer verification can continue afterward.

An explicit change to the owner's application email updates both the private profile and ATS login setting for future runs. An account recorded under a different email does not establish an account for the new identity; successful account recording updates the current identity. Submitted applications and duplicate-submission checks retain their history. Cached resumes and cover letters with a different contact email are regenerated; a generic fallback with an old email is refreshed locally without changing the source PDF. Restart a waiting run to load the new identity rather than continuing its old authentication state.

On Bank of America's public career job-detail page, Apply may open the same-job Workday disclosure/navigation popup. The submission guard permits that opener only when its career host, popup marker, matching Workday tenant/job link and lack of visible application controls prove posting entry. It continues to block final submission and ambiguous controls.

### Motivation answers and self-identification dates (23 October 2026)

Employer/role motivation answers must explain a concrete reason to choose that employer or role using the posting, then connect relevant supported experience. A list of qualifications alone is incomplete. The existing answer reviewer and bounded rewrite enforce this requirement; company facts and personal preferences are not invented to make an answer sound specific.

A Date field within a voluntary self-identification section uses today's date in the format the field requests. Heading scope is retained across explanatory subheadings and ends at a peer section, containing boundary or separator. An unrelated bare Date remains ambiguous. Existing owner-entered values and consent/submission gates remain authoritative.

### Runtime audit corrections (23 October 2026)

The cockpit Stop and handoff Cancel actions stop the active run through the existing stop mechanism and return a JSON result. Cancel clears the handoff after stopping. Answer overrides include the application's host when saved to tracker memory; a tracker synchronization failure is reported in the server log without the answer text.

Gemini form-answer mode also sends factual answer reviews to Gemini. Current security-clearance possession does not establish whether the applicant ever held a clearance; historical questions require their own supported answer. Browser upload dialogs and Workday multiselect inspection use their owning implementations. The rules engine resolves confirmation status before screenshot cleanup and clears checkpoints using its own application keys.

The agent prepares a job application end to end and then **stops before the
final Submit button**. Only the verified auto-submit gateway may click it,
which is off by default and refuses on any uncertainty. Otherwise the visible
browser is handed to the user, who can submit manually.

## What it may do

Read a job posting, tailor the resume, write a cover letter, open the
employer's application page, sign in, create an account, fill the form, upload
documents, answer questions its profile or resume actually support, validate
the result, and hand the finished application over.

## What it never does

| Rule | Where it is enforced | Test |
|---|---|---|
| Ordinary automation cannot click a final Submit | `SubmissionGuardV0` intercepts recognized submit-capable clicks, forced/event/JavaScript clicks, Enter, and form submission; structurally associated submit buttons are also held unless clearly labeled Next/Continue. Only the verified gateway can authorize an automated final click. Handoff parks automation and records it before the guard is relaxed; failed persistence keeps the guard armed | `tests/test_submission_firewall.py`, `tests/test_submission_effect_state.py`, `test_the_agent_has_no_way_to_click_submit`, `test_wizard_navigation_never_presses_a_submit_button` |
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

The Applications dashboard renders its live cockpit whether there are no
applications, the agent is idle, a run is active, or an owner handoff is waiting.
System Health reports an available application database as connected in both
the page and JSON response, and reports a warning when opening the tracker fails.
Waiting workers are labeled **Waiting for you**, with their recorded reason,
instead of appearing to process the form. Continue and Reload create the waiting
worker's signal file. There is no Pause: the runtime cannot pause, so the page has
no such button and a stray pause request finds no route and sends no command that
could resume automation.

The Applications page (23 October 2026) leads with what needs you: a **Needs your
action** strip when the agent is waiting, then the current application, then suggested
jobs, then your list. When nothing runs, the current-application card is a short
invitation with nothing to press. While a run is live, **Continue** shows only while
the agent is waiting for you, **Show browser** and **Stop application** only while it
runs, and each says what happened (a short message, read aloud to a screen reader).
Stop, Delete, Mark as submitted and Start again ask first in a dialog, and Cancel
sends nothing. Text from an employer's page (activity messages, reasons, your saved
answers) is always shown as plain text. In **Answers it gave**, an answer you changed
says **You changed it**, and saving an edit keeps it for next time. The page can be
used by keyboard and screen reader (skip link, labelled controls, visible focus) and its
colours are readable in light and dark mode.
Deliberate sponsorship skips and already-submitted applications
have their own run outcomes instead of a generic failed exit.

On a verified job posting, a real application-entry HTTP link is followed with
a GET request without executing click handlers or weakening the final-submit
guard. Social destinations are refused even when labeled Apply. A button or
JavaScript/fragment link still goes through the existing guarded action path.
Job-description wording such as "automating security checks" does not establish
a CAPTCHA; explicit verification prompts, standalone visible security-check
prompts and visible challenge frames still require the owner.

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

### Addresses, jobs and degrees (30 September, UKG)

- Your country is chosen first, then your state, then your city, and only then the
  street and ZIP: a form that redraws its address block when the state is chosen
  cleared the ZIP and street typed before it.
- A job's or degree's "From month" / "To year" box gets just that part of its date, and
  a month list takes it in its own words ("Feb", "02"): the whole "February 2025" matched
  no month and the box was left on "Choose...".
- A degree already on the page is recognised by its school as well as its degree
  ("Master of Science (MS)" at your school is your master's), and "Add Education" /
  "Add Experience" is pressed only while you have more degrees or jobs than the page
  shows -- it had added an empty entry and filled it with the same master's again.
- An optional box in a job or degree entry that your records do not fill (an
  education "Description") is left empty rather than written up by the AI.
- While the agent is working, the dashboard says so; an application appears under
  "Needs you", with Continue, Resume, Skip, Close browser and Reload, only once the
  agent has stopped for you.
- A degree's From and To come from your profile's education dates when your work
  history holds only its end; you are not asked for dates your profile already has.
- A date in a job or degree entry that contradicts that entry's own record (the site's
  resume import put "Sep 2026" as a finished job's end) is put right by the agent, and
  the end of the job you still have is emptied. Only dates: a school or degree name the
  site spells another way is left as it is. A value you set yourself is never changed.
- A box your profile says has no answer (no apartment or suite: Profile page, "Apartment
  / suite / unit") stays empty, and is emptied if the AI wrote into it before.
- A skills box that already holds tags (each with its own remove "x") counts as filled:
  the agent does not type into it and does not stop for it.
- "Are you currently in OPT / STEM OPT status?" is answered from the work authorization
  your profile states (H-1B: No); with none stated, it is left for you.
- A major the list does not offer is taken as its broader field ("Electrical and
  Electronics Engineering" as "Electrical Engineering"), never the other way.
- Once an application is under way, the agent never presses the site's own menu (its
  header, navigation or footer: "Find Opportunities", "My Presence"). If the page falls
  back to the job board, it returns to the job's own page and resumes the application
  from there, instead of searching the board.

### Tick boxes

Your decision of 30 September: agreement and acknowledgement boxes are ticked ("I have
read and agree", "I acknowledge", "By checking this box", terms and conditions, privacy
notices, "I agree to sign electronically") -- a declaration only while "Sign 'I certify
this is true' declarations for me" is on, the rest while "Accept privacy notices" is on.
A box about text messages (SMS, text messages, WhatsApp) is never ticked, and one already
ticked is cleared, unless your preferred contact is text. Any other tick box is ticked only
by a yes or no, or by an answer that is its own words: the answer of the question beside
it ("Male", a phone number) never ticks it -- UKG's "I decline to say" boxes were ticked
that way. Your name, email, phone and address always come from your profile, over any
saved answer, and are never kept as saved answers from a form.

### Questions are recognised from data, and answered only from you

Which fact a question asks for is recognised from `reference/concepts.json` as well
as the built-in table, so a new wording of a known question is a line of data, not a
change to the agent. The Profile page has fields for what forms ask and only you can
say: preferred first name, gender identity, sexual orientation, transgender,
pronouns, GPA, certifications, languages, and skill levels (1-5, for "rate your skill
with ..."). A question about who you are is answered only from these, never by the AI;
while a field is empty the question comes to you once, and your answer is kept. An
empty middle or preferred name means you have none, and the box is left blank. A
question for one part of a date ("Date available to start work: Month") gets that
part. Nothing about you is written into the agent's code as a default any more
(a job start of "February 2025", a graduation of "December 2022", "LinkedIn",
"Email"): what your profile does not say is asked.

### The answer bank: answered once, answered on every portal

Before, an answer the agent (or the AI) gave was never used again, and one of yours
only on the same site in nearly the same words -- so a question answered on a Workday
form was a new question on Greenhouse. Now every question that was answered on an
application **you sent** -- by the AI, or by the agent and read back on the page --
and every answer **you** gave, anywhere, is kept in one answer bank
(`data/_answer_bank.json`, on your computer only) and reused on every portal. The
agent's answers on an application held for your review but not sent are kept too, as
a weaker source: any answer from a sent application or from you wins over them. The
bank is added to when a run starts and after each time you press Continue, and it
never shrinks: a database that holds less than the one the bank was learned from
(one that was down, or a new one) cannot drop an answer. An answer leaves it only if
you put its question under `forgotten`, or it no longer passes the refusals below.

It is asked **last**: your profile, your saved answers and your own earlier answers on
that site always come first, so changing your profile is never hidden behind an old
copy. It keeps only questions your profile cannot answer, and never: a declaration or
signature, a legal, visa or sponsorship question, an essay, a question that names the
employer, a box of one job or one degree ("Company", "School"), a date (a start date
goes stale), a two-word label ("Month", "Location"), or what older runs recorded that
was not an answer (a press such as "open it"). When a question has several answers,
yours wins over the agent's, a sent application's over a held one's, and the newest over older ones. Page text
that reads like a question ("for more details.") and an answer of "Not specified ..." are never kept. To stop an answer being
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

### Where the history is kept

With Postgres configured (`POSTGRES_PASSWORD` in `.env`), the agent uses Postgres. If it
is not running, the run **stops** and tells you to start it (`docker compose up -d`);
it no longer quietly records into a separate SQLite file. To use SQLite on purpose, set
`TRACKER=sqlite`.

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
dropdown still shows "Choose an option" or "Select". A step counter is context,
not permission to press a submit-capable control: when a button is recognized
as a final-submit control, even an intermediate "Submit" is left for you to
handle rather than being clicked based on the counter. Ordinary "Next" and
"Continue" navigation remains available. It reads only the step the page is *on*: a progress
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
one-time code (or an account-verification link) from your mail -- the code step of a form, the code step of
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

Account creation runs through a dedicated one-click gateway when its button
belongs to a form. The gateway requires an explicit registration label, one
filled email/username field and two matching passwords. Forms containing
application fields, file uploads or another sending action remain blocked.
Ordinary clicks on account submitters remain guarded; only the account helper
can authorize this narrowly scoped click after the existing credential and
consent checks. Authorization is cleared afterwards, and final application
submission still requires its own verified gateway. A blocked click is not
counted as an account-creation attempt, and a form that remains open is reported
as an unconfirmed creation attempt rather than a created account.

For two-step sign-in, the password form may carry the previously accepted
username in a hidden read-only control. The sign-in gateway accepts that
credential form when it has one filled password and a Log In action. If the
gateway blocks the click, the agent records that no password attempt occurred
instead of treating the unchanged form as a rejected sign-in.

An account section the application page itself calls optional ("Create a Career Profile
account (optional)" at the foot of Meta's form) is part of the application, not an account
step: the agent does not make that account, leaves its password boxes empty, and goes on
with the form (Meta, 30 September).

The exception belongs to that section's password fields only. A following section,
a sibling group, or a separate required sign-in keeps its own requirements.
An AI request with an ambiguous password label is kept for review unless every
matching field is optional; a field reference must also match the question.
Verification codes never inherit the optional-password exception. If required and
optional password forms share a page, the agent asks you to complete the required
sign-in rather than risk typing into the optional account. Unknown password fields
also retain the normal account checks.

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
- **Open the verification link** when the site says the new account must be verified
  (also when it says so on its sign-in form, as Workday does: "Verify your account before
  you sign in"). Your decision of 30 September 2026: the agent reads the email the site
  sent from your Gmail, under the same rule as a one-time code (you allowed mail reads, an
  employer site, no CAPTCHA), and opens its link in a tab of its own
  browser -- only a https link back to that same site, and on a shared host such as
  Workday only that employer's own. Never a password-reset link, and never a link through
  a tracking address (those are left to you). Once per site per run; then it signs in.
  If the current verification page offers Resend Account Verification, request a
  fresh email before searching Gmail, unless the page already confirms an email
  was sent. Preserve a different owner-entered email rather than replacing it.
  Plain Sign In pages do not trigger mailbox access. Expired or invalid links
  fail even alongside generic Welcome or Thank You text. A link landing on Sign
  In permits a fresh sign-in attempt; it does not itself prove verification.
  Code or link is read from the page: a box for the emailed code means the code is read
  and typed (the one-time-code rule, unchanged); no such box, and the site says the
  account must be verified, means the link.
- **Stop and tell you** when only you can act: a verification link it could not find or
  that did not work (the dashboard says which), a refused password the reset did not cure (or on an account the site has
  not shown it knows), a website-reported locked account, or an unsupported authentication step. The run says exactly what to do; press Continue when it is done. It no
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
new account's email is not verified yet -- the agent records the outcome and follows
the supported verification flow; the recorded rejection creates no persistent hold.

## Authentication attempt history

At the owner's request (12 October 2026), the agent imposes no daily sign-in,
account-creation, password-reset, or emailed-code/link-read quotas, no 24-hour
cooldown, and no persistent rejection hold requiring Continue. Historical entries
in `data/_login_attempts.json`, including old holds, are diagnostic only and cannot
block a later run. Authentication outcomes are still recorded.

The agent still reads actual portal errors and verification requirements, uses only
authorized credentials and mailbox access, preserves CAPTCHA handoff, and reconciles
uncertain account creation before another dispatch. Existing per-run action tracking
and the unchanged-page loop detector remain. A website's own lockout or rate limit is
reported from observed evidence. Workday password-reset links remain owner-operated.

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

## Choices whose words sit beside them

Some forms draw each choice as a button with its word in a separate box beside it (Meta's
"Male", "Female", "I choose not to disclose"). The agent reads that word as the button's
name and picks the button that is your answer. A choice is never picked because its text
merely contains your answer -- "Male" is inside "Female". Before, Meta's gender showed
Female for an owner whose profile says Male (30 September). If a self-identification answer (gender, race,
veteran, disability) on the page differs from your profile and you did not choose it
yourself, the agent puts it right; a choice you made stands.

## Which locations you would like

When a form asks which of its locations you would like to work or apply in ("Please
select one or more locations where you'd like to apply."), your decision of 30 September
2026 applies: a place in your state (Virginia, from your profile) first; after that,
anywhere is fine. A tick-all-that-apply list gets your state's places and, because your
profile is open to relocation, every other place too. A pick-one list or row of buttons
gets the first place in your state, or, if none is offered, the first place listed. The
rule reads your profile (state, preferred locations, open to relocation) -- change those
and it follows. A question about where you live now, or whether you would relocate, is not
this question; and a question you or the site already answered is left alone.

## An upload button that opens a menu

Some sites' upload buttons open a menu of sources instead of a file window (Jobvite:
Dropbox / File / Type or Paste Resume / Apply With LinkedIn). The agent picks the item for
a file on this computer ("File", "Upload file", "From device") and gives it your tailored
resume -- never Dropbox, Google Drive or LinkedIn -- and closes the menu again if that
fails, so it never leaves one open over the form (Altamira, 30 September).

## Your jobs and degrees on Workday

When Workday's My Experience shows an empty Work Experience or Education section, the
agent adds each of your jobs and degrees from your record the way Workday takes them
(titles, companies, dates, degree and field-of-study lists). It only adds to an empty
section -- anything Workday already filled in is left as it is. Before, only the older
flow did this, and a new Workday application reached My Experience with nothing added
(Ciena, 30 September). Where the employer's Workday draws School as a list, your
school is picked from that list by its name -- never the first row -- and a school the
list does not offer is left for you; no extra, empty entry is added. A date box the agent
cannot place (a bare "Year" under From or To) keeps what was written. When Workday refuses a jobs or
degrees section -- an extra, empty entry, or boxes its error list names -- the agent has
Workday's filler clear that section and enter your record again, once per section. A
school is picked by the row that is one of your names for it (your record, your profile,
and the substitutes you listed, such as "JNTU Hyderabad" -> "Jawaharlal Nehru
Technological University Hyderabad") -- never a row that only starts the same way.

An AI that says it cannot answer ("I am unable to answer...", "... does not have a
Twitter account") is never typed into a form; the box is left empty. A box for a web address
(LinkedIn, Facebook, Twitter, GitHub, Website, Portfolio, a "URL") takes only a web
address, whatever the AI says. And an open list's own entries (Workday's Field of Study
list, left open) are never read as questions of their own.

## Job boards are not the employer's site

A link to a job board (Adzuna, ZipRecruiter, Glassdoor, Monster and the like, as well
as LinkedIn, Indeed and Dice) is refused with a note to use the employer's own careers
page, and the agent never signs in to or makes an account on a job board -- with Google
or a password -- even if a run lands on one. Before, an Adzuna link was accepted and the
run tried to sign in to Adzuna (30 September).

A Google sign-in is recognised however the site words it: "Login with Google",
"Sign-in using Google", "Sign up with Google", "Continue with Google" -- as a button or a
link. Before, Adzuna's "Login with Google" was missed.

## Where you live is not where you were born

Your address answers only questions about where you live now. A question about
the country, state or city of your birth or origin, where a passport or licence was
issued, or a visa is never answered from your address: it is left for you (or your
profile, where it says). Before, "What is the Country of your birth?" was answered with
your country of residence (Forterra, 30 September).

A job read from its link is named by what the page says about itself -- its title,
and the employer's own site name -- not by the job site's address: a Rippling
posting was recorded as "Unknown Role at Ats", and its resume file was named for "Ats".

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

When it stops for required boxes only you can fill, you do not need to press Continue: it watches
the form, and once nothing required is blank and nothing has changed for about 15 seconds (you have
stopped typing), it carries on by itself -- as it already did after a CAPTCHA or a signature.

A run waiting for you has, beside **Continue**, two more ways on:

- **Resume** -- carries on from the page it is on with the agent's latest logic: new code
  is loaded into the waiting run and the page is read again; the browser and everything
  already filled stay as they are.
- **Reload** -- starts the application fresh: after you confirm, the waiting run and its
  browser close (nothing is submitted, and what the site itself saved stays saved) and
  the application starts again from its posting with the latest logic. The tailored
  resume already made is reused.

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

## Your phone when the agent needs you (optional)

Off unless you set it up. When a run stops for something only you can do -- a
CAPTCHA, a question the agent cannot answer, a form that will not go on -- it can
tell your phone and let you act from it, with the laptop's browser as the one that
carries on.

* **The alert** (`phone_alert.py`): set `NTFY_TOPIC` in `.env` to a long private
  name, install the ntfy app and subscribe to it. Each stop sends one short message:
  the company, the job and which kind of help is needed (CAPTCHA, a question it
  cannot answer, or something else). It goes through ntfy.sh (or `NTFY_SERVER`, if you
  host your own), so it never carries an answer you typed, a password, a code or
  an email address -- values in quotes are cut and the text goes through the
  same secret scrubbing as the logs. A missing network never stops an application.
* **What it tells you.** A stop that needs you (CAPTCHA, a question it cannot answer, or
  anything else that stops the form), at high priority and with the console link when there is one;
  and how an application ended: **Submitted**, **Ready to submit** (press Submit at the laptop),
  **Job doesn't exist**, and **Run failed** (the site's name only). Each is sent once, when the
  status changes, never for something you changed by hand, and never with the tracker's notes.
* **The console** (`phone_console.py`): set `PHONE_CONSOLE=on` and install Tailscale on
  the laptop and the phone. The alert then links to a private page on the laptop's
  Tailscale address (never any other address, and it is only open while a run is waiting
  for you; each stop has its own random secret in its link, which stops working when
  the stop ends, so the link passing through the alert server is not a standing key) that shows the
  agent's browser and lets you tap, type, scroll and press Tab/Backspace/Escape/
  arrow keys, and press "Done - carry on". The agent carries on by itself
  as soon as the CAPTCHA is gone or the required blanks are filled, as before.
* **What a phone can never do**: press a Submit button (a tap on a control that sends a
  form, or on one labelled Submit/Apply/Finish, is refused -- sending the application
  stays a decision made at the laptop) or press Enter. Anything else on the page can be
  tapped, so keep the link private: anyone who has it, on your Tailscale network,
  sees and can operate that browser while it is open.
* The picture of the browser is taken only while the page is open on your phone,
  is kept in memory (never written to disk) and goes only to your own device.

A durable manual handoff parks automation for the entire browser context. Existing
application and questionnaire tabs, and tabs opened during that handoff, remain
under manual control. Before automation resumes, the submission guard is re-armed
in every open tab; newly opened tabs are then guarded as usual. A failed handoff
record leaves containment armed. Reloading code preserves the live guard capability
and context callbacks rather than installing a second guard.

## After the user submits

The run keeps watching and records **submitted** only on evidence:

* a confirmation page ("Application Received", "Thank you for applying"), or
* the employer portal listing the job as applied (list reloaded every minute), or
* the employer's confirmation email (when mail reads are allowed).

Page confirmation is checked first on every poll and ends the run promptly,
updating the status and closing the agent's browser. When a previously seen
application form disappears without confirmation, the watcher allows a
45-second grace period before opening Gmail. It makes at most three mail checks,
at least two minutes apart, while continuing to watch the page. Reappearing
Submit controls prevent mail checks; a vanished control or elapsed time alone
is never evidence of submission. Automatic evidence waiting uses the same mail
schedule and respects the profile's mail-read preference.

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

The ordinary page agent never presses an application's last Submit, whatever
`.env` says: the old `AUTO_SUBMIT` setting is ignored everywhere. At the last
step it stops and hands the application to you; only the verified gateway below
may send it automatically. If a run stops on an error while the page already
says the application was received, it is recorded as submitted by you.

Set `AUTO_SUBMIT_VERIFIED_ONLY=true` in `.env` to allow it. Even then, the
agent submits only when `safety.evaluate_auto_submit()` returns an eligible
`AutoSubmitDecision`, accepted by `SubmissionGuardV0` only with its structured
evidence (screenshot and page HTML on disk, and a recorded read-back of the form),
which since 13 October 2026 requires **all** of:

* the job title, company and canonical URL match the tracked application
  (tracking parameters are not a difference);
* every mandatory field is filled: no required field is blank and the form
  shows no error;
* none of your own rules is broken: no sponsorship or work-authorization answer
  that contradicts your profile, no attestation, e-signature or consent
  checkbox left for you, no identity or verification step, no CAPTCHA.

That is the whole rule. It no longer asks whether each answer equals approved
data, whether the attached files are byte-for-byte the generated ones, or
whether the form shows a warning, an ambiguous dropdown choice or a custom
question with no saved answer -- the agent submits with whatever it filled.
Those comparisons are still recorded in `comparison.json` so you can see afterwards
what was sent, but they no longer stop a submission.

Before submitting it writes, into `output/<Company>_<Title>/evidence_<time>/`:
a full-page screenshot, the page HTML, `comparison.json` (the field-by-field
report and every reason), and an audit row in `application_events`. The click
itself is re-checked for a CAPTCHA or attestation that appeared in between.
Afterwards the application is recorded **submitted** only once a confirmation
page, portal entry or confirmation email is found — clicking is not evidence.
A confirmation page is recognized by a visible heading, paragraph, standalone
div/span or alert/status region whose own text essentially is a known confirmation
phrase. Inline receipt fragments surrounded by other sentence text do not count.
Recognition ends the evidence wait and lets normal browser teardown close the
application tab. It is not enough for that wording merely to
appear anywhere on the page: an unrelated FAQ or error page that happens
to mention "application received" or "previously submitted application" in
an ordinary sentence is not read as confirming this run. A real confirmation
this does not recognize is left **needs_user_review**, not guessed at --
never the other way around.

Immediately before the click, a local SQLite transaction writes the durable
`DISPATCHED` state and append-only authorization/dispatch safety events. The
state uses a separate canonical effect key derived from the application URL:
known tracking parameters, fragments, query ordering, trailing slashes, host
case, and HTTP-to-HTTPS variation do not create a fresh effect identity.
Existing application keys for an equivalent normalized URL and the prior raw
URL key are checked as legacy aliases. A host or path the browser actually
visits while working a bound application -- a login page, an ATS redirect, a
review step -- is durably remembered as that same effect identity, so a later
invocation started from that host or path finds the same history instead of a
fresh one; it cannot create a new submission authority merely because its own
URL hashes to a different key. Two identities are never merged on a guess: a
URL that already has its own independent dispatch history, or that has no
provable relationship (by normalization or a remembered redirect) to an
existing bound application, is treated as its own separate identity, never
folded into another one. The state survives new processes,
browser contexts and tabs, including when the normal application tracker uses
PostgreSQL. URLs that do not identify a specific posting fail closed. If the
dispatch transaction cannot commit, no click is attempted. A confirmed result
becomes `CONFIRMED`; an ambiguous result becomes `UNCERTAIN`. Either state,
and an unresolved `DISPATCHED` state found
after restart, blocks another automated submission until reconciliation --
including when that state is only reachable through a remembered redirect
alias rather than the literal key on hand, at any chain depth (a redirect of
a redirect, or an identity discovered to be the same one after the fact):
every member of a merged identity resolves to, and is checked against, the
exact same durable record, never a copy of it. A URL that LinkedIn, Facebook,
Google, Microsoft, or Twitter's own click-tracking parameters decorate
(`trk`, `fbclid`, `gclid`, `msclkid`, `twclid`, and the like) does not create
a fresh identity either, nor does a login/SSO host visited along the way --
both are tied to the one bound identity the same as any other redirect.
Reconciliation can read page/status/confirmation evidence through the
read-only probe, but that probe has no submit operation.

Human handoff parks execution and appends `MANUAL_HANDOFF_STARTED` before
relaxing the browser guard. If durable handoff recording fails, automation
remains parked and the guard stays armed; the person must not treat that as a
successful handoff.

Submission safety events accept only known event types and allowlisted
metadata fields and values; arbitrary payload objects are rejected.

Final-control recognition starts from the centralized submit-label
vocabulary plus deterministic form-submit semantics, and now also denies by
default a labelled, enabled, form-associated control whose own label is
neither a known submit word nor a known non-final one (add/remove/edit/
cancel/back/save-for-later/a dropdown opener/...) when it is the only such
control left in its form -- a custom "Confirm & Send"/"Finalize"-style
button with no native submit type is covered without needing its exact
wording known in advance. An icon-only or otherwise unlabeled control
remains a limitation. This is still not a universal semantic or
network-effects firewall. The browser containment guard is active on every
tab and popup in the browser context automatically, including ones opened
after the browser session starts, not only pages the agent explicitly reads.

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

The agent's window is brought in front of your other apps immediately after opening
the page, before page-opening telemetry is delivered, and again
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

* **Searchable prompts** — a Workday selection widget can appear as a text box.
  The agent opens its picker and clicks the matching option; typing search text
  alone does not count as an answer. The picker is handled in the input's own
  frame. A missing choice remains unresolved.
* **Declarations are signed last** — profile filling leaves declarations for the
  signing step. That step checks the current required fields, including answers
  that disappeared after filling, before signing on the owner's behalf.
* **Locations must match** — a town and region must identify the requested place;
  shared words never justify selecting a different town from a dropdown.
* **Early uploads** — resume uploads work before the main page loop starts, with
  upload and entry state initialized separately for every agent.
* **Custom checkboxes** — when a page exposes only the checkbox's adjacent label,
  the agent resolves the checkbox by its name and verifies its actual checked state.
* **Employer names** — domain-shaped page metadata must agree with the employer
  address or ATS tenant. Unrelated copied branding is ignored.
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

Workday hierarchical prompts: explore only marked category nodes (bounded depth and visits), select a matching offered leaf, and verify its committed value. A category is never accepted as the answer.

Generic field labels: bare Name denotes the applicant outside an education container. Unqualified Month/Day/Year spinbuttons never reuse answer-library or historical values; dates inside work/education entries still use their own record.

Workday split date controls: changing a segment uses bounded spin controls when possible, then a composite digit stream when needed, and checks every segment while preserving neighboring values. Internal gaps remain unresolved rather than being filled with invented dates.

Education subjects: select only the recorded subject or an owner-approved substitute, by clicking its actual option and verifying the committed label. Workday prompts stay tied to the same education row when the input moves, and scroll their own virtual list to inspect later options. Structured profile subjects override older resume extraction for an unambiguously matched school.
Resume uploads on a resumed form check for the already listed filename in the upload field's frame before adding a file; an empty file input beside a saved attachment is not evidence that a new upload is needed.

A Continue signal is consumed only after its file contains a nonempty answer. An empty file created by a writer still in progress leaves the agent waiting, with its normal timeout and browser checks.

Each repeated job’s current-employment checkbox takes that job’s own yes/no value, even when its surrounding heading is read as a choice group. A contradictory site-filled current flag is corrected under the existing provenance policy; owner-entered choices remain protected. From/To groups qualify split Month/Year controls so former jobs receive their own end dates. The Workday entry filler sets each row’s current flag before locating its end-date controls.

Workday skills prompts search one approved skill at a time, click a matching row only inside that widget's popup, and verify the committed tag. Existing tags remain in place. Unavailable skills are noted, the search text is cleared, and the menu is closed; an optional empty skills catalogue does not block progression.
Unnamed layout groups without their own choices do not inherit the preceding field's question and cannot be used to select unrelated checkboxes elsewhere on the page.
The Workday menu status "Options Expanded/Collapsed" is not a second form question. Empty skills menus are dismissed by clicking the section heading when Escape is ignored; unsupported optional skills are retained as intentionally blank for that page.
Numeric/date spinbuttons always use verified spinner entry, including when the planner calls the action "choose". Composite dates preserve their neighboring segments under either action.
# Dependent location pickers

The address sweep commits SuccessFactors country and state choices through their own offered menu rows and verifies the selected value. A location control that remains disabled is deferred without clearing it, force-enabling it, or recording a successful answer. This prevents an uncommitted country from crashing the run on its dependent State field.

## Field reading, modals, and runtime identity

Composite phone widgets produce one number question: the actual child textbox retains the phone heading and the dial-code button does not become its label. Questions in paragraphs retain their wording across embedded terms/privacy links, and radio captions drawn as adjacent paragraphs are read as choices. Policy sweeps leave dialogs containing entry fields to the normal filling and validation flow, and report dismissal only when the dialog hides. The existing page fingerprint breaker still bounds genuine repeated navigation without progress.

Text-message consent in a Yes/No picker or radio group follows the same contact-preference policy as a checkbox, preserving an exact approved saved answer when present. Mentioning a mobile number inside a consent question does not make it a phone-number field.

Start Dashboard uses the checkout's local Python runtime and one canonical dashboard port. The dashboard exposes its loaded directory and source identity at /runtime; startup reuses only the same unchanged checkout, and refuses an old or different server rather than silently starting with stale code. Deployment redirects the owner's old dashboard entry point and old bookmarks to the corrected canonical runtime.

Source identity includes the runtime modules and portal adapters. A dashboard whose code changed since it loaded refuses to start or resume a worker until restarted. Each application log records the source identity used to launch it; the runtime identity endpoint retains the directory/interpreter comparison without copying local paths into diagnostics.
# Active dialog recovery

Grouped native choices covered by a decorative layer use their own associated label when direct checking fails. The fallback clicks once and does not search for a matching label in another question.

The navigation loop guard recognizes changes to visible field values and committed selections on the same wizard step. Its diagnostics store a digest rather than personal answer values; passwords are excluded. An unchanged form still trips the existing loop limit. Preferred-name presence checkboxes use a boolean derived from the profile rather than the name text.

Completed degrees can match broader completed education categories when the exact degree is absent: a master's or doctorate can use Post-Graduate Degree, and a bachelor's can use College/University Graduate. A broad category never selects a specific degree, and incomplete studies do not stand in for a completed degree. Immediate availability can use Not Applicable specifically for a notice-period menu when Immediate is absent. Identity verification offering email and phone chooses the saved contact preference; this does not bypass verification-code, credential, or CAPTCHA policy.

While a visible dialog is open, the page reader focuses on that dialog and excludes background posting controls. After an owner completes CAPTCHA, the existing form's Continue remains the next action; a covered background Apply cannot take priority. Once the dialog closes, the reader returns to the page.

## Diagnostic privacy (P0-B5)

Owner-authorized local testing (20 October 2026): `LOCAL_DEBUG_CAPTURE=1` adds private copies under Git-ignored `local_debug/`. They retain personal application details, question/option labels, runtime log messages and exception details, plus readable screenshots paired with accessibility snapshots and HTML for each readable frame. Captures occur at page-loop reads, unreadable-menu diagnostics, and existing screenshot/stop boundaries. Password/code controls are masked in screenshots; known configured secrets, credential assignments, and token-bearing URLs are redacted from text, and scripts/hidden input values are omitted from HTML. This is sensitive local evidence, not a safe-to-share export. Failed frame inspection suppresses that screenshot and records a capture error. No existing safe viewer/export accepts these files. Existing sanitized logs and exports keep their default policy below; safety and submission gates do not change. The option defaults off; changing it requires restarting the dashboard/worker. Local debug files are retained until the owner deletes them and are not covered by DIAGNOSTIC_RETENTION_DAYS. Already-redacted historical evidence cannot be recovered.

Persisted diagnostics use one privacy layer. HTML is a structural projection:
field values, scripts, bootstrap state, arbitrary text, document content and
active attributes are omitted. Saved accessibility text retains roles, known
labels and boolean state. Live form perception and application answers are
unchanged. Review/validation JSON masks values; review the actual form in the
browser to inspect your answers.

Screenshots conservatively cover the entire document with an opaque temporary
mask, including frames, previews and shadow content. They preserve capture
geometry rather than readable page pixels. Values and input/change events are
untouched, and capture removes temporary nodes in finally. If setup or sanitation
fails, the artifact is omitted; raw capture is never a fallback.

Runtime logs omit dynamic personal values, filenames, URLs and exception bodies.
An invalid Gemini key still produces a fixed format hint without logging the key.
Dashboard stdout/stderr exports retain only safe operation categories. Console
exports retain only message type/error category (latest 200 entries). Structured
application events keep action, evidence, result/category and counts. Existing
submission safety event enums and human-handoff/checkpoint authority are unchanged.

Evidence/log viewers retain resolved-root containment and require B5 manifests
binding safe artifacts to their bytes. Legacy unmarked diagnostics are withheld.
HTML is served as plain text with nosniff and a restrictive CSP.

DIAGNOSTIC_RETENTION_DAYS defaults to 7, accepts 0..30, and uses 7 for invalid
values. Cleanup runs at runtime/dashboard startup and when a zero-retention
worker finishes. Zero retains current-use artifacts until that cleanup boundary.
Cleanup is bounded and best effort, follows no symlinks, and deletes only known
diagnostics under output/, runs/ and logs/. Documents, DBs, profiles, checkpoints,
source material, application records and unrelated files remain. The ten-run
page-recording cap remains in force.

Artifact limits: DOM 2 MiB; accessibility/text/JSON and runtime logs 1 MiB;
console 512 KiB (the category-only projection is much smaller); screenshots
16 MiB; manifests 64 KiB. Oversized sanitized text may be truncated with a
manifest flag; oversized JSON becomes valid truncation metadata.

### Cloud browser startup

Cloud installation verifies a headed persistent Chromium context with the sandbox enabled under Xvfb, matching the worker's browser mode. On Ubuntu hosts that restrict unprivileged user namespaces, an administrator runs `scripts/remote/configure_browser_sandbox.sh` after installing or upgrading Playwright. It installs an AppArmor user-namespace exception for the exact installed Chromium executable; it does not change the system-wide restriction. A failed sandbox startup fails the verification instead of passing with different browser settings.

Authentication classification uses the same active dialog for its accessibility snapshot and password count. Registration fields and frames behind a Sign In modal do not turn it into an account-creation step. Password frames inside the active dialog remain supported, and account checkpoints and attempt limits still apply.

A recognized single-password Sign In form takes priority over registration content behind it. That sign-in action uses the configured credentials within existing attempt limits and cannot fall back to creating another account. An explicit account verification requirement still takes priority.

With final-submission containment armed, the account helper may authorize one Sign In click only on a dedicated form containing one filled email/username and one filled password. Mixed application forms remain blocked, and the capability expires after that click. A blocked or undispatched login stops without an Enter retry or a fabricated rejected-login record; an actually dispatched unsuccessful login still counts against the existing durable limits.

Dedicated credential actions can target an accessible button wrapper over a native submit button. The helper chooses the accessible Sign In control first. The gateway validates the wrapper's enclosing native form, checks both native and ARIA button controls for mixed application actions, and revalidates before accepting a companion native submit event. Account creation uses the same guarded path for these wrappers; no forced click or unguarded form submission is used.

An authorized credential wrapper may delegate one click to a matching native button in the same validated form. A second delegated click is blocked, and mixed-form validation still applies at every dispatch boundary.

### Site-authored error and status text now survives (f147)

Dashboard stdout/stderr exports and saved accessibility text previously omitted
a site's own error or status message exactly as they omit a value you typed --
making it impossible to tell why an account-creation or sign-in attempt failed
from the evidence alone (Chipotle's Workday Create Account form, 9-12 October).

They now keep that text, after the same secret/PII scrubbing everything else
gets: an accessibility role of alert or status keeps its own message instead of
being held to the field-label allow-list, and a reviewed set of failure
categories (account creation/login/submission being held, blocked, rejected,
or needing you) keep their sanitized message instead of the bare category name.
Every value you typed, and every other category (filled fields, codes,
passwords, answers), stays exactly as opaque as before.

### A click that worked is not reported as refused (f149)

The submission guard still refuses any click that could send the application. It
no longer mistakes an ordinary click for a refused one when the clicked row
disappears afterwards -- a list that closes once you choose, or a category that
opens its sub-menu in place of its own row (Workday's nested "How Did You Hear
About Us?"). The guard now reads its refusal counter from the page frame, which
is still there after the row has gone, and a click that replaced the page's own
document counts as a click that worked, because a refused click leaves the
page exactly as it was. A real refusal is still read, still stops the click
fallbacks, and any other failure to read the counter still counts as refused.

### A date made of three boxes is written in order (f151)

A form that asks for a date in separate Month, Day and Year boxes (Workday's
"desired start date") is filled Month, then Day, then Year, whatever order the
answers arrive in, because each box types into the next and any other order
leaves an invalid date. Two dates on one page stay two dates.

### Employment eligibility from your profile (f152)

When a list asks you to describe your employment eligibility (permanent
authorization, a temporary work permit, or needing sponsorship), the agent
chooses "I require sponsorship before I am able to work" if your profile says you
need sponsorship -- the statement you confirmed on 14 October 2026. If your
profile does not say that, it chooses nothing and the question stays yours: it
never picks "permanent" or "temporary" on its own reading of your profile.

### A job that does not exist stops the run (f153)

If the employer's page says the job is gone ("The page you are looking for
doesn't exist." or "This job is no longer available"), the run stops and the
application is recorded as **Job doesn't exist**, listed with your skipped jobs,
instead of being handed to you. Because Workday shows the same page while its
own service is down, the agent reloads the page twice, five seconds apart,
and then, on a Workday site, asks the employer's own job listing whether the
posting's requisition number is still there. Only a listing that answers and
does not hold it counts as gone. A listing that cannot answer is not an answer:
the run then stops for you with "could not be checked, try again later" and does
not call the job gone. If the listing holds the job, the run carries on.

### Where you heard about the job (f150)

Your usual answer to "How did you hear about us?" is a name such as LinkedIn,
and a portal's list rarely uses that exact word: Copart's offers "LinkedIn
corporate page" under Job Sites. The agent now takes a job-site or social brand
(LinkedIn, Indeed, Glassdoor, Monster, ZipRecruiter, Dice, CareerBuilder,
Handshake, Facebook, Instagram, Twitter, Google) as the one option that begins with
it. It still never chooses between two such options, never takes an option that
only mentions the brand ("Saw on personal LinkedIn feed"), and leaves the field for
you when the list offers no option for your answer. The brands live in
`reference/answer_equivalents.json` (`referral_source`).

### Posting navigation and Review detection

A posting's search fields outside its main content and explicitly identified talent-community subscription forms do not turn it into an application when the main content offers an Apply link outside a form. Application fields inside that main content still count, and an application already filled in this run is never reclassified as a posting. The agent follows the posting's application link with GET while retaining submission containment for the destination form.

Review detection requires a visible Submit control or a visible active-step marker whose own visible text identifies Review. A search container with an active class, hidden location options, and hidden review/submit templates cannot establish that the application reached its final step.

After both Review and ordinary handoffs, Continue, Resume/reload, and refresh re-arm submission containment before automation runs again. Ending or skipping the run does not re-arm it. Authentication holds still follow the existing owner-Continue policy.

### Observed application-entry and attachment recovery (13 October 2026)

Workday standalone login preserves tenant prefixes with and without a locale. Resume upload selects a slot compatible with the file and its purpose; a video-only or cover-letter slot is not a resume destination, and an explicit unsupported-type error is not attachment success. Custom Element UI country/state menus are read in full and their committed selection is checked. An explicit mandatory assessment entry takes priority over ordinary Next navigation until completion. An existing-account login beside multiple explicitly offered resume-entry alternatives is optional; actual authentication errors and required login pages retain their existing handling.

Generic education levels in the profile do not validate extracted degree subtypes: matching original resume rows ground those subtypes, while explicit owner degrees retain priority. An explicit standalone closed-job notice stops form filling before job-search controls are treated as application fields; the official employer posting may still be available and must be checked separately.

A generic “any certifications?” Yes/No question resolves to Yes from a nonempty explicit profile certification list; an empty default does not prove No, and a generic credential list does not prove a named qualification. Phone fields with an explicit ten-digit X placeholder use the same number as digits, removing a leading US country code only for a United States profile.

An explicit No current security clearance answers direct current clearance-possession questions, including possession of a named required clearance, as No. It does not answer historical clearance, willingness, ability, or objections.

### Page recordings stay readable (f148)

The pages the agent reads during a run, saved under
`output/<Company>_<Title>/pages/<run>/`, are kept as the agent read them, not
through the structural sanitizer above. Passwords, codes and other secrets are
already hidden at the moment a page is read, so a recording never holds one;
what it does hold -- the questions, the options and the answers on the form --
stays on your machine so a run that stopped can be understood afterwards.
Screenshots, DOM dumps, the account-page snapshots and the run log keep their
privacy treatment.

### Cloud browser startup

Cloud installation verifies a headed persistent Chromium context with the sandbox enabled under Xvfb, matching the worker's browser mode. On Ubuntu hosts that restrict unprivileged user namespaces, an administrator runs `scripts/remote/configure_browser_sandbox.sh` after installing or upgrading Playwright. It installs an AppArmor user-namespace exception for the exact installed Chromium executable; it does not change the system-wide restriction. A failed sandbox startup fails the verification instead of passing with different browser settings.

## Workday skills search tab safety (f167)

Filling Workday's "Type to Add Skills" widget searches each skill separately
against the site's own list. Whatever opens one -- a misclick, or the page's
own script reacting to the search -- a browser tab this search did not ask for
is closed immediately rather than left open, and a skill the widget's list does
not recognize is followed by a short pause before the next search. Nothing
about which skills are offered, chosen, or left blank changes.

## Opening a posting's Apply link (f170)

On a job posting, an Apply that is a link is opened by going to its address, whatever the plan
calls the step. Nothing is clicked and nothing is submitted; the submission guard still refuses
any click on a control that could send the application.


## Account forms with a disclosure button (f171)

A "Read More" / "Learn More" button inside the Create Account form (it only expands the consent text) no longer
stops the account-creation click. Any other button the agent does not recognise in that form still does, and
nothing that could send the application is ever authorised.

## Sign in or create an account (f173)

The agent uses Google sign-in wherever the site offers it, picking your already-signed-in Google account; it never
types a Google password. Where there is no Google option, it checks whether you have applied to the same employer
before (an account on record, or an application already sent): if so it signs in with the configured ATS password and
does not create an account. Only an employer it has never dealt with gets a new account. If a sign-in is refused on
an account the agent only inferred from an earlier application, it then creates one; if it was refused on an account
it has on record, it follows the existing reset rules.

## A refused Create Account press leaves nothing pending (f175)

If the browser guard refuses the Create Account press, nothing was pressed and no account exists, so the agent
forgets that it was about to press and can try again. Only a press that may really have happened stops the run
with "check whether the account now exists".

## When Create Account cannot be completed, the stop says why (f176)

If the agent cannot finish the Create Account form -- no ATS password set, a CAPTCHA, a box only you may tick, a
box that would not keep what was typed, no Create Account button, or the site keeping the form open -- the run stops
and names that reason ("Create Account was not completed on <site>: ..."). It never goes on to press the form's
button as if it were an ordinary page.

## A consent box labelled only "Yes, I consent" (f179)

Its meaning is the text above it, and that text is what the consent rule judges. If it agrees only to creating the
account, the agent ticks it. Your standing decision of 2 October 2026: if the same required consent also agrees to
calls and text messages *about your application* (Marathon Petroleum's: "recurring calls and text messages, including
by automated means, regarding my application or employment opportunities"), the agent ticks it too. If it agrees to
anything else -- marketing, promotions, newsletters, sharing or selling your data, terms and conditions, a declaration --
the agent does not tick it and does not press Create Account: the run stops and shows you the words the box agrees to.
This is only for the consent a site requires to create the account; a separate text-message box on an application still
follows your preferred contact method.

## "Already applied" (f180)

When the site, signed in as you, says in its own words that you already applied for the job ("You've already applied
for this job."), and this run did not send anything, the run ends with the status "Already applied". The site's words
are kept in the note, the browser closes, and you are told on your phone. It is never counted as an application the
agent submitted. A confirmation page that does not say you applied before still comes to you.

## Signing in to Gmail in the agent's browser (f181)

The agent reads an account-verification email from the Gmail its own browser is signed in to. That sign-in is yours:
double-click "Sign In Agent Browser.bat", sign in to the Gmail your job accounts use, then close the window; it stays in
the agent's browser. The agent's browser (real Chrome, `BROWSER_CHANNEL=chrome`) no longer announces itself as
automated, because Google refuses to sign anyone in on one that does. The agent never types a Google password, and it
reads only the verification link from your mail, as before.

## A school list that holds nothing (f183)

Some employers' Workday draws School as a search-and-pick list that is empty for their tenant ("No Items." for every
search, even an empty one). Nobody can choose a school there, and an Education entry would make School required. Since
Education is optional, the agent then leaves the section empty rather than adding an entry it cannot finish, and logs
why. A list that offers schools, even if it loads slowly, is filled as before; a school missing from a list that does
offer others still comes to you.

## "Yes, without restrictions" or "Yes, with company sponsorship" (f184)

When a list offers both rows for a question like "Are you legally eligible to work in this country?", your answer "Yes"
means the row your profile supports: if your profile says you need visa sponsorship, the agent chooses **"Yes, with
company sponsorship"**, never "Yes, without restrictions" (which would be false). If your profile says you do not need
sponsorship it chooses the unrestricted row. If your profile does not say, or the list offers only the row that would be
untrue for you, the question comes to you. Lists without sponsorship wording are answered as before.

## An optional question the form cannot take (f185)

If a question the form does not require (marked "Required" or with a star) is still blank because its list has no row
for your answer -- Invesco's Veteran Status has no "I am not a veteran" -- the agent leaves it blank, notes that it did,
and carries on, instead of stopping the run. It never guesses another row for you. A question the form requires, or an
optional one that holds some other value, still comes to you.

## The review step is the application (f186)

When the agent stops at a review step -- your answers shown as text, with the application's Submit button and no
fields to fill -- it reports the application as ready for you to check and submit. It used to say "the application form
was never reached" there, because the page had no fields. A page with neither fields nor the Submit button (a sign-in
page, say) still reads as not reached.

## Numerical expected earnings and sponsorship-qualified radio/select choices (f187)

Questions asking what you expect to earn per year or your desired annual/base compensation ("What would you expect to
earn per year in the position for which you have applied? Please provide a numerical value.", "What is your desired
annual compensation?") are recognized as `DESIRED_SALARY` from your profile instead of falling through to an open-ended
AI paragraph. When the question asks for a numerical value ("numerical value", "numbers only", "single number",
"whole number"), the agent fills your plain numeric `salary_min` (`125000`) rather than a `$min - $max` range string.
In addition, sponsorship-qualified "Yes" options ("Yes, without restrictions" vs "Yes, with company sponsorship") now
follow `profile.requires_visa_sponsorship` across radio button groups, cached native `<select>` elements, and group
choices just as they do on custom dropdowns.

## Greenhouse 8-character alphanumeric codes, country location questions, and WOTC coverage (f188)

When Greenhouse emails an 8-character alphanumeric security code ("EvPGU6Qy") and the form either shows 8 character
boxes or states "enter the 8-character code", the code-entry step keeps letters as well as digits (only digit-labeled
boxes such as "digit 1 of six" strip non-digits). Embedded Greenhouse boards using `?gh_jid=` also match the
Greenhouse adapter and inspect embedded Greenhouse frames for attachment state. Questions asking "Which country are you
located in?" now resolve to `COUNTRY` (`profile.country`) rather than `RESIDES_IN`; "Where would you like to receive a
verification code?" selects your preferred contact channel (`Email` by default) on both dropdowns and radio buttons;
and WOTC government-assistance questions (TANF, AFDC, SNAP) map to `profile.received_government_assistance`.

## SmartRecruiters guest apply, option range matching, file upload decoupling, and iCIMS scoping (f189)

On SmartRecruiters and other guest apply forms, a field asking to "Confirm your email" alongside applicant personal fields
(First Name, Last Name, Phone, Resume) is recognized as an application form input rather than being misclassified as an
account verification gate (`VERIFY_EMAIL`). Verification-sent notifications on account creation pages (e.g. Workday) are
recognized and prompt verification link retrieval before attempting sign-in. Option selection now supports numeric
intervals for total annual compensation and expected earnings buckets (e.g. choosing "$111,000 - $120,000/yr" for an
expected salary of 120000 or "$120,000") and years of experience. File upload buttons ("Choose file", "Upload file",
"Browse") are decoupled from generic dropdown placeholders so Playwright never clicks them as select inputs to open native
OS file dialogs. iCIMS portals are handled by a dedicated adapter (`sites/icims.py`) that scopes form filling to
`#icims_content_iframe` to prevent outer corporate header search bars from hijacking application navigation, and ATS vendor
job postings extract the tenant employer from the URL path when microdata is absent or records the software vendor name.




## Datadog audit fixes (f190-f194)

- **No writer key no longer stops the run (f190).** With no AI writer set in Settings, the run used to stop before the
  browser opened. It now goes on and attaches your own resume as it is, as it always promised.
- **"No, I need sponsorship now" is your true answer, not a conflict (f191).** When a work-authorization list's rows say
  what you need ("No, I need sponsorship now", "Yes, with company sponsorship", "Yes, without restrictions"), the agent
  checks the chosen row against whether your profile says you need sponsorship -- not against its first word. A bare
  "No" to "Are you authorized to work?" is still flagged.
- **A required "Preferred name" takes your first name (f192).** If your profile has no preferred name and the form
  requires one (a star, or marked required), the agent types your first name and leaves it there. An optional
  preferred-name box is still left blank. A label written "Name*" (no space before the star) now counts as required.
- **The review stop waits for you (f193).** A run's "waiting" note no longer overwrites its own Continue/Stop signal.
- **Your most specific saved answer wins (f194).** When several of your saved answers match a question, the one saved
  for exactly that question -- or the one that covers most of it -- answers it, not whichever comes first in the file.

## A new account that is refused at sign-in (f195)

When the agent has just created your account on a site and the site then refuses the sign-in -- Workday says only
"You may have entered the wrong email address or password or your account might be locked." while a new account's
email is not verified yet -- the agent no longer hands that page to you straight away. It first opens the verification
link the site emailed you (from your Gmail, as before). If no link arrives, it resets the password to the ATS password
in Settings with the code the site emails. Only if that does not work either is the sign-in left for you.

## A CAPTCHA stop ends when the page moves on (f196)

When the agent stops for a CAPTCHA (such as hCaptcha on iCIMS profile creation) and you solve it, the wait loop
watches the browser page progression. As soon as the site submits and navigates to the next page or application step
(the URL path changes or password inputs disappear), the agent immediately carries on with the application without
waiting indefinitely, even if the site keeps third-party CAPTCHA scripts or invisible widget frames embedded in its layout.

## Owner Continue clears uncertain account creation marker (f197)

When an account creation attempt was interrupted (leaving an advisory `account_creation_dispatched` marker in the
checkpoint) and the agent stopped to ask you to verify whether the account exists on the site, clicking Continue
on the dashboard or signal file now clears the pending marker so the agent can execute the owner-approved retry,
rather than repeatedly stopping with the same advisory note. Additionally, iCIMS Candidate Profile builder forms
inside `#icims_content_iframe` are classified as registration and their "Submit Profile" button is properly targeted.

## Candidate profile gateway and step finality on multi-step ATS builders (f198)

ATS portals that build a candidate profile before the application form (such as iCIMS `/candidate?from=profilebuilder`)
combine candidate contact details, file uploads, login credentials, and a button labeled "Submit Profile".
The agent now recognizes candidate profile creation actions through a dedicated, authorized gateway that validates
matching passwords and email while strictly enforcing that no final application submission button exists on the form.
Furthermore, intermediate wizard step progression (e.g. iCIMS "Step 1 of 3", "Step 2 of 3") is recognized as non-final,
ensuring multi-step registration and application flows proceed smoothly to the final review step without premature stops.

## Input submit elements supported in credential and candidate profile gateways (f199)

Portals like iCIMS that render profile creation and sign-in actions with `<input type="submit">` or
`<input type="button">` (instead of standard HTML5 `<button>` elements) are now fully recognized and
authorized by `SubmissionGuardV0`'s credential and candidate profile creation gateways, eliminating
false-positive click blocks when creating ATS accounts or candidate profiles.

## Dynamic CAPTCHA detection after account or profile submission (f200)

When submitting an account creation or candidate profile form triggers an interactive CAPTCHA modal
(such as hCaptcha on iCIMS profile builder), the filler detects the newly presented CAPTCHA immediately
rather than mistaking the held-open form for a generic validation failure. The run enters the dedicated
CAPTCHA handoff loop, prompting the user and resuming as soon as the challenge is completed.

## Owner Continue clears pending account creation across key aliases (f201)

When resuming an application after owner handoff or restart, `apply_flow.py` and `PageAgent.forget_sign_in_attempts`
clear any pending `account_creation_dispatched` checkpoint markers across all key aliases (the human-readable slug
key and the 64-character SHA-256 submission key). This prevents runs from getting trapped in an infinite
`ACCOUNT_CREATION_UNCERTAIN` loop when resuming after an interrupted account creation or CAPTCHA step.


On embedded ATS account forms, CAPTCHA handoff observes the application frame even when the outer page stays unchanged. It follows a replaced frame and keeps waiting if the frame temporarily disappears. The wait also retains an explicit CAPTCHA reason from an account-creation stop. No CAPTCHA is solved by the agent.

An explicit owner Continue that successfully clears a pending account-creation checkpoint also clears the matching in-memory attempt hold. Automatic refresh, failed checkpoint writes, and confirmed creation history do not receive this reset.

Stopped-page and account-failure captures include a `.state.json` report of field validity flags, filled/required/disabled booleans, submit counts, visible-alert counts and known submission-guard denial types. This report excludes field values, labels, URLs, and CAPTCHA-frame contents, and follows the existing artifact retention rules.

Resume tailoring rejects drafts with any remaining claims detected by the existing unsupported-claim checker after correction, including when the correction request fails. It tries the next configured writer and ultimately uses the original resume if all writers fail. Stored tailored PDFs are checked against the current source resume before reuse; unreadable, empty, or flagged PDFs are regenerated. This lexical checker is not a complete semantic fact checker.

Gemini document requests share the configured request-timeout budget across their model attempts, log attempt start/end timing, and move to provider fallback without sleeping on per-minute limits. Responses arriving after the budget are rejected. This is a cooperative budget, not forced cancellation of an active HTTP request; socket inactivity timeouts can overrun elapsed time. Page-answer retry behavior is unchanged.

## Human-paced typing on application inputs (f206)

Filling form fields in 0ms using synthetic instant value assignment triggers anti-bot telemetry and challenge blocks (hCaptcha, Turnstile, DataDome) and can fail custom input controls with keydown listeners. The agent now types normal inputs (<200 characters) character-by-character via natural keypress sequences with variable human delays (30-65ms) and a natural settling pause between fields (150-350ms). Empty inputs do not issue unnecessary clear keystrokes, and long answers/essays (>200 chars) are inserted directly like a pasted response. Fallback logic automatically catches swallowed keys and applies instant fill so fields are never left blank or corrupted. Readback verification requires exact comparison by default (including letter case, punctuation, and decimals) so dropped characters in passwords or numbers trigger immediate fallback fill; only recognized masked phone number inputs allow formatting differences. Human-paced typing is enabled by default in runtime (`HUMAN_TYPING=1`) and disabled in the test suite (`HUMAN_TYPING=0` in `conftest.py`) for speed.


### Open application answers: evidence and content review (20 October 2026, f209)

Technical experience and other open answers use the original resume already parsed for the run, not a
browser extraction helper or a generated attachment. Ordinary resumes are kept up to 12,000 characters;
longer resumes use question-relevant lines within that budget. Profile and approved answers retain priority.

A separate content review checks the final fitted answer for supported personal claims and coverage of the
question, including requested scale/examples. Missing resume evidence does not establish lack of experience.
The shared writer permits at most two drafts and two reviews, reuses identical reviews, and returns no answer
if content approval is missing, malformed, unavailable, or still rejects the draft. Exact known answers do
not need review. Full-page planner fills and corrections must pass the same content review, regardless of
the source label proposed by the model. The selected answering provider performs reviews; document writers
do not. Default word targets no longer force short complete answers to be padded. Explicit form limits remain
mandatory; tone preferences remain advisory after the bounded rewrite.

Unsupported optional fields remain blank under the existing optional-field rules; required gaps use the
existing review/handoff path. Owner-entered values and submission gates are unchanged. Existing filled
answers are not silently rewritten by restarting the code. Semantic review adds a check, not a guarantee;
the owner still reviews generated application answers before submission.


### Personal web addresses require evidence (21 October 2026, f210)

For recognized personal URL fields, the shared browser fill boundary accepts a URL only when it matches
an existing profile/approved answer or a URL explicitly present in the original resume. URL syntax alone
is insufficient; generic platform homepages and invented handles are not substitutes for missing user
facts. Email-address fragments do not establish a personal website. Required gaps remain for review;
unsupported optional fields remain blank under existing rules.
# Related experience in application narratives

When the question names a technology not explicitly documented, the answer writer first connects concrete supported work to the requested skills. It leads with relevant experience instead of resume-omission disclaimers. The reviewer accepts accurate transferable experience without requiring an exact keyword match. This does not authorize invented projects, tool usage, metrics, or negative experience claims. Explicit factual requirements such as exact years using a tool still need evidence.

## ADP job-listing navigation

The submission guard permits ADP's verified job-detail Apply opener when the live URL and component structure identify the posting and no visible form, dialog, or input is present. Apply buttons on application pages, form-associated controls, and final-submit labels remain guarded. This opens the application workflow; it does not authorize submission.

Modal confirmation matches whole accessible button names. Agree cannot match Disagree, and Accept cannot match a negative action containing that word. Existing consent and attestation requirements still apply.

## Radio accessible name recovery from live validation errors (f214)

When an employer portal (such as ADP) displays validation errors on an unselected required question, it associates error messages (e.g. "Error Add response.") with the radio buttons via aria-describedby or aria-errormessage, which pollutes their computed accessible names in the accessibility tree and prevents option matching from recognizing the choices. The accessibility snapshot parser now recognizes validation error banners and placeholder phrases on radio, checkbox, and switch controls and recovers the genuine choice label from child or sibling text nodes ("Yes" / "No").

## ADP post-submission Applied button confirmation (f215)

On ADP career sites (such as `myjobs.adp.com`), completing an application submission redirects the user back to the job description page (`/cx/job-details?reqId=...`) where the action button dynamically changes from "Apply" to a disabled or toggled "Applied" button, without displaying an explicit "Thank you for applying" confirmation page. The dedicated `AdpAdapter` now detects this terminal status on ADP job details pages. If the application form was active (`_seen_form` or `final_pressed`), the runner recognizes submission confirmation, updates the tracker to `submitted`, saves `submitted_confirmation.png`, and cleanly terminates the browser session instead of stalling into `needs_user_review`. If the posting is opened for the first time without prior form interaction in the run, it is recognized as `already_applied`.

## Autonomous ATS Discovery Fleet, 3-Column Command Cockpit & Remote Notifications (P3-1, P3-4, P3-5)

The local web UI and runtime agent feature a 3-column unified command cockpit (`web_ui.py`):
1. **Discovered Feed & Fast Apply (Left)**: Continuous background crawler (`job_discovery.py`) discovers newly posted roles directly from Greenhouse and Lever JSON APIs without browser overhead. Pre-filters against candidate target titles, locations, and visa criteria, assigning match scores (0-100%). Users can 1-Click Apply, dismiss, or paste direct URLs.
2. **Active Application Cockpit (Center)**: Real-time visual stepper, live action display, last verified operation, inline handoff review cards, and direct pause/resume/reload controls during active runs. When idle, shows clear standby status and safety guarantees.
3. **Application Tracker & History (Right)**: Searchable and filterable history with live status pills, relative timestamps, and one-click access to application details and receipts.
4. **Remote 1-Tap Notifications (`notifications.py`)**: Webhook dispatcher supporting Telegram, Discord, Slack, and generic HTTP endpoints. Automatically alerts the owner when high-fit opportunities (>=85% match) are discovered, with 1-tap `/apply <id>` or `/dismiss <id>` command processing via `/api/webhook/command` and `/api/webhook/telegram`. All final submit gates (`SubmissionGuardV0`) remain active without exception.


## Worker profile ownership and ADP receipts (22 October 2026)

The runtime leases its persistent browser profile before cleaning up old browser
processes. If another updated worker holds that profile, startup stops with a
profile-in-use message and leaves that browser untouched. The lease is released
on teardown, failed startup or process exit. This does not enable simultaneous
applications in the dashboard or change the visible-browser default. Finish older
workers before deploying this change; they do not participate in the lease.

ADP's Applied-button receipt requires current live evidence: the exact Applied
label within the posting action row on the requisition's ADP job-details route,
with no visible application form controls. Open shadow roots are inspected.
Cached snapshots, body prose, unrelated buttons and label prefixes cannot mark an
application submitted. Existing generic confirmation banners continue to work.

Direct API submission remains unimplemented: official Greenhouse and Lever write
APIs require employer integration credentials, and the existing verified browser
submission gateway is retained. No personal application payload is POSTed through
an alternative transport. See `docs/MULTI_AGENT_PLAN.md` section 5.2 for prerequisites.


## Authentication page changes after a failed action (22 October 2026, f219)

A failed registration or sign-in can still navigate to a different account step.
The reader now checks the resulting URL and account state before using its old
form controls. A changed page is read again; a page that cannot be read is handed
off explicitly. Authentication is not marked successful merely because it moved,
and credential retry, verification and password-reset policies are unchanged.

## Gemini-guided sign-in fallback (22 October 2026)

When the normal employer sign-in helper cannot locate its controls, a configured
Gemini key enables one guidance attempt per employer host per run through the
configured quick-model ladder. Gemini sees
redacted control labels and types, and selects local control indices. The runtime
fills the configured email and ATS password locally; credentials, input values,
cookies, full page text and screenshots are not sent in this request.

This fallback runs only on a recognized sign-in page before a failed dispatch.
It preserves different existing credential values, rejects changed pages and
invalid plans, and requires the existing dedicated credential-form submission
guard. It does not retry a rejected password, create accounts, reset passwords,
operate identity-provider logins, solve CAPTCHA or authorize final submission.
Unknown button labels that the guard does not support still require handoff.
A verification prompt or a disappearing form alone is not recorded as a successful
login. Model failures leave the existing recovery/handoff path in control.

### Sign-in latency cleanup (22 October 2026, f221)

Hidden password inputs no longer cause a four-second wait followed by unnecessary
Gemini guidance when a visible password input is available. Login outcome checks
also ignore hidden duplicates. Redundant case-only button lookups were removed.
Gemini sign-in guidance makes one pass through the configured quick-model ladder
without sleeping for quota resets or retrying the entire request. Unavailable
models return to the existing handoff/recovery flow. Other page-answer and document
model policies retain their existing behavior; credential and submission checks
remain in place.

### Reading an answer for a list, and a sign-up form's password rule (f225, f226)

- When the AI answers a question that has a list of options, the agent now accepts the option that means the answer in the bounded ways only: the same words, the profile's own answer on sponsorship for a bare "Yes" (and nothing at all if the profile cannot back any row), a listed equivalent such as "Bachelor's" for "Bachelor's Degree", and, for a start-date question, the same wait in days or weeks ("2 weeks" for "Within 2 weeks"). If two options state the same wait, or none matches, the question is left for you.
- On a plain drop-down the profile's sponsorship answer is applied before any other match, as it already was on other lists.
- On an account-creation form, a message such as "Password is invalid: must include a symbol" is treated as the site's rule for the new password, never as a refusal of your password, so it does not start a password reset.

## Your standing decisions on consent, and where you are available to work (f229)

Four questions that forms ask in many wordings are answered from your profile, once, on every form: **AI interview notetakers** (your decision of 23 October 2026: consent, "Yes, I consent"); **sharing your resume or contact details with a company's partners or third parties** (No); **marketing emails, newsletters, job alerts and joining a talent community** (No); and **where you are available to work** (the places you gave). Each is a setting in your profile (`ai_notetaker_consent`, `share_data_with_partners`, `marketing_opt_in`, `available_work_locations`). When a setting is empty the question is left for you, never guessed, and the account-creation rules above are unchanged: a box that also agrees to marketing or sharing your data is still not ticked for you. New wordings of these questions are added as lines in `reference/concepts.json`, not as code.

## Answering from your profile first, not from a model (f230)

Before any AI model is asked, the agent answers fact questions itself from your profile, resume and work history, in any wording: languages you speak, whether you may work in a named country, your current employer's name, how you heard of the job, years in a named skill, degree level, ties to the company, work arrangement, contact preference, clearance, and privacy-notice acknowledgements. Experience is claimed only where your resume shows it; where it does not, the answer follows your `unproven_experience_answer` setting (default "No"; "ask" leaves it for you). A free-text box is never answered Yes or No. Who you are (gender, race, veteran status, disability) and declarations or attestations are never answered by this step. On a page that marks which fields are required, optional questions the agent cannot answer are left blank rather than stopping the run or calling a model (`skip_optional_questions`, on by default).

## Lever-style forms (f231)

On forms that put the required star on its own line, the agent now knows which questions are required, so optional boxes it cannot answer from your profile are left blank instead of being written up by a model. A list whose first row is an instruction ("Click Here ...") counts as unanswered and is chosen from your profile. A town typed into a search-as-you-type box (Current location) is confirmed by clicking the suggestion that names the same town, never the first row. The Date under your name on a voluntary self-identification form is the day it is filled in.


## ADP uploads, repeated cards and saved choices (f252–f256)

ADP document uploads open their own dialog, attach through its shadow file input,
and save before the existing attachment receipt check can report success. A denied
guarded click stops the action. Existing numbered employer and education cards are
expanded and scoped by both their headers and sibling regions; each uses its own
saved history, including Current/Previous and the current job's blank end date.
Approved reasons and employer-contact permissions are merged only into the matching
employer record. Missing permission remains unanswered. No permission is inferred.

A saved dropdown choice nested under a button is separated from its question when
the explicit preceding field label matches. Unrelated button prose is unchanged.
Paused code reload refreshes all registered portal adapters before rebuilding the
registry, rather than retaining an older ADP implementation.

Employment date correction and post-fill verification compare dates in the format
the site commits (f257). A saved month/year accepts the same month without
replacing an already supplied day; a saved full date still requires the same day.

Education cards use school-matched `education_details` for explicit city, state,
country and graduation answers (f258). Unknown facts remain unanswered. Inline
education cards are added only up to the saved degree count, with the same
post-action count check used for work entries.

Personal-profile corrections exclude mapped work and education controls (f259).
An employer's or school's city cannot be replaced by the home city. Named
signature containers provide context for their typed-name fields; authorized
signature plans select affirmative radio choices and never check Decline (f260).
The standing signature permission and sign-last checks still apply. A bare From
Date outside a mapped record no longer borrows the current job's dates (f261).

On ADP, the wizard's Next control on its current Review step is treated as a
final submission action (f262). The guarded agent and supervised actions stop
there for owner review unless final submission is separately authorized.

Known answers to individual text questions stay scoped to that question. Answering Address does not supply Address 2, and answering Employer does not supply Previous Employer.

A guarded employer sign-in can accept one same-button click that the site triggers synchronously while handling the authorized Log In action. A prevented first sign-in form submit can be followed once by the site on that same form; an accepted submit consumes authorization, and additional clicks remain blocked.

On iCIMS, the application-frame step marker controls whether a Submit-labelled button advances an intermediate step. Update Profile uses the guarded candidate-profile action. A signed voluntary portal packet marked 1/2 may advance using approved profile facts and the owner's signature preference. The portal's explicit submitted-job status confirms the application even when an optional follow-up form remains open (f266).
