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

## 4. Start the dashboard

Double-click **`Start Dashboard.bat`** in this folder. A black window opens (keep
it open while the agent works) and the dashboard opens in your browser at
http://127.0.0.1:5000.

Start it yourself this way, not from an IDE's AI assistant or any background
terminal: a dashboard started in the background opens its browser where you
cannot see it.

## 5. Apply to a job

Paste the job's link (a company career page, or a LinkedIn link to one) into the
dashboard and press Apply. The agent then:

- reads the posting and skips it if it says it will not sponsor and your profile
  needs sponsorship;
- writes a resume for the job (Claude, then Gemini or OpenAI if Claude cannot),
  or attaches your own resume if none of them can;
- opens the application in a browser window you can see, creates or signs in
  to the employer's account when needed, and fills every page from your profile;
- writes answers to open questions ("Why do you want to join us?") in plain
  English, inside the form's word limit;
- stops at the Review page and lists anything left for you, and every answer
  it wrote for you to read.

**You press Submit yourself**, in the browser window, after checking the form.
Every application is tracked on the dashboard, so the same job is never applied
to twice. What the agent does and never does is set out in `BEHAVIOUR.md`.

## Notes

- Logs (with API keys and passwords never included) go to `logs/agent.log`.
- The browser's login session lives in `browser_profile/` — delete it to
  force a fresh login next time.
- If a site uses an embedded ATS (Workday, Greenhouse, Lever, iCIMS, etc.)
  with a different field-naming convention, extend `_FIELD_HINTS` in
  `browser_automation.py` as you encounter new field labels — the matcher
  is intentionally simple and easy to widen.

## For development

Changes reach `main` only through a branch and a pull request (see section 7
of `CLAUDE.md`). Once per clone:

```bash
git config core.hooksPath .githooks          # refuse commits and pushes to main
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q                          # the whole suite, as CI runs it
```

On GitHub, protect `main` (Settings -> Branches -> Add rule: require a pull
request and the "tests" status check) and add the repository secret
`PROFILE_JSON` with the contents of `data/profile.json`, which the tests read
until they move to synthetic fixtures.
