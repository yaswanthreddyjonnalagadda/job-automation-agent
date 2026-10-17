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
- **Other open PR**: [PR #35](https://github.com/yaswanthreddyjonnalagadda/job-automation-agent/pull/35) `chore/remove-oracle-worker` -- removes the Oracle remote worker (44 files). Both #34 and #35 append to the end of `BEHAVIOUR.md`; whichever merges second needs that one conflict resolved.
- **Live test job**: Invesco Workday (application 62, R-13974) -- got through account creation, email verification, My Experience (f183) and Application Questions (f184); f185 (optional Veteran Status) is the latest fix being verified on it.
- **Latest Failure Catalogue ID**: `f185` (`reference/failures/f185-optional-question-left-blank.json`) — **Next available ID: `f186`**
- **Recently Completed Fixes on Active Branch (`f170`–`f185`)**:
  - `f170`–`f180`: Workday posting Apply-link GET navigation, active-dialog account scoping, `"applied before"` $\rightarrow$ Sign In rule, Create Account error reading & consent re-verification, and `"Already applied"` terminal status.
  - `f181`–`f182`: Persistent agent browser Google sign-in support and `?authuser=<email>` mailbox targeting.
  - `f183`: Skip optional Workday Education section when tenant's School lookup returns empty (`"No Items."`).
  - `f184`: Sponsorship-qualified work authorization options (`"Yes, without restrictions"` vs `"Yes, with company sponsorship"`) follow `profile.requires_visa_sponsorship`.
  - `f185`: Optional questions whose dropdown lacks the owner's answer (e.g. Invesco's optional `Veteran Status`) are left blank with a note (`OPTIONAL_LEFT_BLANK`) instead of stopping the run.

---

## 4. Shared Cross-Portal Backlog (Toward 100% Human-Free Execution)

Any agent (**Claude Code**, **Codex**, **Antigravity**) picking up work should check this list, coordinate with uncommitted changes, and mark items `[x]` as they land with tests:

### Priority 0 — Immediate Form & Option Accuracy (Workday + Universal)
- [ ] **P0-1: Numerical Salary & Expected Earnings Recognition (`concept_matcher.py`, `reference/concepts.json`)**
  - *Evidence*: Invesco Step 3 (`output/Invesco.../pages/20261017_183133/page_06.txt`) asked *"What would you expect to earn per year in the position for which you have applied? Please provide a numerical value.\*"*. `match_concept()` returned `None` and the AI typed a 45-word paragraph instead of `125000`.
  - *Fix*: Match `"expect to earn"`, `"annual earnings"`, `"expected pay"`, and `"base compensation"` to `DESIRED_SALARY`; when the question or field requests a `"numerical value"` / `"numbers only"` / numeric input, return plain `str(salary_min)` (`"125000"`) instead of a `$min - $max` range string or AI essay.
- [ ] **P0-2: Unify All Option-Selection Paths in `page_agent.py` & `option_match.py`**
  - *Evidence*: `f184` added `option_match.sponsorship_choice()` to `PageAgent._pick()`, but radio button groups (`page_agent.py:4406, 4804`), `native_select` (`page_agent.py:4511`), and `_choose_inside` (`page_agent.py:5666`) still call `self.assistant._best_option()` directly. Also, when `veteran_status` is non-veteran (`"I am not a veteran"`) and a required Veteran dropdown only offers positive veteran categories plus `"Not Applicable"`, map non-veteran safely or leave optional blank.
- [ ] **P0-3: Open Closed Dropdowns Before Concept Option Resolution**
  - *Evidence*: On Workday/ADP/React-Select, dropdown options are absent from the initial ARIA snapshot (`options=()`). When `resolve_profile_value()` runs before the menu opens, it returns a generic string (`"Yes"`, `"Master's"`) without seeing the actual options.

### Priority 1 — Portal Adapters (`Greenhouse`, `ADP`, `iCIMS`, `Avature`, `Sunday`)
- [ ] **P1-1: Greenhouse Emailed 8-Character Security Code & Embedded `#grnhse_app` (`sites/greenhouse.py`, `page_agent.py`)**
  - *Evidence*: `data/unanswered_questions.json` #2 (*"Greenhouse emailed an 8-character code to ... to confirm you are a human"*). Per `CLAUDE.md` §5, this is a standard emailed code (`emailed_codes.py`), not a CAPTCHA. Auto-read the 8-character code from the signed-in Gmail tab and fill `#security-input` automatically. Also match embedded Greenhouse iframes (`#grnhse_app` / `gh_jid`).
- [ ] **P1-2: Zero-Touch Coverage for Recurring Screening Questions (`reference/concepts.json`, `config.py`)**
  - *Evidence*: 28 questions in `data/unanswered_questions.json` that can be resolved deterministically from profile facts:
    1. WOTC / Government assistance (`TANF`, `AFDC`, welfare checks $\rightarrow$ profile field `received_government_assistance`).
    2. Export control / "protected individual" status (derived from `us_citizen`, `work_authorization`, `requires_visa_sponsorship`).
    3. Education completion status (`"Graduated"` / `"Completed"` for completed degrees) & Country of Institution (derived via `geo_reference.py`).
    4. Multi-location checkboxes (match `profile.locations` or relocation preference).
- [ ] **P1-3: Dedicated ADP WorkforceNow Adapter (`sites/adp.py`)**
  - Consolidate ADP prefilled/read-only Email & Mobile skip (`f005`, `f006`), `sdf-select` floating listbox portals (`f007`), inline Experience/Education card Save buttons (`f008`), and non-semantic `"Application Submitted"` span detection (`f145`).
- [ ] **P1-4: Dedicated iCIMS Adapter (`sites/icims.py`)**
  - Automatic `#icims_content_iframe` targeting, 2-step Email-first $\rightarrow$ Password/Profile gate (`f064`, `f066`), and AJAX dependent State dropdown wait after Country selection.
- [ ] **P1-5: Dedicated Avature Adapter (`sites/avature.py`) & Sunday Aggregator Unwrapping**
  - Ignore optional `"Already registered? Login"` links when `"From Device"` resume upload is present (`f161`), format 10-digit plain phone numbers (`f165`), follow outbound ATS links on referral wrappers (`Sunday`), and mark closed postings as `job_not_found` (`f163`).

### Priority 2 — Diagnostics Clarity & Runtime Speed
- [ ] **P2-1: Restore Non-PII Diagnostic Evidence (`diagnostics.py`)**
  - Add `EDUCATION_SKIPPED` and `OPTIONAL_LEFT_BLANK` to `LOG_OPERATIONS` / `CONTENT_PRESERVING_OPERATIONS`.
  - Allow standard wizard step/button labels (`"Save and Continue"`, `"Application Questions"`, `"Voluntary Disclosures"`, `"My Information"`, `"My Experience"`) in `SAFE_LABELS` so `stopped_*.txt` and `dropdown_dump_structure.txt` remain useful for debugging without exposing personal data.
- [ ] **P2-2: Fast SQLite Fallback When Local Postgres Is Stopped (`tracking.py`)**
  - Probe `POSTGRES_HOST:POSTGRES_PORT` with a fast TCP check (e.g., 200ms) before waiting on a multi-second driver timeout when Docker is not running.

