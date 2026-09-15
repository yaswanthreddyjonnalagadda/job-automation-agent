# Job Application Co-Pilot — Setup Guide

## What this is, and what it deliberately is not

This tool automates the tedious parts of applying to jobs — tailoring your
resume, writing a cover letter, filling out form fields, and tracking what
you've applied to — while leaving three things to you on purpose:

1. **Logging into LinkedIn, Indeed, and Dice.** All three sites' Terms of
   Service prohibit automated/scripted account access, and LinkedIn in
   particular actively detects and bans automation (and has pursued legal
   action over it). The tool never stores or uses a site password. Instead
   it opens a real, visible Chromium window with a persistent profile — you
   log in yourself once, and the session cookie sticks around after that,
   same as a normal browser.
2. **Searching/scraping job listings.** Instead of crawling these sites
   (also against their ToS), you browse them normally and drop job URLs you
   find into `data/job_queue.txt`, or paste in the JD text when prompted.
3. **Clicking Submit.** The assistant fills in the fields it can confidently
   match, validates the form, and then stops with the browser in front of you.
   It has no code path that clicks Submit. It also never signs an attestation,
   never guesses an answer, never overwrites something you typed, never types a
   Google password, never works around a CAPTCHA, and never applies twice to
   the same posting.

Everything else — resume parsing, JD analysis, Claude-powered tailoring,
form-field detection/autofill, account creation and sign-in, document upload,
and duplicate-application tracking — is automated.

**See [BEHAVIOUR.md](BEHAVIOUR.md)** for exactly what the agent does before it
stops, how a submission is verified afterwards, and which test covers each
rule.

## 1. Install dependencies

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

## 2. Configure

```bash
cp .env.example .env
```

Edit `.env` and set `ANTHROPIC_API_KEY` (get one from
https://console.anthropic.com/). Leave everything else as-is unless you
need to.

Your job-search profile (name, target titles, locations, salary range,
etc.) lives in `config.py` under `UserProfile` — it's already filled in
with your details but edit it if anything changes.

## 3. Add your resume

Drop your resume at `data/resume.pdf` or `data/resume.docx` (update
`RESUME_PATH` in `.env` if you use a different name/location).

## 4. Queue up jobs

Browse LinkedIn/Indeed/Dice normally, and when you find a role you want to
apply to, add its URL to `data/job_queue.txt` (one per line). The file is
created automatically the first time you run `main.py` if it doesn't exist.

## 5. Run it

```bash
python main.py --dry-run   # generate tailored resume + cover letter only
python main.py             # full flow, including the browser form-fill step
python main.py --list      # see everything tracked so far
```

For each queued job you'll be prompted in the terminal to paste in the job
title, company, location, and full JD text (copy/paste from the page you're
looking at). The tool then:

- Calls Claude to analyze the JD and tailor your resume/cover letter
- Saves both under `output/<Company>_<Title>/`
- Opens the job URL in a visible browser window
- Waits for you to log in (first time only — persists after that)
- Auto-fills form fields it recognizes (name, email, phone, location, etc.)
  and uploads your resume
- Takes a screenshot and pauses so **you** review and submit

Every job is recorded in `data/applications.db` (SQLite) keyed by URL, so
re-running the tool never re-processes or re-applies to something you've
already done.

## Three human checkpoints

Nothing gets applied to without you actively confirming it, at three points:

1. **Right after you paste the JD text (before any API call).** A free,
   local scan flags citizenship/clearance/no-sponsorship language against
   your `requires_visa_sponsorship` setting. If it fires, you're asked
   whether to even bother analyzing the job.
2. **After Claude analyzes the JD (before tailoring anything).** Claude
   reports required years of experience and citizenship/clearance
   requirements; combined with checkpoint 1's scan, you confirm before any
   tokens are spent writing a tailored resume/cover letter.
3. **Right before you'd click Submit in the browser.** A concrete
   checklist (job details match, resume is accurate, cover letter reads
   right, every field is correct, extra questions are answered, correct
   resume attached) — only after that do you submit, by hand, yourself.

A job you decline at checkpoint 1 or 2 is still recorded (status
`skipped`) so it won't be re-prompted if it shows up in the queue again.

## Notes

- Logs (with API keys and passwords never included) go to `logs/agent.log`.
- The browser's login session lives in `browser_profile/` — delete it to
  force a fresh login next time.
- If a site uses an embedded ATS (Workday, Greenhouse, Lever, iCIMS, etc.)
  with a different field-naming convention, extend `_FIELD_HINTS` in
  `browser_automation.py` as you encounter new field labels — the matcher
  is intentionally simple and easy to widen.
