# Phase 0-A Independent Review

## Scope and method

I independently examined the runtime submission, verification, Gmail, credential, and model-authority paths in the repository without modifying production Python. This review specifically checked the paths that can lead to an application being sent, rate-limited, recovered, retried, or re-opened from email links, and compared them to the existing discovery report at [phase0-discovery.md](./phase0-discovery.md).

## Baseline verification status

- **Historical result: `UNVERIFIED HISTORICAL BASELINE`.** The discovery report's `1,938 passed / 0 failed / 3 skipped` claim has no original test output or environment artifact available for independent verification. It remains documented, but is not treated as an authoritative result.
- **New reproducible baseline:** pinned pristine SHA `6fb53e6c7a9bce394e67107c48fa5e47d1041e9b`, tracked tree clean, Python 3.13.13, Windows, fresh virtual environment, dependencies installed from `requirements.txt` and `requirements-dev.txt`. The repository CI specifies Python 3.13 but runs on Ubuntu with a PostgreSQL service; this local run does not reproduce those platform/service conditions.
- `python -m pytest -q -rs -n auto` produced `1 failed, 1836 passed, 104 skipped` in 1005.10 seconds. A second run under Python 3.13.13 reproduced the same summary and same failing test. Python 3.14.5 also produced that same summary and failure. Thus this is a reproducible baseline for the tested Python 3.13.13 Windows environment; it does not establish a fully CI-equivalent Ubuntu baseline.
- **Skip breakdown (104):**
  - 96 require the owner's `data/profile.json` or CI `PROFILE_JSON`.
  - 1 requires `RAISE_WINDOW_TEST=1` and an interactive visible Windows desktop.
  - 2 require a PostgreSQL service; in this run `POSTGRES_PASSWORD` was not set in `.env`.
  - 5 require local, ignored page recordings under `output/`.
- **Single failure:** `tests/test_safety_rules.py::test_a_previously_submitted_job_is_refused`; an isolated Python 3.13.13 run also fails.
  - The test creates an `IGT` record and a stub `Tracker.find_submitted()` that returns it for inputs containing `igt`, then inserts a fake module into `sys.modules["db"]`.
  - `apply.already_submitted()` calls `tracking.open_tracker()`, which selects SQLite when neither `TRACKER` nor `POSTGRES_PASSWORD` is set. The autouse state-isolation fixture redirects `config.DB_PATH` to a fresh per-test temporary database. `JobTracker.find_submitted()` therefore reads an empty isolated tracker and returns `None`; `already_submitted()` returns `False`.
  - `already_submitted()` returns `False` only when `find_submitted()` returns a falsy value; tracker exceptions instead become a `RuntimeError`. The tracker searches only rows with `status == "submitted"` and matches each row if either the normalized URL is equal or both stripped, case-insensitive company and title are equal. URL normalization lowercases and trims the URL, removes `utm_*`, `gh_src`, `src`, `source`, `ref`, `referrer`, and `trackingid` query parameters, strips a trailing path slash, and drops the fragment.
  - The test's fake `db` tracker is never selected. Its synthetic company's `"IGT"` and title `"Engineer NOC I"` would satisfy the company's/title match, but that `Record` never reaches the production finder. The job URL normalizes to `https://jobs.igt.com/igt/job/1`; URL/job-ID matching is not what rejects the expected fake. Location and `dedup_key` are not part of `find_submitted()` matching. This is a stale test double / committed test setup defect after a production tracker-abstraction refactor, not a demonstrated URL normalization, database model, or schema defect.
  - `git blame` shows the test and original direct-`db` implementation date to 2026-09-15. Commit `0a324317` on 2026-09-29 changed the production path to `tracking.open_tracker()` and added SQLite as the default absent Postgres configuration; the test still mocks `db`.
- The historical skip count differs by 101. The current run accounts for that difference with the 96 missing-profile skips and 5 missing-recording skips. Their presence in the historical environment is plausible but not verifiable from available historical artifacts. The two historical database skips were reported as connection timeouts, while the new local run skips because no `POSTGRES_PASSWORD` was configured. The historical `1,938-pass` result remains unreconciled at the evidence level.
- **CI-EQUIVALENT REPRODUCIBLE BASELINE:** the pinned commit was checked out cleanly in Ubuntu 24.04.5 with Python 3.13.13 and PostgreSQL 16.15, using the CI dependency-install and Playwright browser-install commands. The run used `PYTHONUTF8=1` and the CI database/user/password values; `POSTGRES_HOST=postgres` was the isolated Docker service alias rather than `127.0.0.1`. The CI-only profile secret was unavailable. The geo-data check passed. `python -m pytest -q -rs -n auto` yielded `1837 passed, 104 skipped in 395.02s (0:06:35)`, with no failures or errors. This replay ran in an isolated WSL2 Docker environment, not on an actual GitHub-hosted runner.
- The Linux skip breakdown is 96 profile-dependent tests, 1 visible-desktop opt-in test, 5 absent local recording tests, and 2 Windows-desktop-only tests. The PostgreSQL-backed deletion tests ran. Relative to the Windows/Python 3.13 result (`1 failed, 1836 passed, 104 skipped`), Linux removes the stale test failure and replaces the two database skips with two Windows-only skips, with the remaining count explained by the Windows tests actually running versus being skipped on Linux.
- **Safety test CI behavior:** `tests/test_safety_rules.py::test_a_previously_submitted_job_is_refused` passes in the Linux/PostgreSQL-configured environment. Since `POSTGRES_PASSWORD` is set, `tracking.chosen()` selects Postgres; `tracking.open_tracker()` imports the test's monkeypatched `db` module, so the stub tracker supplies the synthetic IGT record. The test does not query the actual service. Without Postgres configuration, `tracking.open_tracker()` chooses SQLite and bypasses that `db` stub, leading to an empty isolated SQLite database and the Windows failure. This confirms a backend-sensitive stale test stub/setup issue, not a production duplicate-matching failure.
- The Linux result still differs materially from the historical `1938 passed / 3 skipped` claim. Current CI-equivalent skips are 101 higher: 96 require owner profile data and 5 require local recordings, neither available in the replay. No evidence proves whether these inputs existed during the historical run; that result remains unverified.
- **Production code modified: NO.** No production code, tests, fixtures, or configuration were modified during this analysis. Only this review documentation and [phase0-discovery.md](./phase0-discovery.md) were updated.
- The earlier dirty-worktree syntax-error event remains documented as a separate incident: the unmatched brace was in an uncommitted edit, not in the pinned committed SHA. The pristine detached worktree at the pinned SHA was clean and compiled successfully.

## Independent findings

### 1) AGREE: final submission can bypass the explicit handoff gate

Evidence:
- In [page_agent.py](../../page_agent.py), `PageAgent.press_next()` computes `final` using submit-like labels and the step counter. The code explicitly says final pressure is allowed only when the step-counter logic says the page is final, or when the planner identifies a final submit; otherwise it still proceeds on the same path.
- The same function calls `submit_gate()` before a final submit is allowed, but the gate tests for CAPTCHA, legal conflicts, and attestations only; it does not require user sign-off or a separate review handoff.
- That means a stale or misleading step counter, or a page that labels a final action as a “next step,” can still turn into a direct browser click without the human review gate.

Classification: AGREE

Comparison to discovery report: This is a valid risk and matches the discovery report’s “intermediate / disguised submit” concern. The core issue is real.

### 2) AGREE: email verification links permit cross-tenant or cross-site matches via a weak hostname comparison

Evidence:
- In [emailed_codes.py](../../emailed_codes.py), `_site_of()` reduces a hostname to its last two labels, e.g. `myworkdayjobs.com` and `example.co.uk` are treated as a site in a coarse way.
- `verification_link_ok()` then checks only the same last-two-label site against the job URL, not a deeper same-host-tenant check for vendor-hosted ATSs.
- The same file and surrounding flow explicitly allow opening a verification link from Gmail when the host matches the same coarse site. This is a real trust boundary weakness for vendor-hosted employers and multi-tenant ATS deployments.

Classification: AGREE

Comparison to discovery report: The discovery report identified a version of this risk, and the underlying logic is confirmed.

### 3) AGREE: unknown post-submit results can lead to duplicate applications on a later run

Evidence:
- [apply_flow.py](../../apply_flow.py) calls `submit_verified()` and then treats a failed or ambiguous confirmation as a review-needed state, not as an explicitly confirmed success or an irrecoverable duplicate state.
- The duplicate guard in [db.py](../../db.py) only compares `status = submitted` records when it decides whether the same job was already applied to.
- That leaves an unknown post-submit result in a state that can be retried later without a confirmed submitted record being present.

Classification: AGREE

Comparison to discovery report: This was identified in principle and is borne out by the code paths.

### 4) DISCOVERY MISSED: the model receives the full candidate profile, including sensitive personal facts, and that material is not limited to the current question

Evidence:
- [config.py](../../config.py) defines sensitive profile fields such as disability status, ethnicity, gender, sexual orientation, and related fields as ordinary profile data.
- [page_agent.py](../../page_agent.py) builds a `facts` payload from the full profile for the planner and sends it to the model.
- [claude_integration.py](../../claude_integration.py) includes that user payload directly in the prompt sent to the LLM. This is a direct trust-boundary issue: the model is not restricted to only the current question; it receives the whole person profile.
- The project also logs answer values and writes forensic artifacts, which increases the exposure surface.

Classification: DISCOVERY MISSED

Comparison to discovery report: The discovery report describes agent submission safety and model-driven actions, but it does not call out the full-profile-to-LLM exposure path as a direct data-handling trust issue.

### 5) DISCOVERY UNDERSTATED: model-generated navigation is not origin-restricted and can result in untrusted page transitions

Evidence:
- [page_agent.py](../../page_agent.py) takes the model’s selected control and directly clicks it. That includes navigation-type controls when the planner identifies a link or a page advance.
- There is no check that the destination remains on the expected ATS origin, same tenant, or application flow. A malicious or compromised page can provide a plausible control with a dangerous destination.
- This is a more direct risk than a generic “AI action click”; it is a model-driven browser navigation privilege without destination validation.

Classification: DISCOVERY UNDERSTATED

Comparison to discovery report: The discovery report mentioned AI-driven click action, but it did not treat the absent origin validation and untrusted navigation path as a core trust boundary in its own right.

### 6) BASELINE RECONCILED: pinned-SHA results are reproducible; historical result remains unverified

Evidence:
- Pristine Windows/Python 3.13.13 execution at the pinned SHA reproduced `1 failed, 1836 passed, 104 skipped`; the failure is the backend-sensitive stale test double described above.
- The CI-equivalent Ubuntu/Python 3.13.13/PostgreSQL 16.15 replay at that SHA produced `1837 passed, 104 skipped`, with no failures; the safety test passes when PostgreSQL selection reaches its monkeypatched `db` module.
- The historical `1,938 passed / 3 skipped` report remains `UNVERIFIED HISTORICAL BASELINE`; its original output/environment are unavailable, and the 101 additional skips are explained by absent profile and local recording artifacts whose historical availability cannot be established.
- An earlier dirty-worktree run did encounter an unmatched brace in `page_agent.py`, but that was caused by an uncommitted edit in a separate worktree. The syntax error is not present in the pinned pristine SHA and does not explain either completed pinned-SHA result.

Classification: CI-EQUIVALENT REPRODUCIBLE BASELINE; historical claim UNVERIFIED

Comparison to discovery report: The Windows and Linux results differ because the stale test double follows different tracker backends, not because the pinned source tree is dirty. The historical result remains an evidence limitation, not a contradiction of the reproducible CI-equivalent baseline.

### 7) AGREE: the repo contains a real “unsupported resume claim” risk path

Evidence:
- [apply_flow.py](../../apply_flow.py) writes the tailored/resume text after a claim-checking loop; if the loop does not reduce unsupported claims, the content continues through the application material path.
- The detection logic is not a full fact-claim validator; it is a heuristic check for numeric or acronym patterns and similar unsupported content, which means some unsupported narrative claims can still be carried forward into the produced resume or application materials.

Classification: AGREE

Comparison to discovery report: This is a real concern in the application-material path, and the report’s emphasis on unsupported claims is valid.

## Comparison summary

- AGREE: The review is correct that the submit path and email verification logic are real risk areas.
- DISCOVERY MISSED: LLM exposure of the full profile and sensitive personal data is a trust-boundary issue absent from the discovery report.
- DISCOVERY UNDERSTATED: The lack of destination/origin validation on model-selected navigation is a stronger and more direct risk than described.
- BASELINE: The pinned SHA has a reproducible CI-equivalent Linux result (`1837 passed, 104 skipped`) and a reproducible Windows result (`1 failed, 1836 passed, 104 skipped`); the platform-sensitive failure is traced to the stale backend-specific test double. The separate historical `1,938 passed / 3 skipped` claim remains unverified. The earlier unmatched-brace event was limited to an uncommitted edit in another worktree.

## Recommendation

READY FOR HUMAN DISCOVERY APPROVAL

Caveats:
- The historical `1,938 passed / 3 skipped` run remains unverified.
- The CI-equivalent replay was not performed on a GitHub-hosted runner.
- The 101 additional skips relative to the historical claim depend on owner-profile and local-recording artifacts that were unavailable and cannot be confirmed historically.
- No production code was modified.
- Phase 0-B must not start until a human approves the discovery findings.
