# Last two job runs — observations, 9 October 2026

Recorded at the owner's request. The observations below describe the original failures; implementation and validation are recorded in the final section.

## LPL Financial — AVP, Network Security Engineer

- Run started at 4:13:41 p.m. America/New_York (20:13:41 UTC).
- Saved application status: `skipped`.
- Recorded reason: the posting excludes work authorization sponsorship now or in the future; the configured profile requires sponsorship.
- The sponsorship stop is expected behavior. Do not bypass it to apply.
- Dashboard run status is misleading: `failed (exit 1)` obscures the deliberate skip.
- Code path: `apply.run_one()` returns `4` for a sponsorship skip; `apply.main()` counts every nonzero result as a failure and returns `1`.
- Follow-up: preserve the skip outcome and its reason in the dashboard instead of presenting a generic failure.

## Chipotle — Senior Engineer, IT Security Engineering

- Run started at 4:15:55 p.m. America/New_York (20:15:55 UTC).
- At inspection, saved application status was `needs_user_review`; the worker remained `running` while waiting for intervention.
- The saved checkpoint and last-page address still pointed to the job posting, with no completed controls recorded.
- Recorded handoff reason: “final submission is only available through the verified gateway or human handoff.” Handoff began at approximately 4:18:18 p.m.
- `PageAgent.press_next()` emits that reason when the browser guard's denial count increases after a navigation click.
- Identified conflict: PageAgent distinguishes opening an application from final submission, while `SubmissionGuardV0` matches the shared submit-label pattern, which includes `Apply Now`, without that navigation distinction.
- Isolated reproduction using the current guard: a synthetic job posting containing an `Apply Now` link was clicked in a separate headless browser. The guard recorded `{"sequence": 1, "kind": "click", "label": "Apply Now"}` and the link's application-opening handler did not execute (`application_opened: false`).
- The live checkpoint supports this failure class; the exact live denied control label was omitted from the retained logs. The synthetic reproduction confirms the guard defect, rather than recovering the missing live label.
- Follow-up: distinguish verified application-entry navigation from final submission while retaining submission containment. Add regression coverage for entry links and true final-submit controls.
- Additional presentation issue: a worker waiting for owner intervention should be clearly shown as waiting rather than appearing to make progress under a generic `running` label.

## Evidence and limits

Read-only evidence inspected: `data/_runs.json`, SQLite application records/events/checkpoints in `data/applications.db`, the latest run logs, and the Chipotle stopped-page artifact. A read-only Windows accessibility observation confirmed the open Chipotle job-posting window.

Neither inspected application record contained submission confirmation. No application was launched, resumed, clicked, or submitted during this investigation; the guard reproduction used a separate synthetic page. No production code was changed for these findings.

The earlier update that Chipotle had reached the application form was corrected after inspecting the checkpoint: it remained on the posting. The dashboard rendering fix is separate from these findings.

Retained logs mostly say `private details omitted`, which limits retrospective diagnosis. Future diagnostics should retain safe reason codes, stage, and outcomes without exposing applicant data or credentials.

## Additional dashboard finding — false database health warning

- The owner also reported a database error in the UI. The live `/health` response returned HTTP 200 but its database check reported `ok: false` with `Database unavailable: name 'chosen' is not defined`.
- Application records remained readable. The error arose while constructing the health message after opening the tracker, because the tracker-selection helper was not imported. It was not evidence of database corruption or lost applications.
- Regression tests reproduced the false warning in both HTML and JSON. The targeted fix imports the existing centralized helper; positive and negative database-health tests cover an available synthetic SQLite tracker and an explicit tracker-opening failure.
- Failure record: `reference/failures/f135-dashboard-database-health-missing-import.json` on `fix/dashboard-cockpit-rendering`, alongside the earlier dashboard rendering correction.
- Local resolution verified: health JSON reports the database check healthy, and the rendered health page shows the connected message with no database warning or page errors. Dashboard-only reload preserved the waiting application worker and its run metadata. Commit `df32e27` is included in PR #14; no merge performed.

## Blue Mantis — confirmation detection and delayed browser closure

- The owner reported repeated Gmail opening despite a visible page confirmation, and the browser remaining open after their manual Submit click.
- Run started at 4:27:31 p.m. America/New_York. The application was recorded as `submitted` at 4:33:13 p.m., with confirmation-email evidence rather than on-page evidence. Email contents and applicant details are intentionally omitted from this note.
- The manual-review watcher calls `submission_confirmed()` first. That detector requires no visible Submit control and recognizes narrowly matched phrases in visible h1/h2/h3/alert/status regions, or a matching applied-job card. Confirmation wording in unsupported markup or wording can therefore be missed. The exact failed live condition cannot be recovered from the redacted logs or the now-closed page.
- After the form's Submit control disappears and page confirmation is not recognized, the watcher immediately permits an authorized Gmail fallback, repeating every 120 seconds if inconclusive. Each check creates a separate temporary tab, then closes it in `finally`. There is no initial page-settling grace interval in this manual-review fallback.
- Browser cleanup occurs after confirmation evidence ends the flow, not merely after a manual click. The email-confirmation delay therefore also delays automatic closure.
- Subsequent read-only inspection found no active agent browser window, and dashboard state reported `is_running: false` with run state `finished -- submitted`. This run eventually finished; the available evidence does not establish a separate teardown failure.
- Follow-up: reproduce the missed confirmation with supported, privacy-safe page structure; improve recognition while preserving false-positive protections, and allow the page time to settle before falling back to email. Keep page evidence ahead of email and stop all checks once submission is confirmed. No runtime code was changed in this investigation, and no application was resubmitted.

## Proposed post-submit waiting sequence

Recorded at the owner's request; these timings are a proposal, not implemented behavior.

- After an observed Submit action, watch the application page for 15–30 seconds while it settles and loads confirmation.
- If sufficiently clear page confirmation appears at any point, record submission, stop further confirmation checks, and close the agent's browser promptly. Do not wait out the interval or open Gmail once page evidence is sufficient.
- If page evidence remains inconclusive, allow 30–60 seconds from the Submit action before the first authorized Gmail confirmation check, giving the email time to arrive.
- Use limited email retries while continuing to prioritize page confirmation. Retry count, spacing and total timeout remain to be defined; these values were not agreed in this discussion.
- A delay or Submit click alone must never mark an application submitted. Without clear page, portal or matching email evidence, retain an uncertain outcome for review and do not resubmit automatically.
- The owner subsequently confirmed that Blue Mantis closed and its status updated. The proposed delay addresses unnecessary Gmail checks and slow completion, rather than an established failure to close at all.

## Baseten — false CAPTCHA stop on job-description wording

- The Security Engineer run started at 4:38:07 p.m. America/New_York and stopped at approximately 4:39:21 p.m. with a CAPTCHA handoff. The owner reported no CAPTCHA was visible.
- The saved posting contains the responsibility “automating security checks.” `safety.CAPTCHA_TEXT_RE` includes the unbounded phrase `security check`, and `safety.captcha_visible()` searches the full body text, so ordinary job-description wording triggers a CAPTCHA result.
- Isolated browser reproduction: a synthetic job posting containing that responsibility, with zero CAPTCHA frames, returned `captcha_visible_result: true` and matched `security check`. This confirms a false-positive failure class rather than a challenge the owner needs to solve.
- The checkpoint's generic CAPTCHA reason did not identify the matched text or distinguish frame evidence from body-text evidence, making the initial status misleading. My earlier instruction to complete a CAPTCHA was based on that recorded reason and is corrected by this investigation.
- Follow-up: require actual challenge context for ambiguous security-check wording; preserve detection of genuine verification prompts and CAPTCHA frames. Add regressions for job descriptions and real challenge UI. Apply the existing owner-approval rule for changes to `safety.py` when implementing the correction.
- No runtime code was changed or challenge bypassed in this investigation. The application was not submitted by this investigation; continuing with the same detector can repeat the false stop.

## Implementation follow-up

The owner requested implementation of the recorded fixes. Branch `fix/application-run-recovery` builds on the tested dashboard fixes in PR #14. Changes cover the false CAPTCHA match (f136), guarded application-entry GET navigation and social destinations (f137), page-first receipts and delayed/bounded email checks (f138), skip/waiting presentation (f139), and worker-visible cockpit Continue signals (f140).

The implemented email fallback uses a 45-second grace period and at most three checks at 120-second intervals. Actual page confirmation finishes immediately without waiting for email. Tests use synthetic pages and profiles; the exact Blue Mantis receipt markup was not retained, so coverage establishes the failure class rather than a verified replay of that receipt. Submitted applications must not be retried to test this change.

The standalone structured-diagnostics PR #13 has separate shared-schema/runtime-integration dependencies noted in its review. These runtime regressions do not claim to complete or merge that independent integration.

Validation before review: dashboard/recovery/source checks passed (91 passed, one skipped); related submission, safety, navigation and confirmation checks passed 279 cases with one skip, with their remaining backend-mock test passing after explicitly selecting the mocked backend. The failure-catalogue checks passed all ten checks for f136–f140. An owner-capture replay left all 607 pages / 259 distinct pages unchanged. Full-suite validation is in progress. No live application has been resumed or submitted to validate these changes.

Review follow-up: two direct PageAgent entry-path cases passed with the actual guard installed. Two browser tests first reproduced stale cockpit badges and passed after the polling controller began rendering the current stage and waiting state. The owner confirmed approval of the CAPTCHA correction in the conversation after the PR approval request. CI initially failed before checkout because of Docker Hub's shared pull limit; using Docker's public ECR PostgreSQL 16 mirror let the checks reach test execution. The local full suite was restarted with two workers after four workers left Windows with approximately 100 MB free physical memory; the application process tree was preserved.
