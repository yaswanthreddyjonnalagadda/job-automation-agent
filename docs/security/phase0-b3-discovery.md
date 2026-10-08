# Phase 0-B3 Discovery — Durable Checkpoint & Recovery

Base: `codex/architecture-reliability` at `2a6189e` (P0-B1 + P0-B2 merged).
Branch: `fix/durable-checkpoint-recovery-hardening`.

This is read-only discovery, produced before any P0-B3 production change, per the
task's own requirement not to rebuild anything that already satisfies the need. It
answers the twelve questions the task posed, then two load-bearing findings that
shape the design: application identity is fragmented, and nothing resembling a
checkpoint exists in code today (only as an aspiration in
`docs/AGENT_ARCHITECTURE.md`).

## 1. What state survives a process restart today?

Durable, in a database row, keyed by `dedup_key` (`sha256` of the lower-cased
posting URL, `jd_analyzer.dedup_key_for_url()`):

- `applications`: `status`, `last_page_url`, `resume_path`, `cover_letter_path`,
  `notes`, timestamps (`job_tracker.py:76-90`, `db.py:67-84`).
- `documents`: the actual uploaded bytes + sha256, so a prior upload's identity
  can be checked without re-reading the filesystem (`job_tracker.py:93-106`).
- `application_events`: an append-only human-readable audit trail (`status`,
  `error`, `evidence`, `note`, `auto_submit`, …) (`job_tracker.py:108-118`).
- `form_answers` / `learned_form_recipes`: "has this question been answered
  before" caches, durable, both backends (`job_tracker.py:157-184`).
- `ats_accounts`: one row per employer — email, login host, method, last login
  (`job_tracker.py:148-155`).

Durable, SQLite-only regardless of configured tracker backend (`db.py:220-243`
delegates every one of these to a local `JobTracker`, a known, previously
documented residual assumption — see `phase0-b1-final-independent-review.md:202-
205,299`), keyed by a *different* identity, the "submission key"
(`jd_analyzer.submission_effect_key_for_url()`, strips only a narrow explicit
tracking-param list):

- `submission_effects`: `AUTHORIZED` / `DISPATCHED` / `CONFIRMED` / `UNCERTAIN`
  (`job_tracker.py:120-124,431-524`) — the P0-B1 exactly-once dispatch guard.
- `submission_identity_aliases`: a many-to-one merge graph so a login/SSO
  redirect host discovered mid-flow can be folded into the same submission
  identity (`job_tracker.py:125-131,327-429`).
- `submission_safety_events`: append-only, trigger-enforced
  (`job_tracker.py:132-146`).

Durable, in a flat JSON file (`data/_login_attempts.json`), keyed by
`host|email` (an account+site identity, *not* an application identity):

- `login_guard.py`'s rolling 24h attempt counters and sticky "hold" (§ below).

Not durable at all, anywhere, today: anything about *where in a multi-step form*
the agent was, which fields/sections were already verified, which repeated
entries exist, or whether an upload actually landed — none of that survives a
restart except by re-deriving it from the live DOM when the browser reopens.

## 2. What state exists only in PageAgent memory?

Every one of `_paused_state`, `written`, `history`, `_entries`, `_attached_here`,
`resume_uploaded`, `_created_at`, `_signed_in_at`, `_emailed_in`, `_code_tries`,
`_last_code`, `_pressed`, `_opened_entries`, `_woken`, `_shapes`,
`_pending_memories`, `owner_answers`, `corrected`, `account_blocker` — all
initialized in `PageAgent.__init__` (`page_agent.py:1276-1335`) and re-defaulted
by `_ensure_state()` (`page_agent.py:1404-1425`). `_ensure_state()` exists
specifically to make a **same-process hot code-reload** (`load_latest_code()`,
`apply_flow.py:1310-1348`, which reassigns `agent.__class__` on the still-living
object) safe when the reloaded class has new attributes — it is not, and was
never meant to be, a cross-process persistence mechanism. Confirmed by grep:
there is no `pickle`/`shelve` use anywhere in the repo, and a brand-new
`PageAgent(...)` is constructed from scratch on every `apply_flow.py` run
(`apply_flow.py:934-935`).

None of these attributes individually gates an irreversible action (the
irreversible-action gate is the durable `submission_effects` state machine,
entirely outside `PageAgent` — see §1/§11). The one attribute whose own comment
(`page_agent.py:1308-1310`) explicitly calls out that it is kept sticky *within
a run* specifically to avoid a second account-creation attempt is `_created_at`
— and it has zero persistence across an actual restart. A resumed process
instead re-derives "does an account exist" from the live page plus the durable,
narrower `account_on_record()` fact (§5/§12). This is the one PageAgent
attribute most worth a closer look for P0-B3, flagged by two independent
agents.

`owner_answers` and `_pending_memories` are the two attributes with a *partial*
durable echo: an owner-edited answer is durably mirrored via
`tracker.record_answer(..., answered_by="user")` the moment it's detected
(`page_agent.py:3998-4001`), and a pending (not-yet-verified) learned-answer
memory is simply dropped, never durably half-committed, if the process dies
before the next snapshot verifies it (`page_agent.py:5223-5268`).

## 3. What state exists only in browser/session memory?

- `SubmissionGuardV0`'s JS-side state (`window.__jaaSubmissionGuardV0`:
  `state.active`, `state.authorization`, `state.denials`) lives only in each
  page's JS heap, wiped on navigation/reload (`submission_guard.py:20-234`).
  This is a liveness concern the guard already repairs on frame
  attach/navigate (`submission_guard.py:293-309`), not a persistence one — the
  guard holds no authority of its own; see §11.
- The browser's **persistent context** (`launch_persistent_context(user_data_dir=
  ...)`, `browser_automation.py:456-457`, directory from
  `config.browser_profile_dir`) is the one thing that *does* survive a process
  restart without any application-level code doing anything — cookies and
  site-local storage persist on disk under that profile directory. This, not
  any in-app state, is most of why "resume" looks continuous today: the ATS
  site itself still recognizes the signed-in session and often still has its
  own server-side draft at the reopened URL.

## 4. What state is durable in SQLite/Postgres?

See §1's first two groups. Parity note: `JobTracker` (SQLite,
`job_tracker.py:243`) and `PostgresTracker` (`db.py:165`) are two independent,
duck-typed classes with no shared interface/ABC — callers frequently guard with
`hasattr(tracker, ...)` (`web_ui.py:521,556`; `apply_flow.py:673,1181,1480,1493,
1530`) rather than relying on a declared contract. `PostgresTracker` has **no**
method at all for `record_submission_identity_alias`/`submission_identity_group`
(absent from `db.py`); every submission-safety method it does expose
(`get_submission_effect_state`, `begin_submission_dispatch`,
`finish_submission_effect`, `record_submission_safety_event`,
`submission_safety_events`) delegates straight to a local SQLite `JobTracker`
(`db.py:220-243`). This was already an accepted, documented residual risk before
P0-B3 — not something this phase introduces.

## 5. What state is written to temporary signal/waiting files?

All under `data/`, named by `slug(f"{company}_{title}")` (`apply.py:85-86`,
truncated to 40 chars) — an identity axis of its own, unrelated to
`dedup_key`/`submission_key` (see "Application identity" below):

- `_signal_<key>.txt` — a one-shot decision (`continue`/`skip`/`refresh`/
  `reload_code`/`close`/`goto:<url>`), written by the dashboard or CLI, read
  and deleted by `_wait_for_signal` (`browser_automation.py:7426-7627`).
- `_waiting_<key>.txt` — presence-only marker, no state, written/removed by the
  *same* process in a `finally` (`browser_automation.py:7438-7450`) purely so
  the dashboard can show a Continue button. **Orphaned if the process dies
  before that `finally` runs.**
- `_job_<key>.json` — the parsed job dict, written once by `apply.py` before
  launching; never cleaned up by `clear_waiting_files()` (which only globs
  `_signal_*`/`_waiting_*`, `web_ui.py:289`) — accumulates indefinitely.

This exact staleness class has already caused real, catalogued failures:
`reference/failures/f043-waiting-note-outlives-its-run.json` (a killed run's
leftover waiting note kept showing a live Continue button after Stop) and
`f107-empty-continue-signal.json` (a signal file read before its writer
finished). Both were fixed for their specific symptom
(`clear_waiting_files()` + gating on `_running_url()`; poll for non-empty
content). The residual risk two agents independently flagged: because the key
is a lossy `company_title` slug rather than an owning process id, a stale
`_waiting_<key>.txt`/leftover `_signal_<key>.txt` for the *same* slug could in
principle still be present when a new run for the same company+title starts —
`apply.py` only unlinks its own key's **signal** file before spawning
(`apply.py:143-144`), not the waiting note or job file.

## 6. What state is reconstructed from `last_page_url`?

Only the URL string itself. `apply_flow.main()` reads
`existing.last_page_url` (gated through `worth_returning_to()`,
`apply_flow.py:575-588`, which just blacklists obvious non-application URL
fragments), passes it to a **cold** `assistant.open_job_page(resume_at)`
(`apply_flow.py:1552`), and if the reopened page no longer
`shows_the_application()` (a visible-form/posting check, not a URL check)
falls back to the plain job URL (`apply_flow.py:1557-1565`). Nothing else is
reconstructed — a brand-new `PageAgent` with every attribute from §2 at its
class default is what actually proceeds from there.

## 7. Which actions can currently be repeated after restart?

- **Sign-in attempts** — rate-limited/held by `login_guard.py`, which is
  disk-backed and keyed `host|email`, so this is already restart-safe
  independent of any in-memory `_signed_in_at`.
- **Verification-code reads** — same backstop, `login_guard.may_read_code` /
  `record_code_read`, 6 reads / 24h rolling window, restart-safe.
  (`login_guard.py:37,118-124,165-170`.)
- **Password-reset requests** — same backstop, `MAX_RESET_REQUESTS = 1` / 24h.
- **Document re-upload** — guarded redundantly by a live DOM check
  (`_file_already_attached`, checked *before* the in-memory `_attached_here`/
  `resume_uploaded` caches are consulted) — restart-safe by construction.
- **Wizard Next/Continue presses, field fills** — driven by re-reading the live
  DOM each pass (`_entries`, `history` are rebuilt from the current snapshot
  every time, not remembered) — generally restart-safe because nothing here is
  memory-driven in the first place.
- **Account creation** — *not* independently guarded by any durable,
  per-application "did I already click Create Account" fact. The only
  backstop is re-reading live account/page state after the fact plus the
  narrower, employer-scoped `account_on_record()` fact (§12). This is the one
  real gap in this list.

## 8. Which actions are non-idempotent?

- **Final submission** — already solved durably and exclusively by P0-B1 (§11).
- **Account creation** — pressing "Create Account" twice could genuinely create
  two accounts; no durable, application-scoped guard exists against this today
  (see §7's last bullet). This is a genuine, if narrow, gap for P0-B3 to close
  without inventing a new submission-style transaction system for it — a
  checkpoint recording "account creation was dispatched, outcome unknown" plus
  mandatory live reconciliation before any repeat attempt is the right shape,
  mirroring the §11 lifecycle at a smaller scale.
- **Repeated-entry Add/Save** — `_opened_entries`/`_pressed` are in-memory loop
  breakers only; a restart resets the attempt budget. Because `_entries` is
  rebuilt from the live DOM every pass, a *duplicate* add is a live-evidence
  question ("does this entry already exist on the page"), not a memory
  question — but today nothing explicitly re-checks that before a resumed run
  presses Add again. Flagged as an adversarial-matrix case (§33 of the task).

## 9. Which existing recovery paths already fail closed?

- The submission-effect state machine: any ambiguity converts `DISPATCHED` to
  `UNCERTAIN`, never silently assumed `CONFIRMED` (`job_tracker.py:495-524`,
  `apply_flow.py:1136-1159`).
- `login_guard`'s sticky hold: one rejected sign-in blocks *all* further
  attempts for that host+email, regardless of the 24h counter, until
  `owner_resumed()` is explicitly called (`login_guard.py:137-154,190-205`).
- `account_state.MFA_REQUIRED`/`LOCKED`: unconditionally terminal, no
  automatic retry path exists in the decision table at all
  (`account_state.py:296-306`).

## 10. Which existing recovery paths assume success from an attempted action?

None found that explicitly assume success — the one documented design
principle (`docs/security/phase0-b1-independent-review.md:17-34`) is the
opposite: ambiguity must resolve to `UNCERTAIN`, never to assumed success. The
one place an *absence* of a durable assumption-guard is itself the gap is
`_created_at` (§2/§7): its own comment says it exists to prevent a second
account-creation attempt, but that protection vanishes on restart because
nothing durable backs it.

## 11. How P0-B1 submission state must interact with recovery

`SubmissionGuardV0.submit_verified()` (`submission_guard.py:410-508`) is the
only method that can click a real final submit. `SubmissionProbe.inspect()`
(`submission_guard.py:511-541`) already exists as the **read-only**
reconciliation surface P0-B3 should reuse: it returns `{state,
application_status, url, title, confirmation_text, application_id,
safety_events}` without any interaction capability of its own. P0-B3's
checkpoint must only ever *read* submission-effect state through this surface
(or `tracker.get_submission_effect_state`) — never write it, never treat a
checkpoint field as permission to submit, and never add a second path to
`begin_submission_dispatch`. A checkpoint saying `READY_TO_SUBMIT` is evidence
about history, not an authorization.

Naming trap to avoid: `state_machine.py`'s `StateFingerprintCircuitBreaker`
(`IDLE`/`BLOCKED_VALIDATION_LOOP`) is a loop-detection breaker for a wizard
stuck re-showing the same page — entirely unrelated to
`AUTHORIZED/DISPATCHED/CONFIRMED/UNCERTAIN`. P0-B3 must not conflate the two
when naming its own checkpoint/reconciliation states.

## 12. How P0-B2 account/auth state must interact with recovery

`account_state.read_state()` takes only a snapshot string (plus an optional
password-box count) — never a prior/cached state — and is called fresh on
every page read, unconditionally, before any in-memory flag is even consulted
(`page_agent.py:1505-1508`). No code path anywhere lets a remembered belief
override a live CAPTCHA/MFA/sign-in read. The one durable fact actually
consulted, `account_on_record()` (`browser_automation.py:4715-4725`, via
`tracker.get_ats_account`), is scoped narrowly to "does an account exist for
this employer" — an identity/history fact that feeds `next_step()`'s choice of
*remedy* (e.g., whether an auto password-reset is authorized), and is never
substituted for the live `state.kind` itself.

P0-B3 must preserve this exactly: a checkpoint's recorded account state is
evidence only. Checkpoint `AUTHENTICATED` + live `SIGN_IN_REQUIRED` means
`SIGN_IN_REQUIRED`. Checkpoint `CODE_ENTRY` + live `MFA_REQUIRED` means
`MFA_REQUIRED`. `login_guard`'s durable per-account hold/limit already is the
correct restart-safe backstop for repeated sign-in/code-read/reset attempts;
P0-B3 must not duplicate or weaken it with a second, checkpoint-local counter.

## Two load-bearing findings for the design

### Application identity is fragmented across five schemes, not one

| Scheme | Computed as | Used by |
|---|---|---|
| `dedup_key` | `sha256(url.strip().lower())` (`jd_analyzer.py:53-57`) | the `applications` row itself — the DB's real primary identity |
| `submission_key` | `sha256(submission_identity_url(url))`, stripping a narrow tracking-param list (`jd_analyzer.py:89-125`) | `submission_effects`/`submission_safety_events` — deliberately *separate* from `dedup_key` per its own comment, because P0-B1 needed a replay identity sturdier than the raw URL hash |
| `submission_identity_aliases` | a many-to-one merge graph resolved transitively (`job_tracker.py:327-429`) | folding a login/SSO-redirect host into the same submission identity as a `submission_key` root |
| `slug(company_title)` | lossy, truncated, no hashing (`apply.py:85-86`) | signal/waiting/job files — collides across similarly-named jobs, diverges from both keys above |
| raw posting URL | the literal string | the dashboard's `_RUNS` live-run registry (`web_ui.py:68`) |

No single field is "the" application identity across all of these today — the
closest is `dedup_key`, but P0-B1 explicitly chose a *different* key for
replay-safety reasons. P0-B3's checkpoint identity (task §3) should anchor on
the `submission_key`/alias-group identity already established by P0-B1 (since
a checkpoint exists to protect against the same class of risk — acting twice
on one application) and record `dedup_key` alongside it for cross-reference,
rather than inventing a sixth scheme or attempting to unify all five, which
would be exactly the kind of submission-gateway/dashboard redesign the task
explicitly puts out of scope.

### Nothing resembling a checkpoint exists in code; it is pure aspiration today

Zero matches for `checkpoint` in any tracked `.py` file. The only occurrences
are in `docs/AGENT_ARCHITECTURE.md` (lines 214, 673-736, 794) — explicitly
labelled "the product contract and incremental architectural direction, not a
claim that every capability below is already implemented"
(`docs/AGENT_ARCHITECTURE.md:5`). Its own `# CHECKPOINTING` section already
names the intended fields and the intended resume algorithm ("inspect actual
browser state → compare with checkpoint → reconcile → continue from verified
state... do not simply reload the previously saved URL and assume it is
correct") — which is precisely what today's `last_page_url`-only resume does
instead, exactly the shortfall the architecture doc warns against. This
confirms P0-B3 is net-new construction, not a modification of an existing,
possibly-fragile mechanism — there is no prior checkpoint behavior to regress.

## Explicitly not rebuilt

Per the above, this phase reuses rather than rebuilds: the entire P0-B1
submission-effect state machine and `SubmissionProbe` (§11); `account_state`'s
always-fresh re-derivation and `login_guard`'s durable per-account holds
(§12); the live DOM re-check that already makes document re-upload and
repeated-entry rebuilding safe (§7); the browser's persistent profile
directory for session continuity (§3); and `last_page_url` itself, demoted
from "the resume point" to "a candidate place to inspect" (task §31) rather
than removed.
