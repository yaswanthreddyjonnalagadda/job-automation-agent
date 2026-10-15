# Phase 0-A Discovery Report

## 1. Analysis Identity

- **Repository**: `job-automation-agent`
- **Git Commit SHA**: `6fb53e6c7a9bce394e67107c48fa5e47d1041e9b`
- **Branch**: Detached `HEAD` at `6fb53e6` (matches tip of branch `codex/architecture-reliability` at `6fb53e6c7a9bce394e67107c48fa5e47d1041e9b`)
- **Working Tree State**:
  - Modified (unstaged): `.gitignore` (local exclusion additions for `data/checkpoints/`, `data/run_metrics.jsonl`, `sandbox/`)
  - Untracked: `.claude/`
  - No production code modified or staged.
- **Date / Time of Analysis**: 2026-10-06 09:33:00 EDT (13:33:00 UTC)
- **Reconciliation Date**: 2026-10-06 11:38:00 EDT (15:38:00 UTC)

---

## 2. Baseline

### 2.1 Historical Discovery Runtime & Environment (reported, not independently verified)
- **Reported Python Version**: Python 3.14.5 (CPython 3.14.5 64-bit on Windows `AMD64`, Windows OS build)
- **Reported Primary Dependencies**: `playwright` (1.63.0), `anthropic` (1.7.0), `Flask` (3.1.3), `beautifulsoup4` (4.15.0), `python-docx` (1.2.0), `pypdf` (6.19.0), `python-dotenv` (1.2.3), `reportlab` (5.0.1), `requests` (2.34.2), `psycopg` (3.3.6), `hypothesis` (6.168.5), `httpx` (0.28.1), `pycountry` (26.2.16), `pytest-xdist` (3.8.0).
- **Reported Environment Isolation**: Global Python environment / `.venv` environment present. Development/testing dependencies specified in `requirements-dev.txt` reported installed for parallel test execution. Historical run logs/environment artifacts were not available to verify these details.

### 2.2 Test Suite Execution Baseline
- **Documented Test Command**: `python -m pytest -q -rs -n auto` (configured in `.github/workflows/ci.yml` line 82; core operational command per `CLAUDE.md` line 141).
- **Historical Primary Discovery Result (reported; unverified)**:
  - Total Tests Discovered: 1941
  - Passed: 1938
  - Failed: 0
  - Errors: 0
  - Skipped: 3
    1. `tests/test_agent_window_is_shown.py:82`: Skipped with message `opens two real Chrome windows on the desktop for a few seconds: set RAISE_WINDOW_TEST=1`. (Requires explicit environment flag to launch interactive UI desktop tests).
    2. `tests/test_delete_application.py:24`: Skipped with message `database not available: connection timeout expired`. (Requires a running PostgreSQL instance on `127.0.0.1:5432`, supplied as a service container in CI but not running as a local background daemon).
    3. `tests/test_delete_application.py:47`: Skipped with message `database not available: connection timeout expired`. (Identical PostgreSQL service dependency).
  - Execution Duration: 849.24 seconds (14 minutes 09 seconds)
- **Historical baseline status**: **UNVERIFIED HISTORICAL BASELINE**
  - The originally reported `1,938 passed / 0 failed / 3 skipped` result remains in this report as a historical claim, not a verified result. No original test log or environment artifact is available to substantiate that run against this SHA.
  - Its 3 reported skips were the interactive desktop test and the two database-backed tests. The later pristine runs had 101 additional skips: 96 tests needing the owner's profile and 5 tests needing ignored local page recordings. The presence of the profile and recordings during the historical run is a plausible explanation for that count difference, but cannot be confirmed from the available historical evidence. The historical database skips were reported as connection timeouts; the new local run skipped because `POSTGRES_PASSWORD` was not set. Therefore the historical result is not fully reconciled.
- **Pristine Windows/Python 3.13.13 baseline**:
  - SHA: `6fb53e6c7a9bce394e67107c48fa5e47d1041e9b`; the detached pristine worktree was tracked-tree clean before execution.
  - Intended CI Python: 3.13 (`.github/workflows/ci.yml`); tested Python: 3.13.13 in a new virtual environment on Windows. Dependencies were installed from `requirements.txt` and `requirements-dev.txt`; no dependency versions were pinned beyond those manifests' declarations.
  - Command, run twice: `python -m pytest -q -rs -n auto`.
  - Both runs: `1 failed, 1836 passed, 104 skipped`. Run 1 took 1005.10 seconds; the recorded Python 3.14.5 run also reproduced the same result (`1 failed, 1836 passed, 104 skipped` in 860.39 seconds).
  - Skip breakdown (104): 96 need `data/profile.json` or CI's `PROFILE_JSON`; 1 requires `RAISE_WINDOW_TEST=1` and a visible Windows desktop; 2 require PostgreSQL and skipped because `POSTGRES_PASSWORD` was not set in the local `.env`; 5 require ignored local recordings under `output/`.
  - The single failure is `tests/test_safety_rules.py::test_a_previously_submitted_job_is_refused`. It also fails when run alone under Python 3.13.13; CI-equivalent PostgreSQL/Linux execution passes it because the configured backend reaches its monkeypatched `db` module.
  - Root cause: the test's fake tracker is injected as `sys.modules["db"]`, but `apply.already_submitted()` now calls `tracking.open_tracker()`. In this environment `tracking.chosen()` selects SQLite because neither `TRACKER` nor `POSTGRES_PASSWORD` is set. The autouse test fixture redirects `config.DB_PATH` to a new per-test temporary database, so the real SQLite tracker is queried and contains no synthetic submitted record; `found` is `None` and `already_submitted()` returns `False`. The fake `Record` and `Tracker` are never used. URL, company, and title matching are not the failing condition; the function never receives the fake record to compare.
  - History confirms the test and original direct-`db` implementation date to 2026-09-15. Commit `0a324317` (2026-09-29, “Run with no database server: SQLite keeps everything Postgres keeps”) changed `apply.already_submitted()` to use the new tracker facade and introduced SQLite selection by default; the test's `db` mock was left unchanged. This is a stale test double / test setup defect, not evidence that the duplicate matching logic itself failed.
  - No production code, tests, fixtures, or configuration were modified for this analysis. The only changes for this update are review documentation.
- **CI-EQUIVALENT REPRODUCIBLE BASELINE (Linux/PostgreSQL)**:
  - Source was checked out from a Git bundle at the same SHA in an isolated Ubuntu 24.04.5 container; the checkout was detached and clean. The container ran on the available WSL2 Docker engine, not on a GitHub-hosted runner.
  - Runtime: Python 3.13.13. PostgreSQL service: `postgres:16`, server 16.15, healthy. CI environment variables were set to `PYTHONUTF8=1`, PostgreSQL database/user `job_agent`, port `5432`, password `ci-only`; `POSTGRES_HOST` was the Docker service alias `postgres` instead of `127.0.0.1` because the test runner and service were separate containers. No `PROFILE_JSON` secret was available.
  - Dependencies were installed with the CI commands: `python -m pip install --upgrade pip`; `pip install -r requirements.txt -r requirements-dev.txt`; `python -m playwright install --with-deps chromium`. The generated environment used pip 26.2.1, pytest 9.1.1, pytest-xdist 3.8.0, Playwright 1.63.0, psycopg 3.3.6, and pycountry 26.2.16. The geo-data check passed.
  - CI test command: `python -m pytest -q -rs -n auto`. Result: `1837 passed, 104 skipped in 395.02s (0:06:35)`; no failures or errors.
  - Skip breakdown: 96 require the unavailable owner profile/`PROFILE_JSON`; 1 requires `RAISE_WINDOW_TEST=1` and a visible desktop; 5 require local ignored page recordings; 2 are Windows desktop-only tests. The PostgreSQL-dependent deletion tests ran rather than skipping.
  - The isolated `tests/test_safety_rules.py::test_a_previously_submitted_job_is_refused` passed on Linux. With `POSTGRES_PASSWORD` present, `tracking.chosen()` selects PostgreSQL; `tracking.open_tracker()` imports `db`, which the test has replaced in `sys.modules` with its fake module. Thus the fake tracker returns the synthetic IGT record and the assertion passes without consulting the PostgreSQL service. On Windows without PostgreSQL configuration, the same test selects SQLite and bypasses its fake `db` module, causing the reproducible failure described above. The platform-specific result is test-fixture/backend coupling, not changed URL/company/title matching behavior.
  - Compared with Windows/Python 3.13 (`1 failed, 1836 passed, 104 skipped`), the CI-equivalent run has one fewer failure and one additional pass. Both have 104 skips, but their composition differs: CI runs two PostgreSQL-backed tests and skips two Windows-only tests; the Windows run skipped the two database tests and ran the Windows desktop tests.
  - This materially differs from the unverified historical claim `1938 passed / 3 skipped`. The Linux run's additional 101 skips relative to that claim are accounted for by 96 missing-profile tests and 5 missing recordings; the historical environment may have supplied those, but no historical artifacts verify it. CI parity therefore confirms the current SHA baseline under this reproduced environment, not the historical run.
- **Prior dirty-worktree incident**: The earlier `BASELINE_CURRENT_TREE_DIRTY` syntax-error report describes a separate worktree event. The unmatched brace was in an uncommitted `page_agent.py` edit, not in this committed SHA. A pristine detached worktree at the SHA compiled the relevant files and enabled the reproducible baseline above.
- **Reference Place Data Check**: `python reference/build_geo.py --check` returned exit code 0 (`reference/geo.json is up to date (pycountry 26.2.16)`).

### 2.3 Linting & Type-Checking Baseline
- **Tooling Status**:
  - No general linter (`flake8`, `ruff`, `pylint`) or type checker (`mypy`, `pyright`) is configured in the repository root or enforced in `.github/workflows/ci.yml`.
  - Custom AST / import checks exist in `.githooks/check_imports.py` (verifies all modules imported by staged files are committed).
  - Regression replay guard exists in `replay_guard.py` (verifies that staged changes do not alter how historical application pages are parsed and answered compared to the previous commit).

### 2.4 Safe Startup Verification
- **CLI Startup (`apply.py`)**:
  - Invoking `python apply.py --help` was executed.
  - `apply.py` lacks a standard CLI argument parser (e.g., `argparse`). It treated `--help` as a target job URL, read user facts from `data/profile.json`, and attempted network fetching against `https://--help`.
  - The process safely terminated after network navigation failed (`Protocol error: Cannot navigate to invalid URL`). No external ATS was contacted, and no user data was written.
- **Dashboard Startup (`launch_dashboard.py`)**:
  - Evaluated dry startup behavior via `visible_desktop.py`.
  - In non-interactive and IDE agent subshells, `visible_desktop.refuse_if_invisible()` detected the execution context (`WinSta0\exebox-...`) and halted with `InvisibleDesktopError`, enforcing the invariant that automated browser actions must never launch hidden on invisible window stations.
  - `python -c "import web_ui"` verified that the dashboard application imports cleanly and discovers 22 Flask routes.
  - `python -c "import config; config.get_user_profile()"` verified clean deserialization of user profile facts without runtime error.

---

## 3. Submission Path Map

Every identified code path capable of triggering, initiating, or contributing to an irreversible final application submission was traced from its entry point to the low-level Playwright browser primitive:

```
========================================================================================================================
PATH 1: Verified Auto-Submit Gate (Intended Autonomous Submission)
========================================================================================================================
[Entry Point]:
  `apply.py:main()` or `web_ui.py:start_run()`
  → [Intermediate Functions]:
      `apply_flow.run_one()`
      → `apply_flow.hand_over()`
      → `safety.evaluate_auto_submit()`
      → `apply_flow.submit_verified()` [File: apply_flow.py:1070-1098]
      → `browser_automation.JobApplicationAssistant.click_verified_submit()` [File: browser_automation.py:6729-6764]
      → `browser_automation.JobApplicationAssistant._click_resiliently()` [File: browser_automation.py:4483-4502]
  → [Final Browser / Network Primitive]:
      1. `locator.click(timeout=10_000)`
      2. `locator.click(force=True, timeout=10_000)`
      3. `locator.dispatch_event("click")`
  → [File:Line]:
      `apply_flow.py:1083`
      `browser_automation.py:6755`
      `browser_automation.py:4491-4493`
  → [Platform / ATS]: Generic / Workday / Greenhouse / Lever / Ashby / SuccessFactors
  → [Confidence]: CONFIRMED
  → [Notes]:
      Guarded by `AUTO_SUBMIT_VERIFIED_ONLY` configuration and `safety.evaluate_auto_submit()`.
      However, once eligible, low-level execution bypasses user interaction and issues Playwright clicks.

========================================================================================================================
PATH 2: Intermediate / Disguised Submit via PageAgent Progression Loop
========================================================================================================================
[Entry Point]:
  `apply_flow.run_page_agent()`
  → [Intermediate Functions]:
      `page_agent.PageAgent.run()`
      → `page_agent.PageAgent.press_next()` [File: page_agent.py:5324-5506]
      → Heuristic classification: `submit_word = safety.is_submit_label(label)`
      → If step counter indicates steps remain (`steps_remain = True`) OR AI plan indicates `next_kind == "next_step"`:
          `final` evaluates to `False`.
          `gate = self.submit_gate(page, controls, last_step=False)` [File: page_agent.py:5438]
          If CAPTCHA, attestation, and legal conflict checks pass, `submit_gate` returns `""` (allow).
  → [Final Browser / Network Primitive]:
      `self.locate(page, control.ref).click(timeout=10_000)`
  → [File:Line]:
      `page_agent.py:5488`
  → [Platform / ATS]:
      Multi-step portals (e.g., Schwab / iCIMS, Workday, Paylocity, Taleo) where intermediate buttons
      are labeled "Submit", "Save and submit", or where step counters are missing/misparsed.
  → [Confidence]: CONFIRMED
  → [Notes]:
      CRITICAL LEAKAGE SEAM: If a portal's final button is labeled "Save and submit" or if the step counter
      heuristic fails, the agent treats the button as a non-final step (`last_step=False`), bypasses the
      final review gate, and clicks the button directly.

========================================================================================================================
PATH 3: Vision-Guided AI Action Click (`look_and_act`)
========================================================================================================================
[Entry Point]:
  `JobApplicationAssistant.apply_to_posting()` (invoked when DOM reading detects no actionable controls)
  → [Intermediate Functions]:
      `browser_automation.JobApplicationAssistant.look_and_act()` [File: browser_automation.py:1999-2070]
      → Screenshot sent to Claude Vision API (`claude.read_page(shot, page.url, goal)`)
      → Claude returns JSON `{click: "<label>", ...}`
      → Label checked against `safe_to_click_for_claude()` [File: browser_automation.py:1989-1997]
      → Locates matching button/link/tab by text or role
  → [Final Browser / Network Primitive]:
      `el.scroll_into_view_if_needed(timeout=3_000)`
      → `el.click(timeout=5_000)`
  → [File:Line]:
      `browser_automation.py:2057`
  → [Platform / ATS]: Unstructured or custom job portals
  → [Confidence]: POSSIBLE
  → [Notes]:
      While `safe_to_click_for_claude` checks `_NEVER_CLICK` (regex for `submit`, `send application`, `finish`),
      any button with an alternative, unlisted, or multilingual label (e.g., "Confirm", "Send", "Transmettre",
      "Finalize", or custom icon buttons without accessible text) will pass filter and be clicked by LLM direction.

========================================================================================================================
PATH 4: Workday Pre-Progression Card Commit
========================================================================================================================
[Entry Point]:
  `sites/workday.py:commit_cards_before_progression()`
  → [Intermediate Functions]:
      Sweeps active DOM for inline work/education cards in edit mode
      → Clicks Save / Update / Done buttons on sub-cards
  → [Final Browser / Network Primitive]:
      `btn.click()` or `btn.evaluate("e => e.click()")`
  → [File:Line]:
      `sites/workday.py:951-953`
  → [Platform / ATS]: Workday
  → [Confidence]: CONFIRMED (contributes directly to form progression state)

========================================================================================================================
PATH 5: Keyboard / Enter Key Submission via Virtual DOM Dispatch
========================================================================================================================
[Entry Point]:
  `interaction.py:fill_and_dispatch()` or `interaction.type_and_commit()`
  → [Intermediate Functions]:
      Focuses input → fills value → dispatches synthetic `input` and `change` events → triggers `Enter` keypress
  → [Final Browser / Network Primitive]:
      `locator.press("Enter")` or `locator.evaluate("el => el.dispatchEvent(...)")`
  → [File:Line]:
      `interaction.py:25-35`, `page_agent.py:4711-4715`
  → [Platform / ATS]: Portals with single-field forms or forms where Enter triggers default `<button type="submit">`
  → [Confidence]: POSSIBLE

========================================================================================================================
PATH 6: Direct Button Click in Authentication / Account Setup
========================================================================================================================
[Entry Point]:
  `page_agent.PageAgent._press_named()`
  → [Intermediate Functions]:
      Finds visible button or link matching regex pattern (e.g., "Create Account", "Sign In", "Continue")
  → [Final Browser / Network Primitive]:
      `found.nth(i).click(timeout=8_000)`
  → [File:Line]:
      `page_agent.py:1674`
  → [Platform / ATS]: Employer account login/registration flows
  → [Confidence]: CONFIRMED

========================================================================================================================
PATH 7: Human-in-the-Loop Hand-Over Review Gate (Preserved Safe Path)
========================================================================================================================
[Entry Point]:
  `apply_flow.hand_over()`
  → [Intermediate Functions]:
      `safety.handover_status()` → evaluates blocking conditions
      → `assistant.raise_window(page)` → brings visible Chromium window to foreground
      → Logs review instructions and halts execution
  → [Final Browser / Network Primitive]:
      Human user manually clicks Submit in the visible browser window
  → [File:Line]:
      `apply_flow.py:832-864`
  → [Platform / ATS]: Universal
  → [Confidence]: CONFIRMED
========================================================================================================================
```

---

## 4. Ambiguous Submission & Retry Map

The repository was analyzed for behaviors that can produce duplicate, uncoordinated, or ambiguous submissions:

### 4.1 Incomplete State Partitioning & Evidence Disconnect
In `apply_flow.py:submit_verified()` (lines 1070–1098):
1. The submission click is issued via `assistant.click_verified_submit(page)`.
2. The agent then waits up to 240 seconds for confirmation evidence via `assistant.wait_for_submission_evidence(page, job.title)`.
3. If no confirmation page, portal entry, or confirmation email is detected within the timeout, `safety.verification_status(None)` returns:
   `("needs_user_review", "You left the application, but no confirmation page, portal entry or email was found...")`.
4. `submit_verified()` returns `False`.
5. `apply_flow.py:821` records the tracker status as `needs_user_review`.

**Critical Vulnerability**:
The tracker (`job_tracker.py:284` and `db.py:295`) implements deduplication via `find_submitted()`. This function executes:
`SELECT * FROM applications WHERE status = 'submitted'`
It **only** queries for `status == "submitted"`. It does **not** recognize:
- `submission_attempted`
- `submission_in_flight`
- `submission_unconfirmed`

If the initial submit click succeeded on the employer's server, but confirmation timed out, the application remains in `needs_user_review`. If the user or an automated queue re-runs the posting URL (`python apply.py <url>`), `already_submitted()` returns `False`. The agent will re-open the form, re-fill the fields, and **attempt submission a second time**.

### 4.2 Retry Loops Around Submit Actions
In `page_agent.py:press_next()` (lines 5500–5505):
```python
after = re.sub(r"\[ref=[\w-]+\]|\[active\]", "", self.snapshot(page))
if after == before:
    alerts = [c for c in re.findall(r"- alert[^:\n]*: (.+)", self.snapshot(page))][:5]
    return "retry", page, ("the page did not move on" + ...)
return "moved", page, ""
```
When `press_next()` clicks a button (including intermediate submit buttons):
- It waits 2,500ms (`self.settle(page, 2_500)`).
- If the employer ATS backend is slow or displays an asynchronous processing spinner that does not immediately alter the accessibility tree snapshot, `after == before` evaluates to `True`.
- `press_next` returns `"retry"`.
- The outer loop in `page_agent.py:3074` catches `"retry"` and re-invokes `press_next()`, clicking the button again.
- **Historical Evidence**: Documented in `page_agent.py:5513` ("on 29 September it pressed Secunetics' 'Submit Application' four times").

---

## 5. Credential & Secret Access Map

The system handles candidate credentials, user facts, and system configuration secrets:

```
+---------------------------------------------------------------------------------------------------+
| A. CANDIDATE / USER / ATS CREDENTIALS & SENSITIVE FACTS                                           |
+----------------------+-----------------------------+--------------------+-------------------------+
| Credential / Fact    | Storage / Sourcing          | Accessed By        | Blast Radius            |
+----------------------+-----------------------------+--------------------+-------------------------+
| ATS_PASSWORD         | .env file                   | config.py:238      | Injected into password  |
|                      | Direct disk read:           | browser_automation | fields across all ATS   |
|                      | Path(".env").read_text()    |   .py:4510         | portals; used in reset  |
|                      | web_ui.py:352 (Form POST)   | page_agent.py:1597 | flows.                  |
+----------------------+-----------------------------+--------------------+-------------------------+
| ATS_EMAIL            | .env file (ATS_EMAIL)       | config.py:237      | Pre-filled into account |
|                      | Fallback: data/profile.json | browser_automation | forms and login fields. |
|                      | (email)                     |   .py:4525         |                         |
+----------------------+-----------------------------+--------------------+-------------------------+
| Persistent Browser   | BROWSER_PROFILE_DIR         | config.py:243      | Grants full access to   |
| Session & Cookies    | (data/browser_profile)      | browser_automation | active session cookies  |
|                      | Persistent Chromium profile |   .py:348          | for Google, LinkedIn,   |
|                      |                             |                    | Indeed, Dice, and ATSs. |
+----------------------+-----------------------------+--------------------+-------------------------+
| User Personal Gmail  | mail.google.com accessed    | browser_automation | Reads inbox emails,     |
| Mailbox              | through persistent browser  |   .py:6227         | extracts 2FA passcodes, |
|                      | session                     | emailed_codes.py:36| follows account verify  |
|                      |                             |                    | links.                  |
+----------------------+-----------------------------+--------------------+-------------------------+
| Full Candidate Fact  | data/profile.json           | config.py:36       | Serialized in bulk into |
| Store (PII & EEO)    | data/profile_answers.json   | page_agent.py:2626 | LLM prompts on every    |
|                      | data/_experience.json       | claude_integration | page evaluation; exposes|
|                      |                             |   .py:plan_page    | sensitive demographics. |
+----------------------+-----------------------------+--------------------+-------------------------+

+---------------------------------------------------------------------------------------------------+
| B. SYSTEM CONFIGURATION SECRETS                                                                   |
+----------------------+-----------------------------+--------------------+-------------------------+
| Secret               | Storage / Sourcing          | Accessed By        | Blast Radius            |
+----------------------+-----------------------------+--------------------+-------------------------+
| ANTHROPIC_API_KEY    | os.getenv, .env             | config.py:200      | External LLM API calls  |
|                      |                             | claude_integration |                         |
+----------------------+-----------------------------+--------------------+-------------------------+
| GEMINI_API_KEY       | os.getenv, .env             | config.py:207      | External LLM API calls  |
|                      |                             | gemini_integration |                         |
+----------------------+-----------------------------+--------------------+-------------------------+
| OPENAI_API_KEY       | os.getenv, .env             | config.py:229      | External LLM API calls  |
|                      |                             | openai_integration |                         |
+----------------------+-----------------------------+--------------------+-------------------------+
| POSTGRES_PASSWORD    | os.getenv, .env             | db.py:684          | Local tracker database  |
|                      |                             | tracking.py:32     |                         |
+----------------------+-----------------------------+--------------------+-------------------------+
```

### Analysis of Secret Leakage Protections
- `safety.py:install_log_redaction()` installs `RedactingFilter` across logging handlers using `redact()`. This masks API keys (`sk-ant-***`), standard `password=` patterns, emails, and phone numbers.
- **Flaws Identified**:
  - `Path(".env").read_text()` in `browser_automation.py:4510` directly inspects the plaintext configuration file at runtime outside of any SecretProvider or authorization boundary.
  - Plaintext password injection directly touches Playwright locators without an isolated cryptographic vault.
  - Full candidate profiles (including disability, ethnicity, gender, sexual orientation, veteran status, legal status, and salary) are serialized wholesale into LLM prompt payloads (`page_agent.py:2652`), violating Minimum Data Disclosure.

---

## 6. LLM → Browser Authority Map

The path from probabilistic model output to deterministic browser execution was traced:

### 6.1 Action Planning via `page_agent.py`
1. **Model Invocation**:
   In `page_agent.py:2925`:
   `plan = PagePlan.from_json(self.claude.plan_page(compact_snapshot(snapshot), self.facts(controls), asked))`
2. **Model Authority**:
   The model returns a JSON plan specifying:
   - `answers`: list of `{ref, question, action, value, source}` where action is `fill`, `choose`, `check`, `uncheck`, `upload_resume`, `upload_cover_letter`.
   - `next`: `{ref, label, kind}` where kind is `next_step`, `final_submit`, `open_application`, `sign_in`, `consent`, `none`.
3. **Execution**:
   In `page_agent.py:3091` (`apply_answers`):
   - The agent loops through `plan.answers`.
   - Calls `refusal = self.refusal(page, answer, control, current_controls)` (line 3126).
   - If not refused, calls `self.do(page, answer, control)` (line 3144), executing `fill_and_dispatch`, `set_checked`, `set_input_files`, or dropdown selections.
4. **Deterministic Validation Present**:
   - `self.refusal()` checks if the field is disabled (`control.disabled`).
   - Checks if question matches `safety.is_attestation()` and ensures signatures require `profile.sign_attestations` and user's full legal name.
   - Enforces that middle name remains empty if profile states none.
   - Rejects non-answers (e.g., "Not provided in resume").
   - Disallows typing passwords via `self.do()`.
5. **Validation Missing**:
   - Free-form text answers generated by the model are directly typed into textareas without validation against a canonical Fact Broker.
   - The model's classification of `next.kind` directly influences whether `press_next` treats a button as intermediate or final.
   - Model-driven navigation clicks are executed without validating whether the destination origin or tenant matches the job posting.

### 6.2 Vision Action Path (`browser_automation.py:look_and_act`)
1. **Model Invocation**:
   In `browser_automation.py:2018`:
   `seen = claude.read_page(shot, page.url, goal)`
2. **Model Authority**:
   Claude Vision outputs a target label string to click.
3. **Execution**:
   The code locates the element by text matching (`scope.get_by_role("button", name=exact)`) and executes `.click()`.
4. **Deterministic Validation**:
   - Filtered by `safe_to_click_for_claude()` against `_NEVER_CLICK` and `is_submit_label`.
   - Filtered by `safety.captcha_visible()`.
   - **Gap**: If the model names an unconventional button label, the system executes a physical click without action authorization.

### 6.3 LLM → JavaScript / `evaluate()` Path
- In all inspected instances of `page.evaluate()` and `locator.evaluate()`, script strings are static, hardcoded JavaScript constants.
- Model output values are passed as parameters (e.g. `loc.evaluate("""(el, value) => { ... }""", answer.value)`), not concatenated into executable script source.
- **Status**: Direct LLM → JavaScript execution was **NOT FOUND** in inspected paths.

---

## 7. Resume / Claim Generation Map

Functionality allowing an LLM to generate or modify application materials:

### 7.1 Architecture & Pipeline
- **Modules**: `apply_flow.py` (`_tailor_with`, `prepare_materials`, `prepare_cover_letter`), `claude_integration.py`, `gemini_integration.py`, `openai_integration.py`, `resume_pdf.py`.
- **Model Ladder**: Claude (`claude-sonnet-5`) → Gemini (`gemini-2.5-pro`) → OpenAI (`gpt-4o`) → static local file fallback (`RESUME_PATH`).
- **Input Provided to LLM**:
  - Full raw text of base resume (`resume.raw_text`)
  - Full job description text (`job.description`)
  - User profile facts (`profile`)

### 7.2 Safety Invariant & Failure Mode
1. **Claim Extraction Guard**:
   In `safety.py:322` (`unsupported_claims`), the system scans the LLM's generated resume text against the base resume text using regexes:
   - `_CLAIM_RE`: numerical metrics, currency amounts, percentages, 4-digit years.
   - `_ACRONYM_RE`: uppercase acronyms (certifications, technologies).
2. **Retry Mechanism**:
   In `apply_flow.py:311-331`, if unsupported claims are found, the system issues a single retry prompt with `extra_instruction`.
3. **CRITICAL TRUST BOUNDARY VIOLATION**:
   In `apply_flow.py:327-333`:
   ```python
   still = safety.unsupported_claims(resume.raw_text, retried)
   if len(still) < len(unsupported):
       tailored, unsupported = retried, still
   resume_txt.write_text(tailored, encoding="utf-8")
   build_resume_pdf(resume_txt, resume_pdf)
   return resume_pdf
   ```
   **Even if unsupported claims remain after the retry**, the function does **NOT** abort or reject the document! It logs a warning, renders the PDF via ReportLab, and returns the tailored resume file.
   The resulting PDF is subsequently attached to the real application and submitted to the employer.
4. **Unconstrained Fact Generation**:
   `safety.unsupported_claims()` only searches for numbers, dates, and acronyms. It does not constrain the LLM from fabricating unheld job titles, altered company names, exaggerated seniority, or unverified narrative claims.

---

## 8. CAPTCHA / MFA / Security Challenge Map

Behavior when encountering anti-automation challenges was inspected across `safety.py`, `browser_automation.py`, `page_agent.py`, and `emailed_codes.py`:

```
+---------------------------------------------------------------------------------------------------+
| CHALLENGE HANDLING CLASSIFICATION TABLE                                                           |
+-------------------------+---------------------+-------------------+-------------------------------+
| Challenge Type          | Classification      | Primary Code Site | Exact Behavior                |
+-------------------------+---------------------+-------------------+-------------------------------+
| reCAPTCHA (v2/Enterprise| STOP / HUMAN        | safety.py:177     | Halts automation immediately; |
| hCaptcha                | HANDOFF             | browser_automation| raises Chrome window; logs    |
| Cloudflare Turnstile    |                     |   .py:2013, 7218  | message; polls for human      |
| Arkose Labs / FunCaptcha|                     | page_agent.py:2696| completion (`captcha_gone_    |
| Generic Bot Text        |                     |                   | polls >= 2`); resumes only    |
|                         |                     |                   | after challenge disappears.   |
+-------------------------+---------------------+-------------------+-------------------------------+
| Emailed One-Time        | DETERMINISTIC       | emailed_codes     | Allowed ONLY if:              |
| Passcode (OTP)          | AUTHORIZED READ     |   .py:36          | 1. check_gmail_for_confirmatio|
|                         |                     | browser_automation|    is True in profile;        |
|                         |                     |   .py:6209        | 2. Employer domain allowed;   |
|                         |                     |                   | 3. No CAPTCHA on page;        |
|                         |                     |                   | 4. < 5 reads / 24h.           |
|                         |                     |                   | Opens Gmail in tab, extracts  |
|                         |                     |                   | code, enters into form.       |
+-------------------------+---------------------+-------------------+-------------------------------+
| Emailed Account         | WEAK HOSTNAME CHECK | emailed_codes     | Uses naive _site_of()         |
| Verification Link       | (CROSS-TENANT RISK) |   .py:70          | (split(".")[-2:]) and         |
|                         |                     | browser_automation| substring matching; subject to|
|                         |                     |   .py:6271        | public-suffix collapse.       |
+-------------------------+---------------------+-------------------+-------------------------------+
| SMS Verification / 2FA  | HUMAN HANDOFF       | browser_automation| No automated SMS interception |
|                         |                     |   .py:7210        | capability exists. Stops and  |
|                         |                     |                   | waits for human completion.   |
+-------------------------+---------------------+-------------------+-------------------------------+
| CAPTCHA Bypass /        | NOT FOUND           | None              | No bypass scripts, solving    |
| Stealth Evasion         |                     |                   | services, or injection hooks  |
|                         |                     |                   | exist in the repository.      |
+-------------------------+---------------------+-------------------+-------------------------------+
```

---

## 9. Authentication Observability & Leakage Risks

Investigation of debugging, logging, and error-reporting mechanisms around authentication:

### 9.1 Visual Artifacts & Screenshots
- **Account Failure Snapshots**:
  In `browser_automation.py:4626`:
  `page.screenshot(path=str(folder / f"{stem}.png"), full_page=True)`
  When an account creation failure occurs, a full-page PNG is saved to `logs/account_failures/`. If the page displays filled password fields (with show-password toggles active), OTP codes, or security questions, the raw bitmap captures them.
- **Account Transition Snapshots**:
  In `page_agent.py:1660`:
  `self.tab(page).screenshot(path=str(stem.with_suffix(".png")), full_page=True)`
  Saved to `{job_dir}/account/{timestamp}_{state}.png`. Captures all authentication states.
- **Vision Model Transmission**:
  In `browser_automation.py:2017`:
  `shot = page.screenshot(full_page=False, timeout=15_000)`
  `seen = claude.read_page(shot, page.url, goal)`
  Transmits the active page bitmap directly to the external Anthropic API. If invoked on an auth page, sensitive visual data is transmitted over the wire.

### 9.2 Tracing & DOM Dumps
- **Accessibility Dumps**:
  In `browser_automation.py:4627` and `perception.py`:
  DOM text dumps pass through `perception.hide_secrets()`, which replaces password characters with asterisks.
- **Playwright Tracing**:
  Playwright trace recording (`context.tracing.start`) was **NOT FOUND** in inspected paths.
- **Console & Diagnostic Dumps**:
  In `apply_flow.py:913` and `state_machine.py:141` (`dump_forensic_failure`):
  Console logs (`_console_logs`), DOM dumps, and screenshots are written to diagnostic folders on unhandled crashes.

---

## 10. Other External Effects

Irreversible and external side-effects beyond final Submit:

```
+---------------------------------------------------------------------------------------------------+
| EXTERNAL EFFECTS INVENTORY                                                                        |
+-------------------------+--------------------+---------------------+------------------------------+
| Effect Category         | Code Location      | Operation Type      | Blast Radius / Impact        |
+-------------------------+--------------------+---------------------+------------------------------+
| ATS Account Creation    | browser_automation | Creates employer    | Permanent account registered |
|                         |   .py:4711, 4947   | account via POST    | in employer's ATS database   |
|                         | page_agent.py:1597 |                     | under candidate's email.     |
+-------------------------+--------------------+---------------------+------------------------------+
| ATS Password Reset      | browser_automation | Triggers employer   | Overwrites candidate's       |
|                         |   .py:6533, 6571   | password reset      | existing account password on |
|                         | page_agent.py:1629 | email and updates pw| employer ATS system.         |
+-------------------------+--------------------+---------------------+------------------------------+
| Email Verification /    | browser_automation | Navigates to email  | Confirms candidate ownership |
| Activation Links        |   .py:6271         | activation URL      | and activates account.       |
+-------------------------+--------------------+---------------------+------------------------------+
| Resume & Document       | page_agent.py:4164 | Uploads PDF/DOCX to | Files stored on employer     |
| Upload                  | browser_automation | remote server via   | servers; persists even if    |
|                         |   .py:771          | file input          | application is abandoned.    |
+-------------------------+--------------------+---------------------+------------------------------+
| Legal Attestations &    | page_agent.py:3229 | Ticks declarations; | Legally binding candidate    |
| Electronic Signatures   |   (sign method)    | types candidate     | assertions submitted without |
|                         |                    | legal name          | per-action confirmation if   |
|                         |                    |                     | sign_attestations is on.     |
+-------------------------+--------------------+---------------------+------------------------------+
| Candidate Fact          | web_ui.py:352      | Modifies local .env | Alters persistent local      |
| Overwrite / .env edits  |                    | and database records| credentials and preferences. |
+-------------------------+--------------------+---------------------+------------------------------+
```

---

## 11. Trust Boundary Violation Map

Summary table of Trust Boundary violations identified during discovery and reconciled with independent review:

| Trust Boundary Requirement | Status | Concrete Code Sites / Evidence | Architectural Firewall Violated |
| :--- | :---: | :--- | :--- |
| **Direct final submission** | **CONFIRMED** | `apply_flow.py:1083` (`click_verified_submit`), `page_agent.py:5488` (`press_next` intermediate submit path). | Effect Firewall |
| **Submission retries after ambiguous result** | **CONFIRMED** | `apply_flow.py:821`, `db.py:304` (`find_submitted` ignores unconfirmed in-flight submissions), `page_agent.py:5504` (`after == before` retry loop). | Effect Firewall |
| **Candidate credential access** | **CONFIRMED** | `config.py:238` (`ats_password`), `browser_automation.py:4510` (reads `.env`), `browser_automation.py:6227` (reads Gmail). | Data Firewall |
| **Candidate credentials sourced from environment** | **CONFIRMED** | `os.getenv("ATS_PASSWORD")` and raw parsing in `config.py` and `web_ui.py:352`. | Data Firewall |
| **LLM → browser command path** | **CONFIRMED** | `page_agent.py:2925` (`PagePlan` actions executed by `self.do()`), `browser_automation.py:2057` (`look_and_act` clicks). | Action Firewall |
| **LLM → JavaScript / evaluate path** | **NOT FOUND** | In inspected paths, `page.evaluate()` executes only static scripts; values are passed as parameterized parameters, not raw code. | Action Firewall |
| **Unrestricted resume rewriting** | **CONFIRMED** | `apply_flow.py:306` (`client.tailor_resume`), `claude_integration.py:140`. Free-form LLM text rewriting without semantic bounds. | Data Firewall |
| **Unsupported claim generation** | **CONFIRMED** | `apply_flow.py:332-335`. When `unsupported_claims` retry fails, the code emits a warning and still writes/uploads the hallucinated document. | Data Firewall |
| **CAPTCHA bypass / evasion** | **NOT FOUND** | In inspected paths, CAPTCHA strictly triggers STOP / HUMAN HANDOFF (`safety.py:198`, `browser_automation.py:7218`). No bypass tools. | Action Firewall |
| **Authentication screenshots** | **CONFIRMED** | `browser_automation.py:4626` (`logs/account_failures/*.png`), `page_agent.py:1660` (`account/*.png`), `browser_automation.py:2017` (sent to Claude). | Data Firewall |
| **Authentication traces** | **NOT FOUND** | Playwright tracing (`context.tracing.start`) was not identified in inspected paths. | Data Firewall |
| **Secret logging** | **SUSPECTED** | `safety.RedactingFilter` exists, but raw tracebacks, `dump_forensic_failure`, or Playwright console logs may capture non-standard formatted tokens. | Data Firewall |
| **Direct browser access to canonical facts** | **CONFIRMED** | `page_agent.py` and `browser_automation.py` directly read `profile.json` facts and compare them inline against DOM elements. | Data Firewall |
| **Browser-side policy decisions** | **CONFIRMED** | `safety.may_overrule`, `safety.is_attestation`, and `page_agent.submit_gate` decide progression policy directly inside browser driving loops. | Action Firewall |
| **Irreversible effects without explicit guard** | **CONFIRMED** | Account creation (`create_account`), password reset (`recover_rejected_sign_in`), and document upload execute without an explicit interactive gate. | Effect Firewall |
| **Full candidate profile data sent to model** *(Reconciled)* | **CONFIRMED** | `page_agent.py:2626-2659` (`PageAgent.facts()`) serializes `UserProfile` containing sensitive demographic and EEO data into prompt payload. | Data Firewall |
| **Model-selected navigation without origin/tenant bounds** *(Reconciled)* | **CONFIRMED** | `page_agent.py:5324-5490` (`press_next()`) clicks model-selected link/action controls without destination origin or tenant validation. | Action Firewall / Effect Firewall |
| **Cross-tenant / public-suffix email link validation weakness** *(Reconciled)* | **CONFIRMED** | `emailed_codes.py:57-88` uses naive `split(".")[-2:]` and substring matching, vulnerable to public-suffix collapse and cross-tenant confusion. | Data Firewall / Action Firewall |

- **Summary of Findings**:
  - **CONFIRMED**: 14
  - **SUSPECTED**: 1
  - **NOT FOUND**: 3

---

## 12. Legacy Coupling / Future Strangulation Seams

The existing system exhibits severe architectural coupling where concerns are intermixed within monolithic modules:

```
+---------------------------------------------------------------------------------------------------+
| MONOLITHIC COUPLING BREAKDOWN                                                                     |
+--------------------------+------------+-----------------------------------------------------------+
| Module                   | Line Count | Mixed Concerns Identified                                 |
+--------------------------+------------+-----------------------------------------------------------+
| browser_automation.py    | 7,375      | Playwright locators, DOM polling, Workday heuristics,     |
|                          |            | account login/creation, password reading from .env, Gmail |
|                          |            | inbox reading, screenshot capture, vision LLM calls,      |
|                          |            | verified auto-submit clicks, modal sweeping.              |
+--------------------------+------------+-----------------------------------------------------------+
| page_agent.py            | 5,679      | Snapshot parsing, Claude page planning, LLM fallback,     |
|                          |            | field filling, cascading dropdown logic, attestation      |
|                          |            | signing, submit gating, circuit breaking, retry loops.    |
+--------------------------+------------+-----------------------------------------------------------+
| apply_flow.py            | 1,777      | High-level workflow orchestration, LLM resume tailoring,  |
|                          |            | document conversion, evidence collection, auto-submit     |
|                          |            | evaluation, tracker DB event recording.                   |
+--------------------------+------------+-----------------------------------------------------------+
```

### High-Risk Shared Helpers & Future Strangulation Seams
1. **The Effect Firewall Seam (`_click_resiliently` & `locator.click`)**:
   - `browser_automation.py:4483` (`_click_resiliently`) is a generic utility used by form progression, modal dismissals, and final application submission alike.
   - *Seam Target*: All irreversible side-effects (final submit clicks, account creations, document uploads) must be routed through an explicit, out-of-process Effect Gateway rather than invoking Playwright primitives directly.
2. **The Data Firewall Seam (`get_user_profile` & `_read_ats_password`)**:
   - Facts and secrets are currently read directly from disk via global module imports (`config.get_user_profile()`, `Path(".env").read_text()`).
   - *Seam Target*: Sourcing must be strangulated behind a read-only, audited `FactBroker` and a fail-closed `SecretProvider`.
3. **The Action Firewall Seam (`submit_gate` & `press_next`)**:
   - `page_agent.py:5324` mixes navigation progression, error retrying, circuit breaking, and final submission gating in a single loop.
   - *Seam Target*: Navigation actions must be separated from irreversible submission actions with provable, deterministic state transitions.

---

## 13. Assumptions, Unknowns & Unverified Paths

1. **Assumptions Made**:
   - Analysis of Playwright browser execution assumes standard Chromium engine behaviors on Windows.
   - Analysis of external LLM responses assumes adherence to JSON schema outputs as structured in prompt templates.
2. **Areas Not Inspected / Limitations**:
   - Live external ATS portals (Workday, SuccessFactors, Taleo, iCIMS) were not dynamically navigated with active live accounts during this discovery phase to avoid making real external side effects.
   - The PostgreSQL storage adapter was not actively connected during local testing because no local Postgres daemon was listening on port 5432; testing relied on SQLite and mocked connection tests.
3. **Dynamic Uncertainties Requiring Characterization Tests in P0-B**:
   - The exact behavior of `_click_resiliently` on multi-page forms when network latency causes DOM mutations to lag beyond 2,500ms.
   - The exact boundary conditions under which `page_agent.py:press_next` encounters ambiguous submit buttons on newly created ATS portals.
   - Independent test baseline run completion status.

---

## 14. Phase 0-B Inputs & Scope Control

### 14.1 Scope Control Boundaries
To preserve execution safety and prevent architectural drift, strict boundaries govern Phase 0:

- **Phase 0 MAY Include**:
  - `SecretProvider` for user/ATS credentials and `SecretValue` redaction wrappers.
  - `SubmissionGateway` / `EffectGateway` and `SubmissionGuardV0` primitive interceptors.
  - Durable submission state machine (`SUBMISSION_IN_FLIGHT`, `SUBMISSION_AMBIGUOUS`) and idempotency locking.
  - Append-only safety event recording.
  - Isolated browser `SubmissionExecutor`.
  - CAPTCHA stop/handoff invariants.
  - Authentication screenshot/trace safety and visual redaction.
  - Disabling unsafe resume rewriting (fail-closed claim enforcement).
  - Minimum-data projection needed to stop immediate LLM PII leakage.
  - Architectural and characterization tests.

- **Phase 0 MUST NOT Expand Into**:
  - Full `FactBroker` multi-service implementation.
  - Complete Question Ontology framework.
  - PostgreSQL / Redis / Celery distributed services or workers.
  - Cryptographic capability architecture.
  - Multi-user tenancy models.
  - Wholesale legacy module rewrites.

### 14.2 Concrete Findings for Phase 0 Implementation
1. **Submit Button Hard-Locking (Effect Firewall)**:
   Any Playwright locator resolving to a submit control (by text, ARIA role, form action, or Enter dispatch) must be structurally intercepted. The LLM must possess zero authority to trigger an irreversible submit primitive.
2. **Idempotency & State Tracking (Effect Firewall)**:
   The application state machine must introduce explicit `SUBMISSION_IN_FLIGHT` and `SUBMISSION_AMBIGUOUS` states. `find_submitted()` must query across all non-terminal submission states to prevent duplicate submissions on retry.
3. **Fail-Closed Document Tailoring (Data Firewall)**:
   When `safety.unsupported_claims()` detects unsupported metrics, years, or acronyms, the system must fail-closed and abort the tailoring process or fall back strictly to the static unedited base resume, rather than uploading hallucinated text.
4. **Credential Isolation (Data Firewall)**:
   Raw calls to `Path(".env").read_text()` in `browser_automation.py` must be eliminated. Credentials must be brokered through a dedicated `SecretProvider` that prevents credentials from ever being rendered into DOM trees or visual snapshots.
5. **Decoupling Navigation from Submission (Action Firewall)**:
   `page_agent.py:press_next()` must be disentangled so that navigation clicks cannot escalate into submission clicks under any step-counter or heuristic failure.
6. **Minimum-Data LLM Projection (Data Firewall Containment)**:
   Immediate Phase 0 containment requires scrubbing sensitive voluntary self-identification fields (`disability_status`, `ethnicity`, `gender`, `sexual_orientation`, `veteran_status`, `felony_conviction`, `security_clearance`, `salary_min/max`) from the `facts` dictionary passed to `plan_page` unless an on-page question has deterministically matched an EEO field requirement.
7. **Destination Origin Boundary Verification (Action Firewall)**:
   Before following model-selected navigation or opening links from email, the destination URL must be validated against the job posting origin and vendor-tenant domain, using public-suffix aware parsing.

---

## 15. Independent Review Reconciliation

### 15.1 Individual Finding Reconciliation

#### Finding 1: Final submission can bypass the explicit handoff gate through an intermediate/disguised submit path
- **Finding**: A button carrying a submit label or review indicator can be clicked directly without user review if the step counter or AI plan classifies it as intermediate (`next_step`), causing `submit_gate(..., last_step=False)` to execute without final submission guards.
- **Independent Classification**: AGREE
- **Reconciliation Status**: **ACCEPTED**
- **Evidence**: `page_agent.py:5416-5488` (`PageAgent.press_next()`). Lines 5426–5427 compute `final`. When `steps_remain` is evaluated as True, `final` evaluates to False; execution enters `elif submit_word: gate = self.submit_gate(page, controls, last_step=False)` (line 5438). If CAPTCHA, attestation, and legal conflict checks pass, `submit_gate` returns `""` (allow) and line 5488 executes `self.locate(page, control.ref).click(timeout=10_000)` directly on the submit control.
- **Final Discovery Classification**: **CONFIRMED**
- **Phase 0 Implication**: A `SubmissionGuardV0` / `EffectGateway` must intercept all submit-capable Playwright click primitives at the driver boundary, independently of whether caller logic believes the step is intermediate.
- **Remaining Uncertainty**: None. Statically confirmed.

#### Finding 2: Model-selected navigation can occur without destination/origin/tenant restrictions
- **Finding**: When the model planner outputs a navigation action, the agent clicks the control without validating that the target URL, hostname, or tenant remains on the expected ATS origin or within the legitimate job application flow.
- **Independent Classification**: DISCOVERY UNDERSTATED
- **Reconciliation Status**: **ACCEPTED**
- **Evidence**: `page_agent.py:5324-5490` (`press_next()`). The agent clicks `control.ref` corresponding to `plan.next_ref` / `plan.next_label`. No origin or destination tenant validation exists before or after the click, allowing unvalidated navigation off-domain to arbitrary third-party or hostile URLs.
- **Final Discovery Classification**: **CONFIRMED**
- **Phase 0 Implication**: Phase 0 Action Firewall must validate navigation destinations against the canonical job origin and ATS vendor tenant boundaries before and immediately after progression clicks.
- **Remaining Uncertainty**: Dynamic testing needed to determine if external iframes inside ATS pages can trigger top-level window redirection.

#### Finding 3: Email verification link validation uses an overly coarse hostname comparison
- **Finding**: Verification link safety validation collapses hostnames to the last two dot-separated labels, failing on multi-part public suffixes, and uses loose substring matching for vendor tenants.
- **Independent Classification**: AGREE
- **Reconciliation Status**: **ACCEPTED**
- **Evidence**: `emailed_codes.py:57-88` (`_site_of`, `verification_link_ok`). `_site_of()` takes `split(".")[-2:]`, treating `example.co.uk` as site `co.uk`. For multi-tenant ATS vendors, `site_host.split(".")[0] not in link.lower()` matches if the tenant prefix appears anywhere in the path or query string (e.g. `https://attacker.myworkdayjobs.com/verify?employer=tenant`), permitting cross-tenant confusion.
- **Final Discovery Classification**: **CONFIRMED**
- **Phase 0 Implication**: Link validation must use public-suffix aware domain parsing and exact subdomain/tenant matching before opening links from the user's mailbox.
- **Remaining Uncertainty**: Complete inventory of all vendor-specific tenant URL patterns across all supported ATS portals.

#### Finding 4: Ambiguous post-submit outcomes can lead to duplicate applications on subsequent runs
- **Finding**: If a submit click succeeds but confirmation evidence times out or cannot be found, status reverts to `needs_user_review`. Deduplication queries check only `status = 'submitted'`, allowing re-runs to re-open and re-submit the application.
- **Independent Classification**: AGREE
- **Reconciliation Status**: **ACCEPTED**
- **Evidence**: `apply_flow.py:1070-1098` (`submit_verified()`), `apply_flow.py:821`. Tracker deduplication in `job_tracker.py:284` and `db.py:295` (`find_submitted()`) filters exclusively on `status = 'submitted'`. In `page_agent.py:5500-5504`, DOM latency where `after == before` triggers immediate within-run click retries.
- **Final Discovery Classification**: **CONFIRMED**
- **Phase 0 Implication**: Phase 0 Effect Gateway must implement durable submission state (`SUBMISSION_IN_FLIGHT`, `SUBMISSION_AMBIGUOUS`) and idempotency locking to prevent subsequent re-submissions.
- **Remaining Uncertainty**: Distribution of confirmation latency across third-party ATS platforms.

#### Finding 5: Full candidate profile data, including sensitive personal facts, is transmitted to the model
- **Finding**: The system serializes the complete candidate profile—including sensitive voluntary self-identification data such as disability, ethnicity, gender, sexual orientation, veteran status, legal status, and compensation expectations—and passes it in LLM prompts on every page evaluation, violating minimum data disclosure.
- **Independent Classification**: DISCOVERY MISSED
- **Reconciliation Status**: **ACCEPTED**
- **Evidence**: `page_agent.py:2626-2659` (`PageAgent.facts()`) serializes `UserProfile` into `"profile": profile`. In `claude_integration.py:plan_page` (and Gemini/OpenAI equivalents), this entire payload is transmitted in LLM prompts on every page evaluation, even when answering basic fields like name or job title. `config.py:42-140` defines sensitive fields including `disability_status`, `ethnicity`, `gender`, `sexual_orientation`, `transgender`, `veteran_status`, `felony_conviction`, `security_clearance`, `salary_min/max`.
- **Final Discovery Classification**: **CONFIRMED**
- **Phase 0 Implication**: Immediate Phase 0 containment requires minimum-data projection—scrubbing sensitive demographic and unneeded fields from the prompt payload unless explicitly required by a validated on-page question. Full FactBroker and Question Ontology remain deferred to later phases per Scope Control.
- **Remaining Uncertainty**: None. Statically confirmed in `config.py` and `page_agent.py`.

#### Finding 6: Logs, screenshots, HTML dumps, traces, or forensic artifacts may expose applicant-sensitive information
- **Finding**: Visual screenshots and diagnostic failure dumps capture raw, unredacted bitmaps and logs that can include sensitive applicant data, passwords, OTPs, or session tokens.
- **Independent Classification**: AGREE / DISCOVERY MISSED (regarding artifact surface)
- **Reconciliation Status**: **ACCEPTED**
- **Evidence**: `browser_automation.py:4626` captures full-page unredacted PNGs to `logs/account_failures/`; `page_agent.py:1660` captures full-page PNGs to `{job_dir}/account/`; `browser_automation.py:2017` transmits unredacted viewport screenshots to Anthropic Vision API; `dump_forensic_failure` writes raw console logs and DOM snapshots to disk. `safety.RedactingFilter` only masks standard API keys and email/phone patterns in logging text, leaving visual bitmaps and forensic dumps unredacted.
- **Final Discovery Classification**: **CONFIRMED**
- **Phase 0 Implication**: Phase 0 must restrict visual screenshots around authentication contexts, disable transmitting unmasked auth screens to vision models, and sanitize forensic failure dumps.
- **Remaining Uncertainty**: Extent of third-party ATS console logging that may contain sensitive session cookies or tokens.

#### Finding 7: Unsupported resume claims may survive the heuristic claim checker and reach uploaded materials
- **Finding**: The resume claim checker relies on regex heuristics and fails open if retries still contain unsupported claims, allowing hallucinated application materials to be uploaded and submitted.
- **Independent Classification**: AGREE
- **Reconciliation Status**: **ACCEPTED**
- **Evidence**: `apply_flow.py:306-335` (`_tailor_with`). `safety.unsupported_claims()` evaluates generated resume text using regexes for numbers, metrics, dates, and acronyms. If unsupported claims persist after one retry, the system logs a warning but proceeds to compile the tailored PDF and return it for upload. Furthermore, regexes do not catch hallucinated skills, responsibilities, or unheld job titles.
- **Final Discovery Classification**: **CONFIRMED**
- **Phase 0 Implication**: Phase 0 must fail-closed: if unsupported claims are detected and not eliminated, resume tailoring must abort or strictly fall back to the unmodified base resume (`RESUME_PATH`).
- **Remaining Uncertainty**: False-positive rate of heuristic claim checkers on legitimate stylistic rewrites.

#### Finding 8: Baseline test suite execution status discrepancy
- **Finding**: The discovery report historically recorded `1,938 passed, 0 failed, 3 skipped`, but that run has no original test output or environment artifact available for independent confirmation.
- **Independent Classification**: CI-EQUIVALENT REPRODUCIBLE BASELINE established; historical claim remains unverified.
- **Reconciliation Status**: **RECONCILED WITH RESIDUAL HISTORICAL UNCERTAINTY**
- **Evidence**: On pinned SHA `6fb53e6c7a9bce394e67107c48fa5e47d1041e9b`, Windows/Python 3.13.13 reproduced `1 failed, 1836 passed, 104 skipped`; the CI-equivalent Ubuntu 24.04.5/Python 3.13.13/PostgreSQL 16.15 replay produced `1837 passed, 104 skipped`. The sole Windows failure is the stale backend-sensitive test double in `tests/test_safety_rules.py::test_a_previously_submitted_job_is_refused`; it passes under PostgreSQL configuration.
- **Final Discovery Classification**: Historical `1,938 passed / 3 skipped` = **UNVERIFIED HISTORICAL BASELINE**. Current CI-equivalent replay = **1837 passed, 104 skipped, 0 failed**.
- **Phase 0 Implication**: No baseline-related gate remains before human discovery approval. Phase 0-B must not start until a human approves the discovery findings.
- **Remaining Uncertainty**: The original historical execution environment and availability of the owner profile and local recordings cannot be established from available artifacts. The CI-equivalent replay was not executed on a GitHub-hosted runner.

---

### 15.2 Reconciliation Summary

- **CONFIRMED findings**: 14
- **SUSPECTED findings**: 1 (`Secret logging` in raw tracebacks, diagnostic dumps, or Playwright console logs)
- **NOT FOUND findings**: 3 (`LLM → JavaScript / evaluate path`, `CAPTCHA bypass / evasion`, `Authentication traces`)
- **NEEDS FURTHER VERIFICATION**: 0 baseline execution items
- **Baseline verification state**:
  - `HISTORICAL BASELINE`: **UNVERIFIED HISTORICAL BASELINE**, reported as 1,938 passed, 0 failed, 3 skipped (849.24s)
  - `CI-EQUIVALENT REPRODUCIBLE BASELINE`: **1837 passed, 104 skipped, 0 failed** on pinned SHA under Ubuntu 24.04.5, Python 3.13.13, PostgreSQL 16.15
  - `WINDOWS / PYTHON 3.13.13`: 1 failed, 1836 passed, 104 skipped; stale test-double/backend interaction is documented above
  - The 101 skips beyond the historical report are accounted for by 96 unavailable owner-profile tests and 5 unavailable local recording tests; historical availability is unknown.
- **Final recommendation**: **READY FOR HUMAN DISCOVERY APPROVAL**, with the historical result, unavailable artifacts, and non-GitHub-hosted replay recorded as residual limitations. No production code was modified. Do not begin Phase 0-B until a human approves the discovery findings.
