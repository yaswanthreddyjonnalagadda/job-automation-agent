# UI/UX discovery audit — verified against current main

This corrects `docs/UI_UX_DISCOVERY_AUDIT.md` (an untracked, uncommitted document
found in the working tree, not part of any branch's history). That document was
produced against what appears to be a different, generic "job automation
platform" template: it cites eight production files --
`browser_agent.py`, `adp_portal.py`, `gmail_client.py`, `dom_actions.py`,
`auth_engine.py`, `submission_engine.py`, `settings.py`, `candidate_profile.py`,
plus `llm_client.py` -- **none of which exist in this repository**. Every
material finding below was re-derived by reading the actual, current code; no
claim from the prior document is repeated here without independent verification.

**Base SHA audited**: `221247a7baf54d31ba0925ce2be1d732b229b746`
(`origin/main`, "Merge pull request #10 from .../integrate/phase0-final",
2026-10-09 00:00:55 -0400). Working tree: clean except this document itself.

Note on scope: a large, separate, still-unmerged draft PR
(`fix/adp-apply-and-questions`, not part of `main` as audited) adds
`sites/adp.py` and a number of Workday/ADP fixes. This audit deliberately
covers only what is actually on `main` today, per the task's own instruction;
where that matters to a finding, it is called out explicitly rather than
silently mixed in.

## 1. File-existence verification

### The eight files the prior audit's "Missing-State Inventory" (section 3) cites as the runtime engine

| File the prior audit cites | Exists on main? |
| --- | --- |
| `browser_agent.py` | **No** |
| `adp_portal.py` | **No** (an `sites/adp.py` exists only on the unmerged `fix/adp-apply-and-questions` branch, not on `main`) |
| `gmail_client.py` | **No** |
| `dom_actions.py` | **No** |
| `auth_engine.py` | **No** |
| `submission_engine.py` | **No** |
| `settings.py` | **No** |
| `candidate_profile.py` | **No** |
| `llm_client.py` | **No** |

None of the nine files the prior audit's detailed findings rely on for their
"Evidence" and "Backend/state dependency" lines exist. Every finding sourced to
one of these files is corrected or reclassified below.

### Files the prior audit also names that DO exist

| File | Exists? | Real line count |
| --- | --- | --- |
| `ui_shell.py` | **Yes** | 312 |
| `web_progress.py` | **Yes** | 76 |
| `web_guard.py` | **Yes** (cited by the prior audit's UI-13, correctly) | 46 |

So the prior audit is not uniformly fictional -- it got the three smallest,
most peripheral UI-support files right, and invented every backend/runtime
engine file it relied on for its central claims. This matters: the audit's
*backend* evidence is unusable wherever cited, but its surface-level UI
observations (there is one HTML shell, one progress view, etc.) happened to
point at real files.

## 2. Complete production file inventory (name, line count)

Top-level production `.py` files on `main`, by line count:

| File | Lines |
| --- | --- |
| browser_automation.py | 7663 |
| page_agent.py | 6171 |
| apply_flow.py | 2014 |
| web_ui.py | 1554 |
| concept_matcher.py | 949 |
| job_tracker.py | 948 |
| safety.py | 927 |
| interaction.py | 825 |
| form_fields.py | 785 |
| db.py | 755 |
| job_sources.py | 734 |
| claude_integration.py | 662 |
| diagnostics.py | 605 |
| submission_guard.py | 540 |
| profile_setup.py | 493 |
| gemini_integration.py | 373 |
| config.py | 373 |
| account_state.py | 363 |
| open_answers.py | 322 |
| ui_shell.py | 312 |
| repeated_entries.py | 292 |
| web_setup.py | 290 |
| model_ladder.py | 264 |
| geo_reference.py | 260 |
| checkpoint.py | 230 |
| replay_guard.py | 226 |
| state_machine.py | 223 |
| answer_bank.py | 215 |
| apply.py | 206 |
| login_guard.py | 204 |
| emailed_codes.py | 191 |
| jd_analyzer.py | 182 |
| provenance.py | 176 |
| handoff.py | 172 |
| openai_integration.py | 170 |
| option_match.py | 156 |
| check_sites.py | 155 |
| recovery.py | 149 |
| session_planner.py | 135 |
| perception.py | 133 |
| ai_choice.py | 131 |
| resume_pdf.py | 126 |
| next_project_knowledge.py | 111 |
| ai_models.py | 110 |
| field_requirements.py | 109 |
| employment_history.py | 107 |
| action_result.py | 93 |
| progress.py | 92 |
| claude_answers.py | 80 |
| web_progress.py | 76 |
| visible_desktop.py | 76 |
| resume_parser.py | 72 |
| location_choice.py | 69 |
| tracking.py | 53 |
| web_guard.py | 46 |
| launch_dashboard.py | 42 |
| application_status.py | 17 |

`sites/` adapters:

| File | Lines |
| --- | --- |
| sites/workday.py | 1201 |
| sites/successfactors.py | 187 |
| sites/amazon.py | 139 |
| sites/base.py | 86 |
| sites/__init__.py | 43 |
| sites/greenhouse.py | 32 |
| sites/eightfold.py | 28 |
| sites/ashby.py | 28 |
| sites/lever.py | 21 |

`concepts.json` and a top-level `profile.json` do not exist; concept matching
is code-driven in `concept_matcher.py`, and the candidate's real profile lives
at `data/profile.json` (a per-installation data file, gitignored, not a
checked-in schema file). `sites/adp.py` does not exist on `main` (see scope
note above).

## 3. Phase-0 B1-B5 reconciliation (actual implementation vs. the prior audit's claims)

Phase 0 (five sequential hardening packages, each independently reviewed and
merged, then validated together as one system) is already on `main` as audited.
The UI architecture must treat these as the one authority system and never
duplicate or second-guess them. What follows is what actually exists, read
directly from the code at the base SHA above -- not what the prior audit
assumed.

### B1 -- submission authority

`submission_guard.py` defines `class SubmissionGuardV0` (`submission_guard.py:237`).
Its gateway, `submit_verified()` (`submission_guard.py:410`), is the **only**
path that may issue a final-submit click. Before it will click anything, it
calls `_valid_authorization()` (`submission_guard.py:375`), which does a strict
`type(decision) is not safety.AutoSubmitDecision` check (not `isinstance`) --
a bare boolean, a dict, or any duck-typed stand-in is rejected outright, not
merely distrusted.

`AutoSubmitDecision` (`safety.py:629`) is built exclusively by
`evaluate_auto_submit()` (`safety.py:731-802`), "the single place that decides
whether an application may be submitted" (its own docstring). Reading its body
directly: it is OFF by default (`AUTO_SUBMIT_VERIFIED_ONLY` must be explicitly
set `true`; `.env.example:23` documents the shipped default as `AUTO_SUBMIT=false`,
and `config.py:263` defaults the real setting to empty/false), and even when
enabled, eligibility requires **all** of: the job title/company/URL on the page
matching the tracked record exactly; every uploaded document verified
byte-for-byte against the stored original; zero blank-required/error/warning/
unanswered/ambiguous/unsupported/pending-attestation items and no visible
CAPTCHA; and **every** required field's on-page value exactly matching
pre-approved data via a deliberately strict comparison (`values_match`,
`safety.py:664` -- a value that merely resembles the approved one does not
count). Any single failure blocks submission with its own recorded reason
string (`safety.py:779-795`).

Durable dispatch state lives in `job_tracker.py`: `AUTHORIZED` → `DISPATCHED` →
`CONFIRMED`/`UNCERTAIN` (`job_tracker.py:34-55` for the event-kind-to-state
map; `job_tracker.py:319`, `569-589`, `596-607` for the transition guards).
`begin_submission_dispatch` raises if the state is already
`DISPATCHED`/`CONFIRMED`/`UNCERTAIN` (replay blocked); a run that resumes after
a crash re-enters this same gateway and is caught by the same check, not by
any UI-level bookkeeping.

**The prior audit's UI-04 ("Accidental Submissions & Missing Pre-Submit Gate",
P0) is FALSE.** It claims `settings.py` has "a simple boolean `auto_submit:
True/False`" and that the agent "clicks 'Submit Application' without a
mandatory human review gate." `settings.py` does not exist. The real gate is
the multi-layer, default-off, exact-match mechanism above, and separately,
`apply_flow.hand_over()`'s own default path (used throughout the existing test
suite, e.g. `tests/test_page_agent.py`'s `handed_over()` helper) stops at the
last step for the owner to click Submit themselves -- the agent does not press
it. See the corrected finding, UI-04-R, below.

A job's coarse lifecycle status is tracked separately and is **not** invented
by this audit: `job_tracker.py:203-212` already defines
`STATUS_PREPARED`, `STATUS_FORM_FILLED`, `STATUS_READY_TO_SUBMIT`,
`STATUS_NEEDS_USER_REVIEW`, `STATUS_SUBMITTED`, `STATUS_SKIPPED`,
`STATUS_DISQUALIFIED_POLICY_MISMATCH`, `STATUS_BLOCKED_VALIDATION_LOOP`. A
`ready_to_submit` state already exists in the running system; it is not a
feature the UI needs invented from nothing, only (per the findings below)
rendered with more detail than a plain status word.

### B2 -- authentication / verification

`account_state.py:24-36` classifies the account step into 13 values:
`SIGNED_IN, LOCKED, WRONG_PASSWORD, ACCOUNT_EXISTS, CODE_ENTRY, MFA_REQUIRED,
VERIFY_EMAIL, CREATE_FORM, SIGN_IN_FORM, EMAIL_FIRST, CHOOSER, LOADING, NONE`.
`login_guard.py` is durable (`data/_login_attempts.json`, atomic
write-then-replace, `login_guard.py:57-65`) and keyed per `host|email` with a
rolling 24-hour window, surviving a restart correctly -- `MAX_FAILED_SIGN_INS`,
`MAX_ACCOUNT_CREATIONS`, `MAX_RESET_REQUESTS`, `MAX_CODE_READS` are all
enforced against this on-disk history, never an in-process counter.
`emailed_codes.why_not()` (`emailed_codes.py:140-154`) gates whether a code
may be read, in order: the owner's `check_gmail_for_confirmation` setting,
`safety.password_allowed()`, no visible CAPTCHA, and `login_guard.may_read_code()`'s
throttle. None of this is re-derived or redesigned here; it is pre-existing,
separately-merged authority. Full detail and exact line citations for how
these feed into the UI are in section 4, UI-02 and UI-03.

### B3 -- recovery

`checkpoint.py:37` defines `ApplicationCheckpoint`; `checkpoint.py:186`'s
`STICKY_FIELDS = ("pending_action", "uncertain_actions")` protects those two
fields from being silently blanked by an unrelated generic checkpoint write
(`merge_update`, `checkpoint.py:189`, only clears a field when the caller names
it explicitly). `recovery.py:89`'s `reconcile()` is a pure function (no browser,
no side effects) comparing a stored checkpoint against live evidence the
caller supplies; its outcomes (`recovery.py:25-34`: `MATCH`, `AHEAD`, `BEHIND`,
`AUTH_REQUIRED`, `SUBMITTED`, `NO_CHECKPOINT`, and others) are currently
produced for a single diagnostic log line and tracker note
(`apply_flow.py` -- see `log_recovery_reconciliation`) rather than used to gate
any action; the actual "never repeat a dispatched submission" guarantee comes
from B1's own durable state machine, independent of this reconciliation
output. Any UI built on top of `reconcile()`'s output must represent it as
informational, not as a second authority.

### B4 -- action verification

`action_result.py:25-31` defines the outcome vocabulary: `NOT_ATTEMPTED`,
`VERIFIED`, `REFUSED`, `VALIDATION_FAILED`, `NO_CHANGE`, `OUTCOME_UNKNOWN`,
`OWNER_REQUIRED`. Its own module docstring is explicit that this "is not a
second policy authority": it has no authority over submission, account/sign-in
decisions, or checkpoint trust. These outcomes are consumed for observability
and to populate the checkpoint's `uncertain_actions` field -- never to
authorize a consequential action by themselves.

### B5 -- privacy / diagnostics

`diagnostics.py` (605 lines; `CAPTURE_VERSION = 5` at `diagnostics.py:24`) is a
storage-only privacy layer for diagnostic/forensic captures: structural DOM
projection, screenshot masking, bounded/sanitized console and event logs, and
`retention_days()` (`diagnostics.py:424`, default 7, accepted range 0-30). Its
`PLACEHOLDER = '<redacted-value>'` (`diagnostics.py:31`) marks text this layer
withheld. Critically for any UI surfacing raw page/application content: this
sanitization applies to diagnostic/forensic artifacts only (`runs/`,
`evidence_*/`, `logs/`) -- the real, per-run page recordings under
`output/<job>/pages/` that a debugging or audit-trail UI would actually want to
read are deliberately raw (secrets are hidden once, upstream, at the point a
page is read, not re-redacted per writer). A UI feature that wants to show
"what the agent actually saw" should read from the real page recordings, not
from diagnostic/forensic exports, which are intentionally lossy by design and
will not serve that purpose.

## 4. Verification of findings UI-01 through UI-15, and which claims are false/stale

Status legend: **VERIFIED** (confirmed as described), **PARTIALLY VERIFIED**
(core issue real, details wrong), **FALSE/STALE** (not true in current main),
**FUTURE FEATURE** (not a current defect). Findings appear in the order they
were completed (UI-04 first, since its evidence -- the B1 submission gate --
was verified directly rather than via the delegated research passes), not in
UI-01..UI-15 numeric order; use search for a specific ID. Summary of every
status (two entries, UI-04 and UI-14, are false claims the prior audit got
backwards -- a stricter-than-claimed safety gate, and an already-working
responsive layout):

| ID | Status | Corrected severity |
| --- | --- | --- |
| UI-01 | PARTIALLY VERIFIED | P1 |
| UI-02 | PARTIALLY VERIFIED | P1 |
| UI-03 | VERIFIED (mechanism corrected) | P1 |
| UI-04 | **FALSE** | P1 (reclassified: transparency, not safety) |
| UI-05 | mostly FALSE | P3 |
| UI-06 | PARTIALLY VERIFIED / mostly FALSE | P2 |
| UI-07 | mostly FALSE | P3 |
| UI-08 | PARTIALLY VERIFIED | P2 |
| UI-09 | PARTIALLY VERIFIED | P2 |
| UI-10 | PARTIALLY VERIFIED | P2 |
| UI-11 | VERIFIED | P2 (as originally scoped) |
| UI-12 | mostly FALSE | P3 |
| UI-13 | PARTIALLY VERIFIED | P2 |
| UI-14 | **FALSE** -- closed, not a defect | -- |
| UI-15 | PARTIALLY VERIFIED | P3 |

### UI-04: Accidental Submissions & Missing Pre-Submit Gate

**Status: FALSE.** Full evidence in section 3 (B1) above. The prior audit's
central claim -- that autonomous submission "lacks an explicit, deterministic
approval checkpoint" and is gated only by a boolean in a nonexistent
`settings.py` -- is the opposite of what the code does. `evaluate_auto_submit()`
(`safety.py:731`) is a strict, multi-condition, default-off, exact-match gate;
`SubmissionGuardV0._valid_authorization()` (`submission_guard.py:375`) then
independently rejects anything that is not a real `AutoSubmitDecision` object;
and durable dispatch state (`job_tracker.py`) blocks a second click outright,
surviving a process restart. What the prior audit got right: the *idea* that a
submission-review surface is valuable is reasonable on its own terms, but not
because today's gate is weak -- it is because today's gate, being this
strict, has no UI representation at all. A candidate who hits
`STATUS_READY_TO_SUBMIT` sees only a status word, not *why* the agent is
confident every field is correct, which field-by-field comparisons it made, or
what evidence it is standing on. That is a real, corrected finding:

**UI-04-R: The already-strict submission gate has no UI representation of its own reasoning.**
- Severity: **P1** (not P0 -- there is no accidental-submission risk to fix;
  the risk is user *trust and transparency* in an already-safe system).
- Backend dependency: `safety.AutoSubmitDecision.as_dict()` (`safety.py:638`)
  already serializes `eligible`, `reasons`, `field_comparisons`,
  `evidence_paths` -- this data exists and is computed, it is simply not
  surfaced anywhere in `web_ui.py`.
- Corrected acceptance criteria: when a tracked application's status is
  `ready_to_submit`, the application detail view renders the actual
  `AutoSubmitDecision` the agent last computed (or, if verified auto-submit is
  off, states plainly that the owner's own click is what will submit it) --
  not an invented "Authorize Submission" button that would add a second gate
  on top of one that already exists.

### UI-01: Live Agent Black Box & Stall/Loop Blindness

**Status: PARTIALLY VERIFIED.** The core problem is real -- there is no
fine-grained, per-field live telemetry -- but the prior audit's description of
the mechanism is wrong on every detail.

Real evidence: `_RUNS: dict[str, dict]` (`web_ui.py:70-71`), persisted to
`data/_runs.json`; each run's actual persisted fields are `state`, `log`, `pid`,
`started` (`_save_runs`, `web_ui.py:75-84`). `_current_runs()`
(`web_ui.py:703-734`) enriches this per request with `last_page_url`,
`company`, `title`, and the most recent screenshot path. This is **not** a
pure binary flag as claimed -- it is a small, real structured state -- but it
genuinely has no step index, no current-field indicator, no retry/stall
counter, and no distinction between "thinking" and "stuck."

The claimed real-time mechanism (SSE) does not exist and nothing resembling it
exists anywhere in the project (grepped for `text/event-stream`, `EventSource`,
`socketio`, `WebSocket` across every `.py` file: zero matches). What the
browser actually does: a `setInterval` that calls `location.reload()` -- a
full page navigation, not `fetch`-based polling -- every 5 seconds, only while
a run is live, suppressed while the user is typing or has a menu open
(`web_ui.py:1146-1155`).

Corrected finding, **UI-01-R**: severity **P1** (not P0 -- a 5-second full
reload is crude and will feel laggy/jarring, especially on the detail page
which has no auto-refresh at all, but it is not silent; the state label does
update). Backend dependency: new instrumentation would be needed for
step/field-level detail -- today's `_RUNS` dict and the tracker's `events()`
table are the only real telemetry source, and neither currently records
"which field is being worked on right now." Corrected acceptance criteria
should target replacing the 5-second full-page-reload with `fetch`-based
partial updates of the run card at minimum, before any SSE investment (see
section 9, which recommends SSE only as a later step).

### UI-02: Silent Handoff for CAPTCHA & Multi-Factor Auth

**Status: PARTIALLY VERIFIED.** The absence-of-proactive-alert problem is
real: there is genuinely no modal, audio chime, or push notification anywhere
in the five UI files (confirmed by full reads). A waiting run is represented
the same way as any other state -- a plain pill with the raw state string.
But the prior audit significantly undersells what already exists, and its
cited evidence file (`_waiting_human.txt`) does not match reality.

The real handoff mechanism is far more structured than claimed. `handoff.py`
defines 14 real categories (lines 20-33, verbatim):
`CAPTCHA, SMS_MFA, AUTHENTICATOR_MFA, SECURITY_KEY, PUSH_APPROVAL,
ACCOUNT_LOCKED, ACCOUNT_CREATION_UNCERTAIN, FIELD_REQUIRED,
UNSUPPORTED_CONTROL, VALIDATION_BLOCKER, ACTION_OUTCOME_UNKNOWN,
APPLICATION_RECOVERY, OWNER_REVIEW, OTHER`. A built `Handoff` is persisted
durably -- `to_checkpoint_fields()` (`handoff.py:109-120`) is merged into the
checkpoint and written to the SQLite `application_checkpoints` table
(`job_tracker.py:149-158`; checkpoint durability is **always SQLite, even
when Postgres is configured**, by explicit design -- `db.py`'s own comment:
"durable checkpoint state always lives in one local SQLite file, never in
Postgres"). It is already read back and rendered on the application detail
page (`web_ui.py:762-780`): category, reason, required action, resume
condition. This is not transient/log-only as the prior audit claims.

What genuinely differs between a CAPTCHA case, an MFA case, and an
unanswered-question case is not the message template (always
`"<employer>/<portal>: <reason>[ -- <required_action>]"`,
`handoff.py:96-107`) -- it is which upstream free-text reason string gets
produced and which regex in `classify()` (`handoff.py:123-152`) matches it.
`classify()`'s own docstring states this plainly: "a wrong category only
affects a label shown to the owner, never any behavior." The categorization
is a best-effort label on text that already existed, not a structured event
type from the source.

Corrected finding, **UI-02-R**: severity **P1** (not P0 -- the state is
durable and already surfaced on a page the owner can check; the real gap is
that nothing *pushes* that information to the owner proactively). Backend
dependency: none new needed for the data itself; only a notification
mechanism (e.g. a desktop notification or sound triggered when a checkpoint's
`handoff_category` changes to one of the four human-required categories)
would need to be added.

### UI-03: Opaque Background Email / Gmail Verification

**Status: VERIFIED** for the core problem, with a corrected mechanism. There
is genuinely no durable "waiting for email" indicator. `account_state.py`
defines 13 classifier values (lines 24-36):
`SIGNED_IN, LOCKED, WRONG_PASSWORD, ACCOUNT_EXISTS, CODE_ENTRY, MFA_REQUIRED,
VERIFY_EMAIL, CREATE_FORM, SIGN_IN_FORM, EMAIL_FIRST, CHOOSER, LOADING, NONE`
-- `CODE_ENTRY` is a one-shot "a code box is on the page right now"
classification, not a stateful "waiting" indicator with duration. No
`WAITING_FOR_EMAIL` value or synonym exists anywhere (confirmed by a
case-insensitive search of the whole repository).

Tracing the actual code path (`PageAgent.complete_account_code()`,
`page_agent.py:2163-2253`): when `emailed_codes.why_not()` blocks a read, or
when the common case of "the code simply hasn't arrived in the inbox yet"
occurs, the result in most sub-cases is only a `logger.info()` call -- no
tracker status changes, no checkpoint field is set, no durable record
distinguishes this from any other quiet pass through the loop. The one
genuinely durable artifact is a screenshot/text snapshot written to
`job_dir/account/{timestamp}_{state}.{png,txt}` on every account-state
*change* (`page_agent.py:1822-1841`) -- durable on disk, but not currently
read by anything in `web_ui.py` (confirmed: no route reads the `account/`
folder). If the code box eventually degrades into a generic blocked/blank
outcome, it shares the same `needs_user_review` status as every other
stoppage reason, which is exactly the "looks identical to any other wait"
problem the prior audit describes -- just reached by a different mechanism
(silent loop exhaustion, not visible background polling with a panic-inducing
new tab, a specific claim this pass could not independently confirm or deny).
Separately, `CLAUDE.md` documents that reading a code is from "the owner's
signed-in Gmail tab" -- consistent with *some* browser-tab-based reading
existing, though this pass did not re-verify whether a new tab is opened or
an existing one is read from.

Corrected finding, **UI-03-R**: severity **P1** (not P0). The fix is not "add
a status the schema lacks" -- it's adding one: no durable signal exists for
"the agent is actively waiting on an email code" at all today, at any
granularity.

### UI-05: Raw Paths & Python Stack Traces Leaked in UI

**Status: mostly FALSE**, with one real, much narrower exception. The prior
audit's central claim -- that raw internal exception strings and absolute
filesystem paths are routinely printed into the dashboard -- is the opposite
of the actual design. Reading the real code: `diagnostics.install_log_privacy()`
is installed before anything else in `web_ui.py` (`web_ui.py:33`) and rewrites
Python's own logging record factory to omit non-numeric interpolated values
and exception bodies app-wide. Subprocess output is piped through
`diagnostics.PrivateRunLog`, which replaces every captured line with a fixed
operation tag (`diagnostics.py:551-555`: `f'{operation}: private details
omitted'`). Tracker event messages are forced to `"event: " + kind` at both
write and read time (`db.py:209`, `job_tracker.py:299`,
`diagnostics.sanitize_event()` at `diagnostics.py:596-605`) -- the prior
audit's cited evidence ("`web_ui.py:145` outputs raw SQLite exception strings
... to HTML") does not match anything in the real code; there is no such
unguarded `notes` rendering path.

The one place a real, if heavily bounded, exception fragment can reach the UI:
`browser_automation.py:7239` truncates a validation-scraper exception to its
first line and 120 characters before appending it to `errors_shown`, which
flows into `validation.json` and renders as `<li>Error: {{ e }}</li>` in the
application detail page (`web_ui.py:1409`). Separately, local `.env`
settings-save errors (not application-run errors) do render `str(exc)`
verbatim (`web_ui.py:365,459`) -- a real but low-sensitivity local
configuration-error path, not leaked application-run internals.

Corrected finding, **UI-05-R**: severity **P3** (not P0). Bound the one
validation-exception fragment to a safer, pre-classified message rather than
any raw substring of `str(exc)`, for full consistency with the rest of the
already-thorough sanitization design.

### UI-06: Zero Answer Transparency & Confidence Traceability

**Status: PARTIALLY VERIFIED / mostly FALSE on the literal claim.** A real,
structured, durable, queryable, per-field answer table already exists and is
already rendered on the dashboard: `form_answers`
(`job_tracker.py:169-180`/`db.py:106-120`) -- `UNIQUE(application_id,
question)`, columns `host, question, answer, options, answered_by,
created_at`. `answers_for(application_id)` is surfaced on the detail page as
"Answers given" (confirmed route-level by the UI pass: `web_ui.py:754`,
rendered ~`web_ui.py:1496-1506`). Real `answered_by` values in use:
`"user"`, `"claude"`, `"verified_agent"`, default `"agent"` -- so source
attribution genuinely exists, just as a coarse 4-value string rather than the
richer `Profile`/`Rule`/`AI Generated`/`User Override` taxonomy the prior
audit describes.

What is genuinely missing, confirmed absent from the schema entirely: **no
`confidence` field anywhere** (grepped `job_tracker.py`, `db.py`,
`page_agent.py`, `safety.py` -- zero hits for a stored confidence value tied
to an answer), and **no durable correction history**. `PageAgent.corrected`
(`page_agent.py:1347`) is an in-memory, per-process Python `set`, reset on
every restart (`_ensure_state()`, `page_agent.py:1424-1438`); the only record
a correction ever happened is a `logger.info("CORRECTED: ...")` line
(`page_agent.py:4028-4030` et al.) -- genuinely log-line-shaped, not
queryable structured data, and discarded entirely once the process logs
rotate out. `form_answers` itself has no versioning: a later answer simply
overwrites the row (`ON CONFLICT ... DO UPDATE SET answer = excluded.answer`,
`job_tracker.py:879-881`) with no record of the prior value.

Corrected finding, **UI-06-R**: severity **P2** (not P1), scoped precisely to
"add a `confidence` column and a `prior_answer`/correction-history record to
the already-working `form_answers` table and surface them next to the
existing, already-rendered answer ledger" -- not "build an answer ledger from
nothing."

### UI-07: Rigid Tech-Centric Schema Excluding Non-Tech Candidates

**Status: mostly FALSE.** The real `UserProfile` (`config.py:42-168`) has 85
fields, independently cross-checked against the owner's actual `data/profile.json`
to confirm these are live, populated fields, not dead schema. It is not a
narrow tech-only schema: it already includes general U.S. employment-screening
fields used across every industry --
`felony_conviction`, `willing_drug_test_and_physical`,
`willing_to_submit_to_pre_employment_background_check`, `bound_by_non_compete`,
`veteran_status`, `disability_status`, voluntary EEO fields
(`ethnicity`, `gender`, `gender_identity`, `sexual_orientation`, `transgender`,
`pronouns`), `security_clearance`/`security_clearance_level`,
`relatives_employed_here`, `previously_employed_here`, `people_managed`,
`gpa` -- plus a genuinely generic, domain-unrestricted `certifications:
tuple[str, ...]` field and a free-form `skill_levels` list. These are not
software-engineering-specific.

What the prior audit got right, narrowly: there is **no dedicated `github_url`
field** (only a generic `portfolio_url` plus `linkedin_url`), and **no
dedicated shift-availability or license-with-expiration-date structure**
(`certifications` is a flat string list with no issuing authority/number/
expiration sub-fields; schedule-adjacent fields exist --
`availability_to_start`, `work_arrangements`, `willing_to_work_weekends`,
`willing_to_work_onsite_three_days` -- but no explicit day/night/rotating
shift-preference field). The networking/CCNA-flavored example text the prior
audit may have been reacting to lives only in `profile_setup.py`'s UI hint
copy (e.g. "Comma separated, e.g. CCNA, CCNP"), not in any schema
restriction -- it reflects the current real owner's own career field, not a
platform limitation.

Corrected finding, **UI-07-R**: severity **P3** (not P1), scoped to "add a
structured sub-object for a license/certification (issuing authority, number,
expiration) instead of a flat string, and consider a dedicated GitHub field
and a shift-preference matrix" -- not a schema redesign, since the schema is
already general-purpose.

### UI-08: Queue Scalability Failure on High Volume

**Status: PARTIALLY VERIFIED.** The underlying concern is real but shaped
differently than described. `list_all()` has no SQL `LIMIT`/`OFFSET` in
either backend (`db.py:489-498`, `job_tracker.py:791-797`) -- every tracked
application is fetched from the database and built into a Python object on
every load of `/`, regardless of how many will be shown. However, rendering
itself is already bounded: `apps[:show]` with `PAGE_SIZE = 10` and
`MAX_SHOWN = 300` (`web_ui.py:616-617,643,648`) caps the DOM at 300 rows no
matter the database size -- so the prior audit's specific claim of "4,000+ DOM
nodes" causing client-side stutter does not match the real code (that
specific failure mode is already prevented). The real, verified cost is a
full, unbounded table materialization in the Python process on every page
load as the application count grows, not an unbounded client-side render.

Corrected finding, **UI-08-R**: severity **P2** (not P1) given the existing
300-row render cap already protects the browser; the real fix is a
database-level `LIMIT`/`OFFSET` (or an indexed count-only query for anything
beyond what's shown) to stop the full fetch-and-build cost scaling with total
history size.

### UI-09: Lack of Step-by-Step, Granular Pause, and Safe Abort

**Status: PARTIALLY VERIFIED.** There is genuinely no mid-action "pause"
control -- confirmed absent across all five files. But the prior audit
undersells what already exists: Stop (`/stop`, hard `taskkill /T /F` on
Windows or `SIGTERM` to the process group on POSIX, `web_ui.py:237-245,
265-290`), Restart/"Reload" (`/restart`, stops then relaunches from the
original posting, `web_ui.py:248-262`), Resume (`/resume/<id>`, relaunches
with `--open-url` pointed at the exact page the run last reached, reusing
already-stored answers, `web_ui.py:463-489`), and the `/signal` mechanism
(continue/skip/close responses to a waiting run, explicitly never submit)
together already form a more graduated set of controls than "only a blunt Stop
button," even though none of them is a true mid-field pause.

Corrected finding, **UI-09-R**: severity **P2** (not P1). The real gap is
narrower than described: an in-flight DOM action cannot be paused before it
completes, and Stop is a hard kill rather than a graceful one. Resume/Restart
already provide most of the "don't lose your place" value the original
finding asked for.

### UI-10: Missing Resume & Document Version Attribution

**Status: PARTIALLY VERIFIED.** The single-global-source-resume claim is
correct: `/setup` overwrites `data/resume{suffix}` and a single `RESUME_PATH`
`.env` key on every upload (`web_setup.py:70-73`); there is no multi-resume
upload/management UI anywhere in these files. However, the claim that
"the `applications` table does not store a hash or filename of the uploaded
document" is wrong: documents are stored as DB blobs
(`get_tracker().document(doc_id)`, streamed by `/document/<int:doc_id>`,
`web_ui.py:845-854`), and the application detail page already shows, per
document, filename, kind, an "In use" vs. "Earlier attempt" pill, size, and
timestamp (`tracker.documents_for`, rendered `web_ui.py:1436-1452`) --
i.e. per-application document version attribution already exists for the
*generated, tailored* documents. What's actually missing is upstream of that:
no way to manage multiple *candidate source* resumes to tailor from.

Corrected finding, **UI-10-R**: severity **P2** (not P1), scoped specifically
to "allow more than one source resume to be uploaded and selected from,"
since per-application version attribution of the generated documents is
already implemented.

### UI-11: Disconnected Setup Wizard Without System Health Checks

**Status: VERIFIED.** Confirmed by a full read of `web_setup.py` (290 lines):
zero validation of Playwright/Chromium binaries, API key validity, or Gmail
authentication exists anywhere in the file. The closest thing to a check is
`_drafting_ai()` (`web_setup.py:34-52`), which silently falls back to no
AI-assisted drafting if no configured provider's client constructs
successfully -- no error or warning is ever shown for this. Resume-file
validation (extension, size, parseability) does exist (`web_setup.py:65-78`)
but that is input validation, not environment/dependency health checking.

### UI-13: Missing Local Device, License, and Session Boundaries

**Status: PARTIALLY VERIFIED.** No PIN, password, session token, or login
system of any kind exists (confirmed: no `session[...]`, no cookie-based auth,
no `/login` route in any of the five files). But `web_guard.py` is correctly
described by the prior audit as doing "basic origin checks" -- reading it in
full (46 lines) confirms it is working exactly as designed for a different
purpose than user authentication: loopback-only host-header checking,
Origin/Referer matching, and a per-process CSRF token
(`secrets.token_urlsafe(32)`, generated fresh on every dashboard start, never
tied to a user identity). Its own docstring states its actual scope plainly:
defending against "a website they happen to visit," not against another local
user or process on the same machine. The real gap -- no local-user
authentication boundary at all -- is correctly identified by the prior audit,
just not by pointing at a deficiency in `web_guard.py` itself, which does its
actual job correctly.

### UI-14: Mobile Viewport Clipping & Touch Target Failures

**Status: FALSE.** A real, working responsive breakpoint already exists:
`@media (max-width:760px)` in `ui_shell.py:224-238` converts the applications
table into stacked cards (hiding the header row, stacking cells to full
width). The prior audit's specific evidence citation -- "hardcoded
`min-width: 900px`" and "missing media queries" -- does not match the real
CSS; no such fixed width exists, and the media query the audit claims is
absent is present and functional. `ui_shell.py` additionally has a
`prefers-color-scheme: dark` block and a `prefers-reduced-motion: reduce`
block, both absent from the prior audit's description of "a desktop-only
stylesheet." This finding should be closed, not implemented.

### UI-15: WCAG 2.1 AA Violations

**Status: PARTIALLY VERIFIED.** The specific color citation is wrong --
`--muted` is `#667085` in light mode and `#94969c` in dark mode
(`ui_shell.py:19,29`), not the `#64748b` the prior audit cites (that exact
hex does not appear anywhere in `ui_shell.py`) -- so the "3.8:1, fails 4.5:1"
contrast claim cannot be verified against real code and may be entirely
fabricated. What IS confirmed true: no `aria-live` region and no
`role="status"` exist anywhere across all five UI files (grepped, zero
matches); the 5-second auto-reload (UI-01) means a screen-reader user gets no
non-disruptive announcement of a run-state change at all, only a full page
reload. `role="alert"` does exist, in `web_ui.py:1133` and
`web_setup.py:154,195` (not `ui_shell.py` as the prior audit's citation
implies).

Corrected finding, **UI-15-R**: severity **P3**, scoped specifically to
adding `aria-live="polite"` to the run-status region and verifying actual
rendered contrast values against the real `--muted` tokens above, rather than
the prior audit's unverifiable specific numbers.

### UI-12: Static Funnel Metrics Without Failure Categorization

**Status: mostly FALSE.** Reading `web_progress.py` and `progress.py` (both
short, read in full) directly contradicts the central claim. `progress.funnel()`
(`progress.py:60-92`) already computes stage-to-stage conversion **rates**
(`Funnel.rate()`, `progress.py:56-57`, rendered as e.g. "62% of started" in
`web_progress.py:42-44`), a genuine **by-site breakdown** (started/reached-review/
submitted per job site, `progress.py:64-80,90`, rendered `web_progress.py:54-61`),
and a real **failure-categorization taxonomy**: `stop_reasons`
(`progress.py:85-91`) extracts the first segment of each stuck application's
notes, generalizes it by replacing quoted values and numbers with placeholders
so similar reasons group together (`_reason()`, `progress.py:37-41`), and shows
the top 8 most common reasons with counts (`web_progress.py:65-71`). Outcome
tracking beyond submission (`interviewing`, `rejected`, `offer`) is also
already wired into the funnel's status sets (`progress.py:14-18`) -- these are
set by the owner via the application detail page's status control, not
auto-detected, but the data model and funnel computation already account for
them.

What genuinely is missing: the funnel's stat cards are not clickable -- there
is no drill-down from "62% reached review" to a filtered view of exactly those
applications (confirmed: no `href`/`onclick` on any `.stat` element in
`web_progress.py`'s template).

Corrected finding, **UI-12-R**: severity **P3** (not P2), scoped to "funnel
stages are not clickable drill-down filters," since the rate/breakdown/
failure-taxonomy functionality the original finding asked for already exists
and works.

## 4b. Complete route inventory (web_ui.py and blueprints)

| Path | Method(s) | Function | File:Line | What it does |
| --- | --- | --- | --- | --- |
| `/runtime` | GET | `runtime_info` | web_ui.py:58-61 | Returns directory/source_id/interpreter so a stale dashboard process can be detected. |
| `/apply` | POST | `start_apply` | web_ui.py:220-234 | Starts a new application run (refuses if one is already live). |
| `/stop` | POST | `stop_apply` | web_ui.py:237-245 | Kills a running process tree by posting URL; tracked status left unchanged. |
| `/restart` | POST | `restart_apply` | web_ui.py:248-262 | Stops then relaunches the currently-running URL fresh from the posting. |
| `/settings` | GET, POST | `settings` | web_ui.py:345-381 | Views/saves API keys, ATS credentials in `.env`. |
| `/settings/ai` | POST | `settings_ai` | web_ui.py:427-460 | Saves answer-mode and per-provider/tier model ordering. |
| `/resume/<int:app_id>` | POST | `resume_application` | web_ui.py:463-489 | Restarts a specific application, reopening its last-known page. |
| `/stop-application/<int:app_id>` | POST | `stop_application` | web_ui.py:492-513 | Stops a run keyed by application id. |
| `/application/<int:app_id>/status` | POST | `change_application_status` | web_ui.py:516-543 | Lets the owner set status directly from a fixed whitelist. |
| `/delete/<int:app_id>` | POST | `delete_application` | web_ui.py:546-568 | Deletes an application and its filed materials. |
| `/reload-agent` | POST | `reload_agent` | web_ui.py:571-583 | Signals every waiting run to reload code without closing its browser. |
| `/signal/<path:signal_file>` | POST | `send_signal` | web_ui.py:586-599 | Writes a review decision (never `submit`) for a waiting run to read. |
| `/` | GET | `index` | web_ui.py:620-652 | Main applications dashboard. |
| `/application/<int:app_id>` | GET | `application` | web_ui.py:737-780 | Per-application detail/review page. |
| `/evidence` | GET | `evidence` | web_ui.py:828-841 | Serves a path-confined, content-verified diagnostic artifact. |
| `/document/<int:doc_id>` | GET | `document` | web_ui.py:845-854 | Streams a stored document's bytes from the database. |
| `/log` | GET | `log` | web_ui.py:857-870 | Serves a path-confined, content-verified run log. |
| `/setup` | GET, POST | `setup` | web_setup.py:55-84 | First-run resume upload and profile drafting. |
| `/profile` | GET, POST | `profile` | web_setup.py:103-115 | View/save the candidate profile. |
| `/answers` | GET, POST | `answers` | web_setup.py:118-134 | View/manage saved Q&A overrides. |
| `/progress` | GET | `progress_page` | web_progress.py:12-26 | Renders the conversion funnel. |

This is exhaustive for the five UI files (verified by grepping every
route-decorator pattern across the whole file set). No other routes exist.

## 5. Runtime → UI state map

Every state/event below is real, with its backend source. "Durable" means it
survives a process restart; "transient" means it exists only in memory for
the current process.

| State/event | Source | Meaning | Durable? | Safe for UI display? | User action required? | Transition trigger |
| --- | --- | --- | --- | --- | --- | --- |
| `prepared` | `job_tracker.py:203` | Materials generated, not yet filling | durable (DB) | yes | no | Materials prep completes |
| `form_filled` | `job_tracker.py:204` | Being filled in | durable (DB) | yes | no | Agent reaches this stage |
| `ready_to_submit` | `job_tracker.py:205` | Filled + validated; human clicks Submit | durable (DB) | yes | **yes** | All required fields verified, no blockers |
| `needs_user_review` | `job_tracker.py:206` | Something needs a person -- blanks, errors, CAPTCHA, uncertain outcome, **or** a generic runtime crash | durable (DB) | yes | **yes** | Any of many distinct conditions (see "collapsed states" below) |
| `submitted` | `job_tracker.py:209` | Confirmed by the site or a confirmation email | durable (DB) | yes | no | B1 gateway confirms |
| `skipped` | `job_tracker.py:210` | Owner or sponsorship-policy skip | durable (DB) | yes | no | Owner action or policy match |
| `interviewing` / `rejected` / `offer` | `web_ui.py:520-521` (status whitelist) | Post-submission outcome | durable (DB) | yes | no (owner-set) | Owner sets manually via the status control |
| `DISQUALIFIED_POLICY_MISMATCH` | `job_tracker.py:211` | Sponsorship-policy auto-skip | durable (DB) | yes | no | Sponsorship-shield match |
| `BLOCKED_VALIDATION_LOOP` | `job_tracker.py:212` | Circuit breaker tripped | durable (DB) | yes | no | 3 identical consecutive state fingerprints |
| `AUTHORIZED`/`DISPATCHED`/`CONFIRMED`/`UNCERTAIN` | `job_tracker.py:34-55`, `submission_guard.py` | Durable submission-dispatch effect state | durable (DB) | **not currently shown** | no | B1 gateway transitions |
| `account_state.*` (13 values, `account_state.py:24-36`) | account classifier | What the current page's account step shows | transient per read; one value written to disk on *change* (`page_agent.py:1822-1841`) | the on-disk screenshot/text exists but nothing in web_ui.py reads it back | varies | Live page re-read |
| `handoff_category` (14 values, `handoff.py:20-33`) | `Handoff.to_checkpoint_fields()` | Why the owner is needed | durable (checkpoint, SQLite-only) | **yes, already rendered** (`web_ui.py:762-780`) | yes (4 of 14 categories) | A page-level outcome handoff.classify()'s regex matches |
| `_RUNS[url].state` (free-text strings, `web_ui.py` multiple sites) | in-memory + `data/_runs.json` | Process-level run status | durable (JSON file) | yes, but coarse | varies | Process start/exit/signal |
| `recovery.reconcile()` outcomes (`MATCH, AHEAD, BEHIND, AUTH_REQUIRED, SUBMITTED, NO_CHECKPOINT`, ...) | `recovery.py:25-34` | How a resumed run's checkpoint compares to the live page | computed fresh, logged only | not currently surfaced to the UI at all | no (informational only) | A resumed run's first reconciliation pass |
| `form_answers` rows | `job_tracker.py:169-180` | Per-field question/answer/source | durable (DB), **no versioning** | yes, already rendered | no | Each field write |

**Collapsed states worth calling out explicitly**: `needs_user_review` is
reached by every one of these, with no further distinction in the status
column itself: a blank required field, a form validation error, a visible
CAPTCHA, an uncertain submission outcome, a validation loop, and a genuine
unhandled runtime crash (`apply_flow.py:1044`, note text "Runtime
interrupted; sanitized diagnostic capture attempted" -- itself sanitized by
the same B5 layer covered in section 3). The `handoff_category` field (when
present) already disambiguates most of these on the detail page; what's
missing is surfacing that disambiguation on the *list* page, where today
every `needs_user_review` row looks identical.

## 6. Missing telemetry inventory

| Needed state/signal | Classification | What exists today | What's needed |
| --- | --- | --- | --- |
| Current form-filling step/field | **NEW TELEMETRY REQUIRED** | Nothing -- confirmed no per-field live event exists | A new, bounded event emitted from `page_agent.py`'s fill loop |
| Retry/stall counter for the current action | **NEW TELEMETRY REQUIRED** | `state_machine.py`'s circuit breaker exists but only as a 3-strike trip, not a live counter exposed to the UI | Expose the breaker's `consecutive_matches` property via `_RUNS` or a new field |
| "Waiting for email code" as its own state | **NEW TELEMETRY REQUIRED** | Nothing durable (section 4, UI-03) | A new checkpoint/status field set when `complete_account_code()` is genuinely blocked or polling |
| CAPTCHA/MFA/handoff category on the list page | **INSTRUMENTABLE** | Already computed and stored (`handoff_category`), just not joined into the list-page query | Add `handoff_category` to the list-page query/render |
| Durable submission-dispatch state (`AUTHORIZED`/`DISPATCHED`/...) in the UI | **INSTRUMENTABLE** | Already computed and stored in `job_tracker.py` | Expose `get_submission_effect_state()` on the detail page |
| `recovery.reconcile()` outcome | **INSTRUMENTABLE** | Already computed and logged every resume | Surface the logged outcome on the detail page as informational text |
| Answer confidence score | **NEW TELEMETRY REQUIRED** | Nothing -- confirmed absent from the schema entirely | A new column; and a source for the value, since nothing currently computes a confidence number at all |
| Answer correction history | **NEW TELEMETRY REQUIRED** | In-memory-only + log lines, confirmed non-durable | A new table or JSON column recording prior value + reason when `form_answers` is overwritten |
| Account-state screenshots in the UI | **INSTRUMENTABLE** | Already written to disk (`job_dir/account/*.png`), confirmed unread by any route | Add a route/section exposing this folder, same containment pattern as `/evidence` |
| Funnel drill-down (click a stat → filtered table) | **INSTRUMENTABLE** | The data (`by_site`, `stop_reasons`) already exists; only the click-through UI is missing | Add query-param-based filtering to `/` and link the funnel stats to it |
| Resume-library/multi-resume selection | **NOT APPLICABLE (future feature)** | Single global path or single generated-per-job file; no library concept anywhere | Out of scope for a UI fix -- this is new product capability |

## 7. Four-dimensional state architecture

The current system already mixes what should be two separate concerns into
one flat `status` column (job lifecycle AND post-submission outcome:
`prepared`...`submitted` alongside `interviewing`/`rejected`/`offer` all live
in the same 9-value set, `web_ui.py:520-521`). The corrected architecture
separates genuinely distinct dimensions, each sourced from what already
exists wherever possible.

**A. Job lifecycle** (maps directly onto existing `job_tracker.py` status
values, split out from outcome):
`PREPARED` (EXISTS, `STATUS_PREPARED`), `FORM_FILLED` (EXISTS,
`STATUS_FORM_FILLED`), `SKIPPED` (EXISTS, `STATUS_SKIPPED` /
`DISQUALIFIED_POLICY_MISMATCH`). (`DISCOVERED`/`MATCHED`/`QUEUED` from the
prior audit's proposal do not correspond to anything in the real system --
there is no job-discovery/matching/queueing pipeline today; applying is
always to one URL the owner supplies. These would be **FUTURE FEATURE**, not
current states, and are not included here as current values.)

**B. Execution state** (sourced from `_RUNS[url].state` plus,
where instrumented, the circuit breaker):
`IDLE` (EXISTS -- no entry in `_RUNS`), `STARTING` (EXISTS, `"running"`
immediately after `/apply`), `APPLYING` (EXISTS, same `"running"` value --
today's system does not distinguish navigating/opening/answering, all
**NEW TELEMETRY REQUIRED** to split out), `VERIFYING_ACTION`
(**INSTRUMENTABLE** -- B4's `action_result.py` outcomes exist in the action
layer but are not threaded up to `_RUNS`), `WAITING` (EXISTS, a waiting
run's state), `PAUSED` (**NOT APPLICABLE** -- no true pause exists, only
stop/restart/resume, see UI-09), `FAILED` (EXISTS, `f"failed (exit
{code})"` or the sanitized runtime-error string), `COMPLETED` (EXISTS,
`"finished"` / `submitted` status).

**C. Human handoff state** (sourced directly from the real
`handoff_category` enum, not invented):
`NONE` (no active handoff), `CAPTCHA_REQUIRED` (EXISTS, `handoff.CAPTCHA`),
`MFA_REQUIRED` (EXISTS, covers `SMS_MFA`/`AUTHENTICATOR_MFA`/`SECURITY_KEY`/
`PUSH_APPROVAL`), `EMAIL_CODE_REQUIRED` (**NEW TELEMETRY REQUIRED**, section
3/7 -- no real state exists for this today even though the category list
has room for it), `QUESTION_REQUIRED` (EXISTS, `handoff.FIELD_REQUIRED`),
`REVIEW_REQUIRED` (EXISTS, maps to `ready_to_submit`), `OWNER_REQUIRED`
(EXISTS, `handoff.OWNER_REVIEW`/`OTHER`/`ACCOUNT_LOCKED`/
`ACCOUNT_CREATION_UNCERTAIN`/`VALIDATION_BLOCKER`/`UNSUPPORTED_CONTROL`/
`ACTION_OUTCOME_UNKNOWN`/`APPLICATION_RECOVERY`).

**D. Application outcome** (currently folded into the same column as
lifecycle; corrected to its own dimension using the exact same real values):
`NOT_SUBMITTED` (implicit -- absence of `submitted`), `SUBMITTED` (EXISTS),
`REJECTED` (EXISTS, owner-set), `INTERVIEW` (EXISTS, `interviewing`,
owner-set), `OFFER` (EXISTS, owner-set). `WITHDRAWN` from the prior audit's
proposal does **not** exist as a value anywhere -- **FUTURE FEATURE**, not a
current state.

### Combination examples

| execution | handoff | outcome | Renders as |
| --- | --- | --- | --- |
| `APPLYING` | `NONE` | `NOT_SUBMITTED` | "Applying -- Acme Corp, Network Engineer" |
| `WAITING` | `CAPTCHA_REQUIRED` | `NOT_SUBMITTED` | "Action required -- solve CAPTCHA to continue" |
| `WAITING` | `EMAIL_CODE_REQUIRED` | `NOT_SUBMITTED` | "Waiting for a verification code sent to your email" (today: indistinguishable from `APPLYING`) |
| `WAITING` | `MFA_REQUIRED` | `NOT_SUBMITTED` | "Action required -- a second sign-in step is needed" |
| `WAITING` | `QUESTION_REQUIRED` | `NOT_SUBMITTED` | "Action required -- a required field needs your answer" |
| `WAITING` | `REVIEW_REQUIRED` | `NOT_SUBMITTED` | "Ready for you to review and submit" |
| `FAILED` | `OWNER_REQUIRED` | `NOT_SUBMITTED` | "Stopped -- needs a look (see the reason below)" |
| `COMPLETED` | `NONE` | `SUBMITTED` | "Submitted -- confirmed by the site" |
| `COMPLETED` | `NONE` | `INTERVIEW` | "Submitted -- interview scheduled" (owner-set) |
| `COMPLETED` | `NONE` | `REJECTED` | "Submitted -- not selected" (owner-set) |
| `IDLE` | `NONE` | `NOT_SUBMITTED` | No active run; shows last known lifecycle status (`PREPARED`/`FORM_FILLED`) |

## 8. Current defects vs. future product capabilities

### A. Current UI/UX defects (corrected list, this audit's own findings)

UI-01-R (live telemetry granularity, P1), UI-02-R (no proactive handoff
alert, P1), UI-03-R (no durable email-wait state, P1), UI-05-R (one narrow
unsanitized exception fragment, P3), UI-06-R (no confidence/correction
history on an already-working answer ledger, P2), UI-07-R (no structured
license sub-fields, P3), UI-08-R (unbounded DB fetch despite a bounded
render, P2), UI-09-R (no true mid-action pause, P2), UI-10-R (no multi-source-resume
management, P2), UI-12-R (funnel stats not clickable, P3), UI-13 (no local
authentication boundary, P2), UI-15-R (no `aria-live`, unverified contrast
values, P3). UI-04, UI-11, UI-14 confirmed as described in substance (UI-04
reclassified as a transparency gap, not a safety gap; UI-11 verified as-is;
UI-14 is **closed**, not a defect at all).

### B. Future product capabilities (explicitly out of scope for a UI fix)

- Automatic job discovery/matching/scoring (`DISCOVERED`/`MATCHED`/`QUEUED`
  job-lifecycle states from the prior audit's proposal -- no such pipeline
  exists; applications are always to one owner-supplied URL).
- Resume library / multi-resume tagging and selection (config.py has exactly
  two resume *modes* -- tailored-per-job or one static master file -- never a
  library of named variants).
- Answer confidence scoring (no component in the codebase currently computes
  a confidence number for any answer).
- Licensing/payments, multi-user/SaaS boundaries (this is a single-owner,
  loopback-only local tool by design, confirmed by `web_ui.py`'s own
  docstring and `web_guard.py`'s scope).
- Remote/phone handoff (WebRTC or similar) -- nothing resembling this exists.
- `WITHDRAWN` application outcome tracking -- no such value exists anywhere.

## 9. Real-time transport assessment

1. **Threaded or async?** Flask's built-in dev server, run directly
   (`app.run(host="127.0.0.1", port=5000, ...)`, `web_ui.py:1548-1549`) --
   not an async framework (no `asyncio`/ASGI server in use for this app).
   Long-running work (an application run) is offloaded to a daemon thread
   (`threading.Thread(target=_run_apply, ...)`, `web_ui.py:233`) driving a
   `subprocess.Popen` of `apply.py`, not an async task.
2. **SocketIO/SSE extensions?** None. No such dependency is imported or used
   anywhere in the project.
3. **How updates currently reach the browser:** a `setInterval` that calls
   `location.reload()` (a full page navigation) every 5 seconds while a run is
   live, suppressed during active typing or an open menu
   (`web_ui.py:1146-1155`). The detail and progress pages have no auto-refresh
   at all.
4. **Can SSE be added incrementally?** Yes, without an async migration. Flask
   can serve a `text/event-stream` response from a generator function on its
   existing synchronous dev/production server (each open SSE connection just
   holds a worker thread/connection for its duration); this is a well-trodden,
   incremental addition, not an architecture change, though it does mean each
   concurrent SSE client holds a thread for its connection's lifetime -- fine
   at this project's actual concurrency (one local user, one browser tab),
   not something to scale past that assumption without reconsidering.
5. **Simplest reliable path to real-time telemetry, in order:** (a) replace
   the full-page `location.reload()` with a `fetch`-based partial refresh of
   just the run-status card, polling every 2-3 seconds -- zero new
   dependencies, immediately removes the jarring full navigation and is a
   strict improvement on today's mechanism; (b) only once genuinely granular,
   sub-field-level events exist to stream (which requires new backend
   instrumentation regardless of transport, see section 6), consider adding
   one SSE endpoint for the live-run card specifically. Given this project's
   actual scale (one local user, one browser), SSE is a reasonable later step,
   not a blocking prerequisite -- the prior audit's recommendation to build an
   "SSE-streaming Live Agent Cockpit" first gets the sequencing backwards: the
   missing piece today is the backend telemetry itself, not the transport.

## 10. Corrected implementation priorities

**P0 -- none.** Nothing in this corrected audit rises to P0. The prior
audit's one P0-worthy claim (UI-04, a missing submission gate) is false: the
real gate is already strict, multi-layered, and default-off. No other
finding here represents an active safety or data-loss risk.

**P1 -- transparency into an already-safe system:**
1. UI-01-R: replace the 5-second full-page `location.reload()` with a
   `fetch`-based partial refresh of the run-status card (no new backend
   instrumentation required for this step alone).
2. UI-03-R: add a durable "waiting for email code" signal -- today this is
   the one state with no record at all, not even a degraded one.
3. UI-02-R: surface `handoff_category` on the list page (data already
   exists, `web_ui.py` query just needs to join it), then add a proactive
   notification (sound/OS notification) when it changes to a
   human-required category.

**P2 -- already-working features that need extending, not building:**
4. UI-06-R: add `confidence` and correction-history to the already-working
   `form_answers` table/UI.
5. UI-08-R: add real `LIMIT`/`OFFSET` to `list_all()` so the database query
   scales with what's shown, not total history.
6. UI-09-R: add a true mid-action pause point in the fill loop (Stop/
   Restart/Resume already cover most of the "don't lose my place" value).
7. UI-10-R: allow more than one source resume to be uploaded/selected from.
8. UI-13: add an optional local PIN, now that the existing CSRF/origin
   protection (`web_guard.py`) is confirmed working correctly for its own,
   different purpose.

**P3 -- narrow, low-risk polish:**
9. UI-05-R: replace the one 120-char exception fragment with a
   pre-classified message.
10. UI-07-R: add a structured license sub-object (issuing authority, number,
    expiration) in place of the flat `certifications` string list.
11. UI-12-R: make funnel stat cards clickable drill-down filters (data
    already computed).
12. UI-15-R: add `aria-live="polite"` to the run-status region; re-verify
    actual rendered contrast against the real `--muted` tokens
    (`#667085`/`#94969c`) rather than the prior audit's unverifiable figures.

**Closed, not a defect:** UI-14 (responsive mobile layout already exists and
works).

## 11. Residual uncertainty

A few items this pass could not fully close out and a follow-up should treat
as open, not assumed:

- Whether reading an email verification code actually opens a *new* browser
  tab (as the prior audit's vivid anecdote claims) or reads an *existing*
  Gmail tab the owner already has open (as `CLAUDE.md`'s phrasing "the
  owner's signed-in Gmail tab" suggests) was not independently re-verified
  by either research pass in this audit.
- The `account/*.png`/`*.txt` diagnostic screenshots (section 4, UI-03) are
  confirmed written but confirmed unread by any current route; whether that
  is simply unfinished wiring or a deliberate privacy choice was not
  determined.
- This audit covers `main` at the base SHA only. A large, separate,
  still-draft branch (`fix/adp-apply-and-questions`) has extensive unmerged
  Workday/ADP fixes (including `sites/adp.py`, which does not exist on
  `main` at all); none of that work is reflected here, by design, per the
  task's own scope instruction.
