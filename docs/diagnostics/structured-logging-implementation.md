# Structured diagnostics and secure handoff storage

## Baseline and scope

Original fetched `origin/main` base: **221247a7baf54d31ba0925ce2be1d732b229b746**.
Branch: `feature/structured-diagnostics`, isolated from the owner's checkout.
Read-only dependencies supplied during implementation were fast-forwarded intact:

- `e4b358d`: verified UI discovery audit.
- `5b0969d2f20aa871a097ae5ea89dbafd2d6c24e6`: shared telemetry schema/bus.

`runtime_events.py` is unchanged from its author's commit. No UI, runtime emitter,
submission, safety, checkpoint or recovery modules are changed. The owner must
reconcile overlapping audit commits when integrating the separate branches.

## Status and shared contract discrepancies

**NOT READY TO MERGE: the supplied shared schema differs from Prompt 3.**

Its `RootCauseCategory` enum has `SELECTOR_CHANGED`, `AUTH_EXPIRED`,
`NETWORK_TIMEOUT`, `FIELD_UNSUPPORTED`, `STALL`, `BOT_DETECTION`,
`USER_ACTION_REQUIRED`, `INTERNAL`. It does not contain the requested sixteen
subsystem categories. Reports retain the actual shared category (`INTERNAL`
fallback when no exact member exists), separately expose `diagnostic_subsystem`,
and set `schema_category_gap=true`. That fallback means unavailable taxonomy,
not evidence that the runtime itself caused the failure.

The schema also lacks `RUN_FAILED`, `OWNER_HANDOFF_CREATED`,
`LOOP_FINGERPRINT_REPEATED`, `MAIL_LOOKUP_STARTED`, field-write attempt/result
events and top-level `state_before`/`state_after`. Unknown event names are
rejected rather than silently added to another agent's schema. Current
`RUN_STOPPED` with a non-success reason and `HANDOFF_CREATED` create bundles.
The requested event names work when present in the supplied enum, as proven by
synthetic contract tests, but cannot be emitted as typed members of this schema.
Transition fields inside `safe_metadata` are consumed when supplied.

The schema's metadata sanitizer is not a B5 export boundary: it retains arbitrary
strings and can replace browser-session references based on key substrings.
This implementation independently projects every export, and does not use
`RuntimeEvent.as_dict()` or trust the name `safe_metadata` as proof of privacy.
Upstream raw event buffering, UI consumption and emitter coverage are outside
this PR's ownership and are not certified by these tests.

## Integration API and event storage

```python
from structured_logging import SchemaBinding, StructuredLogConsumer

consumer = StructuredLogConsumer(output_dir, SchemaBinding()).start()
# RuntimeEvent.emit(...) now calls the registered storage-only listener.
# Close explicitly when the execution ends:
consumer.close()
```

Registration is opt-in and idempotent. Another agent must wire its lifetime into
the runtime. Importing these modules starts no worker, browser, notification,
server, model call or runtime action. `consume(event)` reports telemetry storage
success only; its boolean must never be used as action permission.

Per-run JSONL reuses B5 file/manifest infrastructure instead of creating a second
event database. Existing tracker event APIs use a narrower, older payload
projection and cannot preserve the shared schema; they remain unchanged.

Path: `output/<sha256(application_key)>/diagnostics/<sha256(run_id)>/run_events.jsonl`.
Supplied job/company/browser/action/fingerprint references are also hashed.
Empty and default run IDs are refused. All public declared event fields retain
their schema positions under `event_data`; private dataclass bus/lock fields are
excluded. Unknown free text is withheld. Canonical envelope fields add sequence,
severity, represented count and verified action timing.

Bounds: 256 retained records, 1 MiB per artifact, 32 cached runs. The last verified
event and current-stage anchor are prioritized alongside the tail. Byte pressure
can shorten history further; sequence gaps make truncation visible. Reopening a
consumer replays only manifest-verified bytes for its run/application. Repeated
identical retry/loop observations keep first/last timestamps and exact represented
count. Distinct retries remain separate until bounded retention removes them;
summary counts explicitly refer to retained history, never an invented lifetime
total. Source attempt/max/reason/change facts are retained only when safe.

One runtime owner/consumer per run is required. In-process calls serialize with a
lock; simultaneous processes writing the same run are not supported. Bus
callbacks are synchronous, so disk overhead requires observation during the
other agent's runtime integration. This is not an asynchronous delivery queue.

## Transitions, attempts and durations

`state_before` plus `state_after` produce previous/new state, trigger, safe
evidence and timestamp. Closed state vocabularies preserve known account,
mail, handoff, checkpoint and submission facts. Unknown free-string states are
withheld. Report defaults are UNKNOWN; absence of telemetry proves neither
absence of Gmail activity nor absence of a submission effect.

Attempt/start events and verified/failed/blocked/no-change observations remain
separate records. A verified progress claim must be a result event and, where
the schema provides it, have `is_verified=true`; setting that flag on an attempt
does not promote the attempt. Diagnostics never rewrite that flag in the event.

Timing requires a matching explicit action/operation ID and operation family.
It stores started/finished ISO timestamps and nonnegative elapsed milliseconds.
Missing IDs, multiple competing starts, negative durations or missing start
history remain unpaired. Emitter work must supply IDs for navigation, Gmail,
model/upload/settlement/submission/handoff pairs; timestamps alone are not proof
that concurrent actions correspond. Ordinary retries have no raw traceback.

## Stop-Cause Bundles and classification

Abnormal observed terminal events automatically generate `stop_cause/`:

- `summary.json`: correlation, stage, last verified progress, next attempted
  action, observed evidence, reason, category/subsystem, retained retries/loops,
  account/mail/handoff/checkpoint/submission facts and next investigation.
- `timeline.jsonl`: last 50 retained events, stage anchor, last verified event and
  retained retries of the failing operation.
- `state_transitions.json`, `action_history.json`, `stop_context.json`.
- `safe_errors.json`: closed exception category and fixed explanation; no stack.
- `checkpoint_summary.json`, `handoff_summary.json`: observed safe facts only.
- `stop_summary.txt`, `copy_for_agent.txt`: concise human/developer reports.

Each file uses the existing capture-version-5 manifest and SHA-256 verification.
Partial bundle failures remove this generator's fixed bundle artifacts, avoiding
a mixed new timeline/old stop report. No fallback reads or copies raw HTML,
screenshots, page recordings, form answers, logs or checkpoints.

Classification uses observed stable reason codes, safe explicit category and
event names in reverse order, without an AI call or numerical confidence.
The desired subsystems are NAVIGATION, APPLICATION_ENTRY, PAGE_CLASSIFICATION,
FORM_FIELD, ANSWER_SELECTION, ANSWER_VALIDATION, AUTHENTICATION,
EMAIL_VERIFICATION, CAPTCHA, MFA, RECOVERY, LOOP_STALL, SUBMISSION, NETWORK,
PORTAL_CHANGED and INTERNAL_RUNTIME. Shared enum gaps are disclosed above.
An uncertain/dispatched submission observation always recommends independent
reconciliation rather than interpreting a diagnosis as permission to retry.

## Human and Copy-for-Agent reports

`run_diagnostics.read_report(run_folder, copy_for_agent=True)` reads only the
already-projected, manifest-verified report. Reports separate FACTS, LIKELY
CAUSE and RECOMMENDED INVESTIGATION, include static real production paths, and
identify sensitive values as REDACTED / OMITTED. They provide investigation
steps, not speculative fixes. Old, missing, invalid or tampered captures produce
a fixed unavailable report; raw legacy logs are not re-exported.

## Handoff security model

`HandoffStore(existing_sqlite_tracker_path)` creates only its own private
`diagnostic_handoffs` table/index, preserving application/authority tables.
An explicit path is required; there is no implicit second logging database.
Postgres installs need an equivalent store adapter before runtime wiring.

`create_session()` constructs and stores the actual shared `HandoffSession`
using fixed safe display facts and known handoff category/shared reason enum.
Application and browser bindings are hashes, never browser/CDP addresses.
`get_session()` returns that shared model with the current storage status.
The lower-level `create()` returns an invitation for callers needing storage
alone. No incoming arbitrary handoff text, credentials or browser state is
persisted. The token is returned separately with its repr suppressed.

Tokens use `secrets.token_urlsafe(32)`; public handoff IDs use `secrets.token_hex(32)`.
Their distinct formats prevent a bearer token from being accepted in the push
payload's ID field. Company references are rehashed before display. Only SHA-256
token hashes are stored. Atomic
SQLite transactions and constant-time hash comparison permit exactly one
PENDING-to-OPENED claim. Claims erase the token hash. Status follows OPENED ->
IN_PROGRESS -> COMPLETED; active sessions can be revoked or expire. Terminal
sessions cannot reopen. Every claim/read/update requires application/browser
binding. Default TTL is 600 seconds (configurable 1–3600). `cleanup()` deletes
expired private records. Remote configuration requires an HTTPS origin without
userinfo, query, fragment or session path.

This is invitation lifecycle metadata, not authorization to control a remote
browser, clear CAPTCHA/MFA, resume a job, reconcile a checkpoint or submit.
Authenticated remote transport, token delivery, browser isolation and live
completion verification are separate future integration work. No such server
or route is implemented here.

## Mobile notification foundation

`NotificationRegistration` defines device ID, HTTPS push endpoint, bounded
Web Push keys, created_at and user reference, hiding routing fields in repr.
`NotificationPayload` contains the fixed title/body with a hashed company
reference; `data` contains only the opaque handoff ID. The bearer token is not a
notification field. `NotificationDispatcher` is abstract. Actual Web Push/PWA,
private subscription persistence/encryption, authenticated device registration,
company display-label resolution, and delivery are deferred. No notification
was sent while implementing/testing this foundation.

## B5 privacy and retention review

Manual scope review plus automated sentinel/security tests verify:

- Structural allowlists, declared schema fields, closed codes/labels/counts,
  hash-only correlation; no trust in arbitrary metadata/display/error strings.
- Unknown applicant/body fields are withheld wholesale even if nested keys
  imitate structural facts. No cookie, token, OTP, credential or form capture.
- Sanitizer/write failures omit artifacts. Manifest tampering fails closed.
- No raw screenshot/DOM/trace capture and no stack exported in these reports.
- Fixed bundle file allowlist; existing path/link/junction protection and limits.
- Windows long-path support for opaque run directories.
- Existing configurable retention reaches only the exact structured subtree;
  active captures, materials and permanent `pages/` replays remain protected.
- No imports/calls into action or authority modules from the diagnostic modules.
  Handoff completion leaves durable submission state untouched.

This review covers these new export/storage boundaries, not an independent audit
of the shared bus, UI or future transports. The existing B5 local-filesystem trust
model remains: a process able to replace trusted code/manifests is outside scope.

## Validation

- New diagnostic/security suites: **77 passed, 0 failed**.
- Related privacy/retention/handoff/checkpoint/recovery/submission suite:
  **340 passed, 1 skipped, 0 failed** (includes the earlier new tests; counts overlap).
- Place-literal policy checks: **7 passed, 0 failed**.
- Saved-page replay against the staged implementation: **616 saved pages,
  264 distinct, no interpretation differences**. The replay used the original
  checkout's existing recordings with this worktree's staged index; no raw
  recordings were copied or recaptured.
- Import-availability hook, Python compilation and `git diff --check`: passed.
- Affected handoff/shared-schema rerun after token/ID hardening: **41 passed,
  0 failed**; included again in the final 77-case run.

The full repository suite was not run locally. CI results are reported separately
on the draft PR. No test was weakened and no replay override was used.

All **16/16** requested failure classes have deterministic synthetic contract
tests verifying that the summary alone identifies stage, last verified success,
attempt, evidence, subsystem and investigation. Tests against the actual schema
separately verify its supported email/submission/handoff events and enum gaps.
Synthetic coverage is not a claim that missing runtime emit points are live.

## Remaining acceptance work

1. Shared schema owner reconciles taxonomy and missing typed events/transition
   fields, or owner explicitly accepts the documented narrower contract.
2. Runtime owner wires consumer lifetime and supplies unique run IDs, safe
   references, verification evidence, operation IDs and detailed retry facts.
3. Verify a live end-to-end run, including all requested emit points and callback
   overhead. No new application was run, submitted or resumed for this work.
4. Add authenticated remote handoff/push transport and a Postgres storage adapter
   if those deployment modes are required. These interfaces grant no authority.
