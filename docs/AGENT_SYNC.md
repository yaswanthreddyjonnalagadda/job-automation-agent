# Multi-Agent Synchronization & Project Organization Hub

> **Read by:** Claude Code (`CLAUDE.md`), Codex (`AGENTS.md`), and Antigravity (`AGENTS.md`).
> **Purpose:** Keep all coding agents synchronized on branch state, active work, repository structure, failure catalogue numbering, and the shared cross-portal backlog so agents never duplicate work, overwrite in-flight edits, or fix only one symptom per run.

---

## 1. Multi-Agent Coordination Protocol

Whenever **Claude Code**, **Codex**, or **Antigravity** starts a task in this repository:

1. **Run the Pre-Flight Status Check First (`python scripts/agent_status.py`)**:
   - At the start of every session or task, run `python scripts/agent_status.py`.
   - It reports in `<0.5s`: current branch & recent commits, **uncommitted working-tree files**, **pending branches & stashes**, the **latest failure ID (`fNNN`)**, **pending/stopped applications in `data/applications.db`**, the **latest saved page run in `output/`**, and the **open backlog items** in this file.
   - **Never revert or overwrite another agent's in-flight edits** (`git checkout`, `git restore`, etc.). If another agent has staged or unstaged work, read `git diff` and build on top of it cleanly.
   - Check the highest failure number in `reference/failures/f*.json` (reported by `agent_status.py`) before assigning a new `fNNN` ID so two agents never collide.

2. **Stop the "One-Symptom-Per-Run" Loop**:
   - When a live application run stops or fails (`output/<Company_Role>/pages/<Run>/page_XX.txt`), **inspect every field, dropdown, and error on that page and any subsequent saved steps** before declaring the fix done.
   - Example: If Step 3 has both an ambiguous sponsorship dropdown AND a numeric salary box where the AI wrote a paragraph, fix **both** in the same pass rather than waiting for the next browser run to fail on the second field.

3. **Fix Every Call Site in the Subsystem**:
   - **Option matching**: Put shared option-matching logic in [`option_match.py`](../option_match.py) and ensure all selection paths call it (`concept_matcher.best_option_match`, `page_agent.PageAgent._pick`, radio groups, `native_select`, `_choose_inside`, and `browser_automation._best_option`).
   - **Question recognition & profile resolution**: Put new question patterns in [`reference/concepts.json`](../reference/concepts.json) and [`concept_matcher.py`](../concept_matcher.py), and user facts in [`config.py`](../config.py) (`UserProfile`).
   - **Portal-specific DOM behavior**: Put platform-specific selectors, step detection, and widget quirks in [`sites/`](../sites/) (`workday.py`, `greenhouse.py`, `adp.py`, `icims.py`, etc.), not buried inside 6,000-line generic loops.

4. **Update This File (`docs/AGENT_SYNC.md`) After Meaningful Changes**:
   - Update **Section 3 (Current State)** with the latest `fNNN` ID and active branch/PR.
   - Mark completed items `[x]` and add newly discovered cross-portal bugs to **Section 4 (Shared Backlog)**.

---

## 2. Canonical Repository & Subsystem Map

All Python modules live at the repository root (so imports and `replay_guard.py` remain stable) and `sites/`. Every file belongs to one of these 9 subsystems:

| Subsystem | Files | Responsibility & Rule |
| :--- | :--- | :--- |
| **1. Entrypoints & Orchestration** | `web_ui.py`, `launch_dashboard.py`, `apply.py`, `apply_flow.py`, `state_machine.py`, `run_outcomes.py`, `application_status.py` | Flask dashboard, CLI runner, high-level application wizard loop, and terminal outcome classification. |
| **2. Form Perception & Interaction** | `page_agent.py`, `browser_automation.py`, `perception.py`, `form_fields.py`, `interaction.py`, `repeated_entries.py`, `location_choice.py`, `action_result.py` | Accessibility snapshot parsing, DOM interaction primitives (`fill_and_dispatch`), detached dropdowns, and repeatable Work/Education sections. |
| **3. Answer Resolution & Knowledge** | `concept_matcher.py`, `option_match.py`, `reference/concepts.json`, `reference/geo.json`, `geo_reference.py`, `answer_bank.py`, `open_answers.py`, `employment_history.py`, `field_requirements.py`, `employer_names.py` | Resolves questions strictly in order: Profile $\rightarrow$ Saved Answers $\rightarrow$ Answer Bank $\rightarrow$ Deterministic Rule $\rightarrow$ AI $\rightarrow$ User. Never hardcode place names in Python (`reference/geo.json` only). |
| **4. Portal Adapters (`sites/`)** | `sites/__init__.py`, `sites/base.py`, `sites/workday.py`, `sites/greenhouse.py`, `sites/lever.py`, `sites/ashby.py`, `sites/successfactors.py`, `sites/amazon.py`, `sites/eightfold.py` | Platform-specific selectors, upload slots, custom pickers, account state, and confirmation phrases. |
| **5. Auth, Accounts & Gmail** | `account_state.py`, `login_guard.py`, `emailed_codes.py`, `sign_in_browser.py` | ATS account classification (`SIGN_IN_FORM`, `CREATE_FORM`, `VERIFY_EMAIL`, `ALREADY_APPLIED`), Gmail OTP/verification link reading (`emailed_codes.why_not()`), and persistent browser sign-in helper. |
| **6. Safety, Provenance & Guards** | `safety.py`, `submission_guard.py`, `provenance.py`, `visible_desktop.py`, `web_guard.py` | **Owner approval required to modify `safety.py`.** Enforces `SubmissionGuardV0`, attestation rules, blocked password domains, and `may_overrule()` provenance. |
| **7. AI Providers & Documents** | `ai_models.py`, `model_ladder.py`, `ai_choice.py`, `gemini_integration.py`, `claude_integration.py`, `claude_answers.py`, `openai_integration.py`, `jd_analyzer.py`, `resume_parser.py`, `resume_pdf.py`, `profile_setup.py`, `web_setup.py` | Model quota ladders (Gemini/Claude/OpenAI), resume parsing/tailoring, cover letter generation, and profile onboarding. |
| **8. Persistence, Diagnostics & Replay** | `tracking.py`, `job_tracker.py`, `db.py`, `checkpoint.py`, `recovery.py`, `diagnostics.py`, `runtime_events.py`, `structured_logging.py`, `stop_cause.py`, `run_diagnostics.py`, `replay_guard.py`, `next_project_knowledge.py` | SQLite/Postgres application tracker, Stop-Cause diagnostic bundles, privacy sanitization, and the 609-page pre-commit `replay_guard.py`. |
| **9. Handoff & Alerts** | `handoff.py`, `handoff_session.py`, `phone_console.py`, `phone_alert.py`, `ui_shell.py`, `web_progress.py` | Live Agent Cockpit handoff controls and mobile/desktop alerts. **The Oracle remote worker is being removed** ([PR #35](https://github.com/yaswanthreddyjonnalagadda/job-automation-agent/pull/35), owner's instruction 17 Oct 2026): `durable_queue.py`, `worker_daemon.py`, `worker_manager.py`, `resource_manager.py`, `remote_client.py`, `ssh_remote_client.py`, `remote_cli.py` and `scripts/remote/` -- do not extend them. Oracle Recruiting Cloud as a *job portal* (`reference/ats_fields/oracle.json`) stays. |

---

## 3. Current Repository State

- **Active Working Branch**: `fix/posting-apply-link-any-plan` (Open **[PR #34](https://github.com/yaswanthreddyjonnalagadda/job-automation-agent/pull/34)**)
- **Default Base Branch**: `main`
- **Codex context refresh (17 October 2026)**: Reviewed this hub, operational rules, runtime behavior, recent commits and f183–f185. Verified PRs #34 and #35 are open (CI in progress at inspection), latest catalogue ID remains f185, and local SQLite records one pending application: Invesco (`needs_user_review`). The 10 backlog items remain open; no runtime fix or live application verification was performed in this refresh. Preserved the pre-existing `scripts/agent_status.py` trailing-newline edit and untracked `.claude/`. WSL Git status exceeded the checker’s 5-second timeout; Windows Git verified the branch and working tree.
- **Other open PR**: [PR #35](https://github.com/yaswanthreddyjonnalagadda/job-automation-agent/pull/35) `chore/remove-oracle-worker` -- removes the Oracle remote worker (44 files). Both #34 and #35 append to the end of `BEHAVIOUR.md`; whichever merges second needs that one conflict resolved.
- **Live test job**: Invesco Workday (application 62, R-13974) -- **SUBMITTED** 17 Oct 2026 23:39 UTC (status `submitted`, the page confirmed it). The run went account creation -> email verification -> My Experience (f183) -> Application Questions (f184) -> Voluntary Disclosures (f185, Veteran Status left blank) -> Review (step 5/5). No application is pending now.
- **Latest Failure Catalogue ID**: `f204` (`reference/failures/f204-silent-form-stop-diagnostics.json`) — **Next available ID: `f205`**
- **Codex iCIMS integration (19 October 2026)**: Integrated f202 (embedded frame CAPTCHA observation in `browser_automation.py` and `apply_flow.py`), f203 (owner continue clears matching `_created_at` retry hold in `page_agent.py`), and f204 (silent form stop structural diagnostics in `diagnostics.py`).
- **Recently Completed Fixes on Active Branch (`f170`–`f204`)**:
  - `f204`: Silent form stop diagnostics (`diagnostics.py`, `apply_flow.py`, `browser_automation.py`).
  - `f203`: Owner continue clears matching in-memory creation retry hold (`page_agent.py`).
  - `f202`: Embedded ATS frame CAPTCHA observation and account-helper reason preservation (`browser_automation.py`, `apply_flow.py`).
  - `f201`: Clear pending account creation marker across key aliases on owner continue (`page_agent.py`, `apply_flow.py`).
  - `f200`: Dynamic CAPTCHA detection after account or profile submission (`browser_automation.py`).
  - `f199`: Support HTMLInputElement (`input[type=submit]`) in credential and candidate profile creation gateways (`submission_guard.py`).
  - `f198`: Candidate profile gateway and iCIMS step finality on multi-step ATS builders (`submission_guard.py`, `browser_automation.py`).
  - `f197`: Clear durable `account_creation_dispatched` marker in `apply_flow.py` when owner signals `continue` to break infinite blocker loop; add iCIMS `candidate_account_state` and profile creation button matching in `browser_automation.py` and `sites/icims.py`.
  - `f196`: CAPTCHA wait loop advances once page moves on.
  - `f170`–`f180`: Workday posting Apply-link GET navigation, active-dialog account scoping, `"applied before"` $\rightarrow$ Sign In rule, Create Account error reading & consent re-verification, and `"Already applied"` terminal status.
  - `f181`–`f182`: Persistent agent browser Google sign-in support and `?authuser=<email>` mailbox targeting.
  - `f183`: Skip optional Workday Education section when tenant's School lookup returns empty (`"No Items."`).
  - `f184`: Sponsorship-qualified work authorization options (`"Yes, without restrictions"` vs `"Yes, with company sponsorship"`) follow `profile.requires_visa_sponsorship`.
  - `f185`: Optional questions whose dropdown lacks the owner's answer (e.g. Invesco's optional `Veteran Status`) are left blank with a note (`OPTIONAL_LEFT_BLANK`) instead of stopping the run.
  - `f186` (commit e772800): a Review step (answers shown as text, the application's Submit button, no inputs) counts as the application form in `apply_flow.hand_over`; it was reported "The application form was never reached". Cross-portal: any portal whose last step is text-only.
  - `f187` (commit 7f50da4): Expected earnings (`"expect to earn per year"`) and desired annual/base compensation (`"desired annual compensation"`) match `DESIRED_SALARY` and return plain numeric `salary_min` (`"125000"`) when the prompt asks for a numerical value; sponsorship-qualified `"Yes"` options (`f184`) are unified across radio groups (`_control_for`, `do`), cached `native_select` recipes, and `_choose_inside`.
  - `f188`: Preserve alphanumeric characters in `PageAgent.complete_account_code` for Greenhouse 8-character security codes (`"EvPGU6Qy"`) and match `?gh_jid=` in `GreenhouseAdapter`; exclude `Which/What/Where` questions from `RESIDES_IN` so `"Which country are you located in?"` resolves to `COUNTRY`; add `GOVERNMENT_ASSISTANCE` (`profile.received_government_assistance`) and `"receive a verification code"` channel selection; add `EDUCATION_SKIPPED` / `OPTIONAL_LEFT_BLANK` and wizard step labels to `diagnostics.py`; and add a 200ms TCP probe in `db.get_tracker()`.
  - `f189`: Fix SmartRecruiters guest apply false-positive `VERIFY_EMAIL` auth blocker by guarding with `not _application_fields(snapshot)` and recognizing account creation verification-sent notifications; add numeric interval matching for salary/compensation and experience buckets in `option_match.py`; decouple file upload buttons (`"Choose file"`, `"Upload file"`) from generic dropdown placeholders in `page_agent.py`; implement dedicated `IcimsAdapter` in `sites/icims.py` scoping Playwright operations to `#icims_content_iframe`; and resolve employer from URL path in `job_sources.py` when host matches `ATS_VENDORS`.
  - `f190`–`f194` (Claude Code, commit 00d4116; from Codex's 18 Oct Datadog audit): `f190` with no writer key, `hasattr(brain, "cost_summary")` answers "absent" instead of crashing before the browser opens (`ai_choice.Writers`); `f191` **`option_match.sponsorship_said(text)` is now the one reading of a sponsorship-qualified row** (True/False/None), used by `sponsorship_choice` AND `safety.legal_answer_conflicts` -- "No, I need sponsorship now" to an authorized-to-work question is no longer a conflict for an owner who needs sponsorship (owner-approved `safety.py` change); `f192` a REQUIRED box whose profile field is blank on purpose takes `config.REQUIRED_STAND_IN` (preferred_name -> first_name) and is not emptied, and `required_questions` reads `Label*` without a space; `f193` `waiting_note_for` never returns the signal path itself; `f194` `library_answer` ranks matches (exact question, then the longest regex match) instead of first-in-file.
  - `f195` (Claude Code, commit 5a271b0): `account_state._knows_account` counts an account the agent **created in this run** (`memory.created`); a refused sign-in right after creation (Workday says only "wrong email or password or locked" for an unverified new account) goes VERIFY_BY_LINK (`verify_account_by_email_link(just_created=True)`) -> RESET_PASSWORD -> owner. Live test: R. L. Polk Mobility (application 81, wd503) restarted with it.
  - `f196`: In `browser_automation._wait_for_signal`, track initial URL path and password field count during `for_captcha=True`; when the page navigates away (e.g. from iCIMS profile creation to `/candidate?from=profilebuilder`) or password fields disappear, treat the CAPTCHA step as completed and return `"refresh"` immediately instead of stalling indefinitely when persistent layout widgets keep `captcha_visible` True.
  - **Note for the f188 author:** f188 reads the code length from the page ("8-character code"), which made `test_a_code_the_site_says_confirms_a_human_is_entered_when_no_captcha_is_showing` fail (its stub returned 6 digits for an 8-character page). Fixed the test's stand-in to `"EvPGU6Qy"` (commit after 00d4116); f188's behaviour is right. Run `tests/test_page_agent.py` with the owner's profile before committing changes to `complete_account_code`.
  - **Not done yet from the same audit (open for any agent):** job-reader items B1-B6 -- typed employment/location metadata kept, expired/template pages rejected before tailoring, ADP employer read as `Mascsr`, iCIMS `UNAVAILABLE` location, Oracle render readiness.

---

## 4. Shared Cross-Portal Backlog (Toward 100% Human-Free Execution)

Any agent (**Claude Code**, **Codex**, **Antigravity**) picking up work should check this list, coordinate with uncommitted changes, and mark items `[x]` as they land with tests:

### Priority 0 — Immediate Form & Option Accuracy (Workday + Universal)
- [x] **P0-1: Numerical Salary & Expected Earnings Recognition (`concept_matcher.py`, `reference/concepts.json`)** — *Done (`f187`)*
  - *Evidence*: Invesco Step 3 (`output/Invesco.../pages/20261017_183133/page_06.txt`) asked *"What would you expect to earn per year in the position for which you have applied? Please provide a numerical value.\*"*. `match_concept()` returned `None` and the AI typed a 45-word paragraph instead of `125000`.
  - *Fix*: Match `"expect to earn"`, `"annual earnings"`, `"expected pay"`, and `"base compensation"` to `DESIRED_SALARY`; when the question or field requests a `"numerical value"` / `"numbers only"` / numeric input, return plain `str(salary_min)` (`"125000"`) instead of a `$min - $max` range string or AI essay.
- [x] **P0-2: Unify All Option-Selection Paths in `page_agent.py` & `option_match.py`** — *Done (`f185`, `f187`)*
  - *Evidence*: `f184` added `option_match.sponsorship_choice()` to `PageAgent._pick()`, and `f187` unified `_control_for`, cached `native_select`, radio/checkbox groups in `do()`, and `_choose_inside` through `PageAgent._sponsorship_option()` / `_match_option()`. Optional dropdowns lacking the owner's answer are left blank (`f185`).
- [ ] **P0-3: Open Closed Dropdowns Before Concept Option Resolution**
  - *Evidence*: On Workday/ADP/React-Select, dropdown options are absent from the initial ARIA snapshot (`options=()`). When `resolve_profile_value()` runs before the menu opens, it returns a generic string (`"Yes"`, `"Master's"`) without seeing the actual options.

### Priority 1 — Portal Adapters (`Greenhouse`, `ADP`, `iCIMS`, `Avature`, `Sunday`)
- [x] **P1-1: Greenhouse Emailed 8-Character Security Code & Embedded `#grnhse_app` (`sites/greenhouse.py`, `page_agent.py`)** — *Done (`f188`)*
  - *Evidence*: `data/unanswered_questions.json` #2 (*"Greenhouse emailed an 8-character code to ... to confirm you are a human"*). `complete_account_code` previously stripped letters via `re.sub(r"\D", "", code)` when `expected > 0`, rejecting 8-character alphanumeric Greenhouse codes (`"EvPGU6Qy"`). Fixed in `f188` along with `?gh_jid=` and embedded frame support in `GreenhouseAdapter`.
- [x] **P1-2: Zero-Touch Coverage for Recurring Screening Questions (`reference/concepts.json`, `config.py`)** — *Done (`f188`)*
  - *Evidence*: Added `GOVERNMENT_ASSISTANCE` (`profile.received_government_assistance`) for WOTC/TANF/AFDC/SNAP questions, fixed `"Which country are you located in?"` matching `COUNTRY` instead of `RESIDES_IN`, and matched `"Where would you like to receive a verification code?"` to `profile.preferred_contact_method`.
- [ ] **P1-3: Dedicated ADP WorkforceNow Adapter (`sites/adp.py`)**
  - Consolidate ADP prefilled/read-only Email & Mobile skip (`f005`, `f006`), `sdf-select` floating listbox portals (`f007`), inline Experience/Education card Save buttons (`f008`), and non-semantic `"Application Submitted"` span detection (`f145`).
- [x] **P1-4: Dedicated iCIMS Adapter (`sites/icims.py`)** — *Done (`f189`)*
  - Automatic `#icims_content_iframe` targeting, 2-step Email-first $\rightarrow$ Password/Profile gate (`f064`, `f066`), and AJAX dependent State dropdown wait after Country selection.
- [ ] **P1-5: Dedicated Avature Adapter (`sites/avature.py`) & Sunday Aggregator Unwrapping**
  - Ignore optional `"Already registered? Login"` links when `"From Device"` resume upload is present (`f161`), format 10-digit plain phone numbers (`f165`), follow outbound ATS links on referral wrappers (`Sunday`), and mark closed postings as `job_not_found` (`f163`).

### Priority 2 — Diagnostics Clarity & Runtime Speed
- [x] **P2-1: Restore Non-PII Diagnostic Evidence (`diagnostics.py`)** — *Done (`f188`)*
  - Added `EDUCATION_SKIPPED` and `OPTIONAL_LEFT_BLANK` to `LOG_OPERATIONS` / `CONTENT_PRESERVING_OPERATIONS` and standard wizard step/button labels (`"Save and Continue"`, `"Application Questions"`, `"Voluntary Disclosures"`, `"My Information"`, `"My Experience"`) to `SAFE_LABELS`.
- [x] **P2-2: Fast SQLite Fallback When Local Postgres Is Stopped (`db.py`)** — *Done (`f188`)*
  - Probed `POSTGRES_HOST:POSTGRES_PORT` with a 200ms TCP check in `db.get_tracker()` before waiting on a multi-second driver timeout when Docker is not running.


