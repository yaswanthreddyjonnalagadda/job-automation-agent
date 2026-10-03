# Job Automation Agent — Codex Instructions

## Mission

Develop a reliable job-application agent that completes as much as possible autonomously using the user's profile, preferences, approved answers, authorized credentials, application state, deterministic rules, and AI reasoning. Human intervention should be the exception. Correctness and reliability take priority over new features.

## Coding agents, models, and cost

- Use one primary coding agent for normal debugging, bug fixes, features, portal/browser/authentication changes, tests, and targeted refactors. Do not spawn agents merely to explore the repository.
- Use at most one subagent when clearly justified by genuinely independent workstreams; more require a strong reason. Keep short or dependent steps in the primary agent. Avoid duplicate investigations, repeated context, and overlapping edits.
- When model/reasoning controls are supported, use the least expensive configuration that reliably fits the task: fast/cheap for mechanical work, balanced capable model with medium reasoning for normal engineering, stronger reasoning for difficult state/browser/authentication/architecture problems. Reserve the highest-capability model for justified escalation. Escalate instead of repeatedly failing with weak reasoning; do not use high reasoning for formatting, renaming, documentation, boilerplate, or obvious fixes.
- Read only relevant files; do not explore the entire repository or reread unchanged large files by default. Prefer targeted inspection → targeted change → targeted tests → broader verification when justified. Use deterministic logic when sufficient and avoid duplicate AI calls.

## Development and branches

Before editing, inspect the branch, working-tree changes, relevant recent work, and existing implementation. Preserve unfinished and unrelated work. Make the smallest correct change, preserve working behavior, and avoid unrelated refactors. Report unrelated issues unless they block correctness or safety. A roadmap is context, not a task queue.

Architecture/reliability work currently belongs on `codex/architecture-reliability`. Do not silently move work between branches, create unnecessary PRs, or merge to `main` without an explicit request. Keep commits logically scoped and reviewable; leave merging to the owner unless requested.

Retain the engineering constraints in `CLAUDE.md`: fix the failure class rather than one site/value; record production bugs in `reference/failures/` with real regression references; keep place data in `reference/geo.json` and user facts in the profile; use existing centralized policy and preserve owner-entered values. Changes to `safety.py` require explicit owner approval on the PR. Update `BEHAVIOUR.md` for runtime behavior changes. Future explicit user instructions take precedence over repository guidance.

## Runtime contract: read the relevant sections

For application answers, browser behavior, accounts, login, verification, OTP/MFA/CAPTCHA, recovery, checkpoints, submission, portal adapters, privacy, runtime AI, or application state, read the relevant portions of [docs/AGENT_ARCHITECTURE.md](docs/AGENT_ARCHITECTURE.md):

- Answers and field requirements: §§3–8, 50.
- Browser actions, verification, submission, adapters: §§9–10, 31–35, 41–42.
- Accounts, authentication, credentials, sessions: §§11–31.
- Checkpoints, recovery, handoff: §§33, 36–40.
- Testing, privacy, diagnostics, metrics, AI cost: §§43–49.
- Coding-agent orchestration and model escalation: §§51–52.

Do not load the whole document for every trivial change. It describes both current constraints and incremental targets; do not begin a large refactor merely to satisfy the specification.

Resolve application information in this order: structured profile → explicit preference → approved saved answer → deterministic rule → application context → AI interpretation → user intervention. Never fabricate personal information or replace an approved answer with an AI guess. Do not ask when a reliable answer exists. Optional fields without supported answers normally remain blank instead of blocking progress. Apply existing consent, credential, and submission gates.

## Testing and communication

For a bug: reproduce when practical → add regression coverage → implement the smallest correct fix → run targeted tests → related tests → full suite when justified. Do not rerun the full suite after every small edit. Retain repository hooks and inspect replay differences; replay consistency alone does not prove correctness. Prefer synthetic profiles and verify observed outcomes.

During meaningful work, report failures, root causes, significant findings/assumptions, unexpected test results, and scope changes without narrating every command. At completion report the problem, cause, changes/files, tests and pass/fail results, unverified behavior, and commit status/hash. Never claim verification that did not happen.
