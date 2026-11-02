# Mutual of Enumclaw iCIMS investigation — 19 October 2026

The application incident remains unverified. Two resume defects were reproduced and patched in an isolated checkout; the live browser and Antigravity's runtime edits were not changed.

## Evidence and diagnosis

- Reviewed every field, option and error in the last six saved runs (ten page snapshots). Their substantive content is identical: Basic Information, step 1/3, with resume sources, first/middle/last name, email, login, password and confirmation, privacy link, and Submit Profile. No screening dropdowns or later application steps appear in those snapshots. These captures precede account filling and do not prove that fields were blank at submission.
- Earlier logs record an unconfirmed account-control click; later logs record an open form; the latest log (`run_20261019_043910.jsonl`) explicitly records a CAPTCHA appearing after the click. The latest checkpoint retains `account_creation_dispatched` and labels the stop OWNER_REVIEW. No application acceptance was observed.
- The owner reports that after completing CAPTCHA, the profile form stays open with no visible error. The saved post-stop screenshot is fully masked, and its text largely redacted. A current live DOM connection was unavailable. No final application was sent by this investigation.
- f202: account creation selects the iCIMS frame, while `apply_flow` passes the outer tab to the wait. Two local transition regressions timed out before the fix. The patched wait follows the adapter-selected frame, including replacement and same-URL progression, and does not interpret temporary frame disappearance as success. The explicit CAPTCHA reason from the account helper also enables CAPTCHA waiting when its outcome kind is `owner_needed`.
- f203: owner Continue clears the durable marker, but `_created_at` still causes the next CREATE_FORM decision to return FOR_OWNER. The regression failed before the fix. The patch clears only the matching memory hold after an explicit owner action and a verified successful checkpoint clear. Refresh, failed storage writes, unrelated hosts, and previously resolved creation history retain their existing protection/recovery behavior.
- A delayed site callback blocked by the submission guard is an unresolved hypothesis. Latest stored safety events show manual handoff was parked and no submission denial. No guard permissions or authorization lifetimes were expanded.
- f204: new `stopped_<timestamp>.state.json` and account-failure `.state.json` reports capture per-frame native validity flags, filled/required/disabled booleans, submit counts, alert counts, and known guard denial types. No input values, labels, URLs, password contents, or challenge-frame contents are exported. Reports use existing manifests and retention.

## Validation

- Latest combined authentication, handoff, account-state, diagnostics and retention batch: **135 passed, 1 skipped**. The skipped test requires Windows symlink capability.
- Earlier related gateway/CAPTCHA/recovery tests passed; the newly added handoff reason cases initially failed and passed after correction.
- Saved-page replay against `debdb46`: **643 saved pages, 382 distinct, zero differences**, using identical synthetic default profiles for both versions. This checks interpretation consistency, not live completion or the owner's actual answer correctness.
- No full-suite claim. No live employer POST, account retry, CAPTCHA interaction, or final submission was performed.

## Integration and next observation

Changes are uncommitted in `C:\Users\jonna\job-automation-agent-codex-icims`, based on `debdb46`; the shared branch and Antigravity's edits remain intact. The accompanying `icims-recovery.patch` contains the complete tracked change set plus new tests/catalogue entries and this report. Reconcile it with current work before applying; no cherry-pick hash exists.

After integration, the runtime should attempt the workflow. If it stays on the profile form after owner completion of CAPTCHA, inspect the new structural report for native invalid fields, disabled submit controls, and guard denial kinds. Observe the site reaching Candidate Profile / step 2 before concluding profile creation succeeded. An unchanged page and absence of an error are insufficient success evidence; do not clear uncertain actions or repeat account creation automatically.
