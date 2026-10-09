# Job Automation Agent — Runtime Architecture

## How to use this specification

This is the product contract and incremental architectural direction, not a claim that every capability below is already implemented. Read only the relevant sections for the current task. Future user instructions take precedence. This document is context, not an automatic implementation queue.

The current runtime starts at `web_ui.py` → `apply.py` → `apply_flow.py`; page handling lives in `page_agent.py`, with shared field, account, policy, and portal helpers. `BEHAVIOUR.md` documents current user-visible behavior; `CLAUDE.md` retains repository engineering constraints. Inspect the relevant implementation before changing it.

Current policy remains explicit: final submission uses the existing review gate or the user-enabled, strictly verified auto-submit path. Declarations require the stored signing permission and final validation. `safety.py` centralizes policy and changes require the owner's explicit approval on the PR. General autonomy goals do not authorize bypassing those gates.

Current employer ATS credentials come from configured identity and `ATS_PASSWORD`, not newly invented passwords. Identity-provider passwords remain blocked by `safety.password_allowed()`. Authorized email reads use `emailed_codes.why_not()` and `login_guard`; existing verification links must return to the same employer site and tenant. The existing once-per-site reset exception restores that same ATS password only under its established conditions. The generic verification and authentication capabilities below remain subject to explicit authorization and supported secure mechanisms.

## 1. Product Goal

The application agent should operate as autonomously as reasonably possible.

Target experience:

Job selected
→ application opened
→ portal identified
→ account state determined
→ account created or existing account used
→ verification handled
→ authenticated session established
→ application understood
→ fields answered
→ documents uploaded
→ navigation completed
→ actions verified
→ review/submission reached
→ submission result verified
→ application state recorded

Human intervention should only occur when information or authentication genuinely requires the user.

The product should move toward:

“Start the application and allow the agent to handle the process.”

## 2. Priority Order

Optimize for:

1. correctness
2. reliability
3. autonomous completion
4. authentication reliability
5. safe recovery
6. user-data privacy
7. low AI cost
8. maintainability
9. performance
10. new features

Do not sacrifice correctness merely to reduce AI usage.

---

# ANSWER RESOLUTION

## 3. Answer Hierarchy

For every application field:

1. user profile
2. explicit preference
3. approved saved answer
4. deterministic rule
5. current application context
6. AI interpretation
7. ask the user

Do not use AI when deterministic information already answers the question.

Do not ask the user simply because the wording is unfamiliar.

## 4. Never Invent User Data

Never fabricate:

- employment
- education
- certifications
- immigration/work authorization
- addresses
- salary preferences
- dates
- personal identity data
- security-question answers
- legal declarations

If genuinely required information is missing, ask the user.

## 5. Autonomous Fields

When supported by stored information, automatically fill:

- legal/preferred name
- email
- phone
- address
- LinkedIn
- GitHub
- portfolio
- employment history
- education
- skills
- certifications
- resume
- cover letter when applicable
- work authorization
- sponsorship
- immigration-related application questions
- relocation
- location preference
- salary expectation when a rule exists
- gender
- race/ethnicity
- veteran status
- disability/self-identification
- previously approved employer questions

Respect stored user choices exactly.

Never replace an explicit approved answer with an AI-generated one.

## 6. Optional Fields

If a field is optional and no supported answer exists:

normally leave it blank.

Do not stop the application merely because an optional field is unanswered.

---

# FIELD MODEL

## 7. Field Metadata

Where practical, detected fields should carry structured metadata:

- field identifier
- label
- field type
- section
- repeated-entry context
- required / optional / conditional / unknown
- evidence for requirement state
- proposed answer
- answer source
- confidence
- interaction attempted
- website acceptance
- verification status

Avoid broad page-level assumptions when a decision belongs to one specific field or section.

## 8. Required / Optional Scope

Requirement status should be tied to the correct field and section.

Example:

An optional account section containing a password must not cause:

- a required login password elsewhere
- OTP
- verification code
- authentication prompt

to be ignored.

Nearby labels and section context should be used for unnamed controls when necessary.

Mixed password forms should be handled conservatively.

---

# BROWSER AUTOMATION

## 9. Browser Responsibilities

Prefer deterministic browser automation for:

- DOM/accessibility inspection
- identifying controls
- typing
- clicking
- selecting
- uploads
- navigation
- reading validation errors
- checking visible state
- verifying outcomes

Use AI mainly for interpretation and reasoning.

Avoid using an AI call for every browser action.

## 10. Preferred Runtime Flow

Preferred architecture:

Parse page locally
→ identify controls
→ resolve known answers locally
→ identify unresolved questions
→ send only necessary context to AI
→ receive structured decisions
→ execute actions locally
→ verify locally
→ checkpoint

Do not continuously send entire pages/screenshots to AI when local parsing is sufficient.

Reduce:

- irrelevant navigation
- repeated boilerplate
- already answered controls
- huge unnecessary option lists
- duplicate page text

Preserve information required for correct decisions.

---

# ACCOUNT MANAGEMENT

## 11. Account Management Is Core Product Behavior

The agent must handle account creation and authentication across multiple application systems.

Examples:

- Workday
- UKG
- Greenhouse
- Lever
- iCIMS
- Taleo
- SmartRecruiters
- SuccessFactors
- ADP
- custom company career portals
- other ATS systems

Do not architect authentication solely around Workday or any single portal.

## 12. Account State Machine

Maintain explicit states such as:

`NO_ACCOUNT`

`ACCOUNT_CREATION_STARTED`

`VERIFICATION_REQUIRED`

`ACCOUNT_CREATED`

`SIGNED_OUT`

`SIGN_IN_REQUIRED`

`SIGNING_IN`

`AUTHENTICATED`

`PASSWORD_RESET_REQUIRED`

`AUTHENTICATION_FAILED`

`ACCOUNT_LOCKED`

`AUTHENTICATION_STATE_UNKNOWN`

`OUTCOME_UNKNOWN`

Authentication behavior should evolve toward a state machine instead of unrelated special cases.

---

# ACCOUNT CREATION

## 13. Required Account Creation

When an account is required:

1. determine whether an appropriate account already exists
2. use an existing account when appropriate
3. otherwise create an account
4. use the correct stored identity
5. securely obtain or create credentials according to project policy
6. submit account creation
7. verify whether creation succeeded
8. detect verification requirements
9. complete supported verification
10. establish authenticated state
11. continue the application

Do not create duplicate accounts unnecessarily.

## 14. Unknown Creation Outcome

If account creation is interrupted:

do not immediately try to create another account.

Set the result conceptually to:

`OUTCOME_UNKNOWN`

Inspect the actual portal/account state.

Determine whether account creation succeeded before retrying.

## 15. Optional Account Creation

If an account section is explicitly optional and the application can continue without it:

skip it when appropriate.

An empty password inside that optional section must not cause unnecessary user handoff.

This behavior applies only to that optional account section.

It must not suppress legitimate authentication elsewhere.

---

# SIGN-IN

## 16. Authentication Flow

When sign-in is required:

1. identify the correct portal
2. identify the correct account
3. securely retrieve the credential
4. enter username/email
5. enter password
6. submit
7. verify authenticated state

Do not mark authentication successful simply because the login button was clicked.

## 17. Authentication Verification

Evidence may include:

- transition into application
- disappearance of login UI
- authenticated account UI
- expected authenticated session indicators
- successful application-state transition

## 18. Failure Classification

When login fails, determine why before retrying.

Possible classifications:

- incorrect username
- incorrect password
- account does not exist
- account not verified
- email verification required
- OTP required
- MFA required
- password expired
- CAPTCHA
- account locked
- portal/site failure
- unexpected page
- unknown failure

Do not blindly retry credentials.

## 19. Attempt Limits

Track authentication attempts per account/portal where practical.

Protect the user from lockout.

Preferred flow:

failure
→ inspect error
→ classify
→ correct cause
→ retry only if justified

Not:

failure
→ retry
→ retry
→ retry

---

# VERIFICATION

## 20. Email Verification Links

When the portal requires an email verification link:

- detect the requirement
- use an authorized email integration if available
- identify the correct recent message
- ensure it belongs to the current account and transaction
- follow the link only when authorized
- return/reconcile browser state
- verify account status
- continue

Avoid stale or unrelated verification links.

## 21. Email OTP

When an OTP arrives through email:

- retrieve it only through an authorized mechanism
- confirm it belongs to the active authentication flow
- enter it
- submit it
- verify acceptance
- continue

Treat OTPs as secrets.

Do not retain them longer than needed.

Do not expose them in ordinary logs.

## 22. SMS Verification

If SMS verification is required:

use an explicitly authorized integration if the project supports one.

Otherwise:

- preserve current state
- hand control to the user
- tell the user precisely that SMS verification is required
- continue automatically after successful verification

Never guess verification codes.

## 23. Authenticator / TOTP

Use authenticator codes automatically only through an explicitly authorized and secure mechanism.

Otherwise hand off to the user.

Never bypass MFA.

## 24. Magic Links

If a portal uses a magic link:

- locate the current authorized message
- avoid expired/stale links
- follow the valid link
- reconcile the authenticated browser session
- verify login
- continue the existing application

## 25. CAPTCHA

When CAPTCHA appears:

- detect it
- do not bypass or defeat it
- preserve browser/application state
- explain exactly what requires user action
- resume once completed

## 26. Security Questions

Use a security-question answer automatically only if the user explicitly stored/approved that answer.

Otherwise ask the user.

Never invent answers.

---

# CREDENTIALS AND SECURITY

## 27. Credential Safety

Credentials are secrets.

Never:

- commit passwords
- print full passwords in logs
- send passwords to AI unnecessarily
- expose session tokens
- expose cookies
- expose OTP codes
- expose authentication headers
- unnecessarily capture secrets in diagnostic artifacts

Prefer secure credential storage.

The AI reasoning layer should normally not need raw passwords.

## 28. Account Identity

Account records should be associated with information such as:

- employer
- portal/domain
- username/email
- credential reference
- creation status
- verification status
- last successful login
- failed attempt count
- lockout information
- active session information

Do not select credentials only because an email address happens to match.

---

# SESSION MANAGEMENT

## 29. Sessions

Preserve valid authenticated sessions where practical.

Do not unnecessarily sign in repeatedly.

Before trusting a stored session:

verify that it is still authenticated.

Expired sessions should transition back into authentication logic safely.

---

# AUTHENTICATION ARCHITECTURE

## 30. Shared Authentication Manager

Move incrementally toward a shared authentication abstraction, conceptually similar to:

`AuthManager`

Capabilities may include:

`detect_auth_state()`

`account_exists()`

`create_account()`

`detect_verification_requirement()`

`complete_supported_verification()`

`sign_in()`

`verify_authenticated()`

`recover_auth_state()`

`handoff_to_user()`

Do not perform a giant rewrite just to introduce this abstraction.

Refactor incrementally behind stable interfaces.

## 31. Portal Adapters

Portal adapters should contain portal-specific browser behavior.

Shared authentication policy should remain centralized whenever practical.

Do not duplicate the entire authentication decision system for every ATS.

---

# ACTION VERIFICATION

## 32. Verify Actions

Never assume an action succeeded merely because automation attempted it.

After important actions, inspect the result.

Text field:
verify expected value.

Dropdown:
verify committed selection.

Upload:
verify attachment.

Next/Continue:
verify the application advanced.

Account creation:
verify account state.

Login:
verify authenticated state.

Submission:
verify confirmation.

## 33. Non-Idempotent Actions

Be especially careful with:

- account creation
- password reset
- verification
- sending messages/information
- final submission

If interrupted:

set state to:

`OUTCOME_UNKNOWN`

Inspect before attempting again.

Never blindly duplicate consequential actions.

---

# APPLICATION SUBMISSION

## 34. Before Submission

Verify:

- required fields completed
- known answers align with stored profile/preferences
- validation errors resolved
- required documents attached
- no critical unresolved fields remain

## 35. After Submission

Look for strong confirmation evidence.

Only mark an application:

`SUBMITTED`

after confirmation is sufficiently clear.

If ambiguous:

record an uncertain submission outcome
and inspect further.

Never intentionally submit the same application twice.

---

# CHECKPOINTING

## 36. Checkpoint Data

A useful application checkpoint should include:

- application identity
- employer
- portal
- job URL
- authenticated account
- verified current step
- completed controls
- repeated entries
- uploaded documents
- pending action
- last verified action
- uncertain actions
- code/version
- relevant session state

## 37. Resume

When resuming:

1. inspect the actual browser state
2. compare with checkpoint
3. reconcile differences
4. continue from verified state

Do not simply reload the previously saved URL and assume it is correct.

Do not treat a random visible input as proof that the correct application has resumed.

---

# FAILURE RECOVERY

## 38. Interrupted Authentication

If interruption occurs during:

- account creation
- sign-in
- verification
- password reset

determine the actual state before retrying.

Example:

If account creation might already have succeeded, check first.

Do not create another account automatically.

## 39. Code Changes During Paused Runs

Prefer associating each application run with a known code version.

Avoid uncontrolled hot-swapping of behavior into an existing run.

If a fix is needed during a paused application:

checkpoint
→ apply/update code
→ reconcile actual browser state
→ continue

Preserve reproducibility.

---

# HUMAN HANDOFF

## 40. Precise Handoff

Human handoff should say exactly what is required.

Good:

“SMS verification is required for the KBI Workday account.”

Good:

“CAPTCHA is blocking the Workday sign-in page.”

Bad:

“Agent needs your help.”

A handoff should identify:

- employer
- portal
- application
- current stage
- required user action
- work already completed

After the user completes the step:

detect the new state
and continue automatically.

Do not require the user to restart the application.

---

# ARCHITECTURAL DIRECTION

## 41. Core Runtime Pipeline

Move gradually toward:

Read page
→ identify controls
→ classify fields
→ resolve answers
→ apply policy
→ act
→ verify
→ checkpoint

Do not perform an enormous rewrite solely because current modules are large.

Improve boundaries incrementally.

## 42. Shared Policy vs Portal Behavior

Portal-specific modules should describe portal/browser differences.

Shared policy should decide things such as:

- whether a field needs user input
- whether an answer is supported
- whether an optional field can be skipped
- whether an action needs verification
- whether authentication should retry

Avoid portal-specific duplication of common policy.

---

# TESTING

## 43. Testing Order

For a bug:

1. reproduce
2. add regression coverage when practical
3. implement minimal fix
4. run targeted test
5. run related tests
6. run larger suites only if justified

## 44. Testing Layers

Maintain multiple confidence layers:

- unit tests
- targeted regressions
- saved-page tests
- browser interaction tests
- authentication tests
- application workflow tests
- resume/recovery tests

Replay consistency alone is not proof of correctness.

## 45. Synthetic Profiles

Prefer synthetic test profiles instead of relying on the real user's private data.

Cover scenarios such as:

- different work histories
- different education structures
- multiple repeated entries
- optional accounts
- existing accounts
- new accounts
- email verification
- OTP
- MFA handoff
- CAPTCHA
- interrupted authentication
- expired session
- interrupted submission

---

# PRIVACY AND DIAGNOSTICS

## 46. Sensitive Diagnostics

Screenshots, HTML dumps, traces, and logs may contain:

- personal data
- credentials
- tokens
- application answers
- account information

Move toward centralized diagnostic capture that supports:

- masking
- sanitization
- retention limits
- safe debug exports

Git ignore rules alone are not sufficient privacy protection.

---

# OBSERVABILITY

## 47. Explainability

Important decisions should be traceable.

For application answers, record:

- chosen answer
- source
- reason

For browser actions:

- attempted action
- observed result

For user handoff:

- exact reason automation could not continue

---

# RELIABILITY METRICS

## 48. Metrics Worth Tracking

As the product matures, measure:

- applications completed without intervention
- applications reaching review
- incorrect answers detected
- unnecessary handoffs
- successful resume/recovery rate
- authentication failure rate
- completion by portal
- AI/model cost per application
- AI calls per application
- uncertain or duplicate actions

Use measured outcomes to evaluate architectural improvements.

---

# AI COST CONTROL

## 49. Runtime AI Efficiency

Reduce AI cost through:

- profile/rules first
- saved-answer reuse
- deterministic parsing
- targeted context
- compressed dropdown representations
- batching compatible unresolved fields
- avoiding repeated page analysis
- avoiding duplicate model calls
- using cheaper models for simple interpretation when suitable

Do not reduce context so much that accuracy suffers.

---

# CURRENT RELIABILITY WORK

## 50. Current Optional-Account Issue

The current/recent reliability work includes optional account fields.

Intended behavior:

- optional account password can remain blank
- required password elsewhere remains required
- OTP remains detectable
- verification code remains detectable
- unnamed inputs can use nearby label/section context
- mixed password forms are handled conservatively
- authentication behavior must remain intact

Before changing this area, inspect current repository state and existing regression work.

Do not redo work that is already implemented.

---

# CODING-AGENT ORCHESTRATION

## 51. One Agent by Default

For repository development use one coding agent by default.

Use a subagent only when work is independent.

Good split:

Primary:
authentication architecture

Subagent:
independent field-resolution tests

Bad split:

Primary:
debug page_agent.py

Subagent:
also debug page_agent.py

Avoid redundant context consumption.

## 52. Model Escalation

When model selection is supported:

Simple work
→ cheapest capable coding model

Normal feature/bug work
→ balanced capable model, medium reasoning

Hard state/browser/authentication problem
→ stronger reasoning

Exceptionally difficult unresolved issue
→ highest-capability model

Do not use the highest-capability model simply because it exists.

Escalate when needed rather than spending credits on repeated failed attempts.

---

# PRODUCT END STATE

## 53. Desired User Experience

Eventually I should be able to start an application and have the agent:

- identify the ATS
- determine account state
- create an account if needed
- use an existing account when appropriate
- sign in
- handle supported verification
- safely hand off unsupported MFA/CAPTCHA
- resume automatically
- answer known questions
- use AI for genuinely ambiguous interpretation
- upload documents
- navigate all steps
- recover from interruptions
- verify all important actions
- reach review/submission
- verify submission
- record the result

The user should be interrupted only when genuinely required.

Autonomous does not mean reckless.

The agent must remain evidence-driven, state-aware, and recoverable.
