# P0-B5 discovery (base a967976)

Continuation on `fix/privacy-diagnostics-hardening`; no tracked production edits
were present. Existing untracked B1 reviews and local settings are preserved.
The prior baseline was not recoverable: pytest cache contains stale failures,
not a completed result. The single clean baseline completed before implementation:
**2316 passed, 4 failed, 3 skipped, 0 errors**, 1496.00 seconds.
Exact failing node IDs (all pre-existing):

- tests/test_page_agent.py::test_a_whole_application_is_read_answered_and_submitted
- tests/test_page_agent.py::test_a_carried_over_no_to_sponsorship_is_corrected_from_the_profile
- tests/test_page_agent.py::test_a_submit_button_on_a_step_before_the_last_just_moves_on
- tests/test_page_agent.py::test_the_cover_letter_is_attached_where_the_form_asks_for_one

All fail waiting for the synthetic owner's navigation to `/done`. There were no
error node IDs. Skips: two unavailable database tests and the opt-in desktop test.

## Completed dashboard audit retained

`web_ui.py:816–852`: `/evidence` and `/log` already resolve paths and require
containment beneath output/ and logs/, respectively. This is **not a confirmed
path traversal defect**. `/document` serves DB document bytes with fixed types.
The defect is `.html` served as `text/html`, allowing raw employer script to run
on the dashboard origin; other output files, including legacy raw PNGs, are also
trusted without a privacy marker. Preserve containment and the document route.

`apply_flow.py:561`: post-submission cleanup removes top-level PNGs only.
`page_agent.py:6171`: recordings keep ten run folders, without an age limit.
Neither covers nested evidence, runs/ forensics, account failures, or JSON review
diagnostics. `config.py` has no general diagnostic retention setting.

## Writer audit

| Writer (base lines) | Trigger and content | Existing protection |
| --- | --- | --- |
| state_machine.py:127–204 | stall/loop/crash: PNG, raw page.content HTML, accessibility JSON, console JSON, metadata with reason and full URL | password-only perception masking for accessibility; no bounds for HTML/console |
| apply_flow.py:541–558 | pre-decision evidence: PNG, raw page.content HTML | none |
| browser_automation.py:7309–7372 | review: PNG, raw HTML, JSON with field/screening answers and validation | none |
| apply_flow.py:599–610 | stopped page PNG and aria text, full URL | hide_secrets masks passwords, not general PII |
| page_agent.py:1821–1838 | changed account state: PNG and snapshot with URL/state/reason | live snapshot password hiding only |
| browser_automation.py:4825–4844 | account creation refused: PNG and aria text in logs/account_failures | password hiding for text only |
| page_agent.py:5152 | dropdown failure: question, URL, DOM widget text | none |
| page_agent.py:6163–6178 | each read: accessibility text under pages/run | live password hiding; ten folders |
| apply_flow.py:957–959 | decision comparison and validation JSON | raw applicant comparisons/document filenames |
| apply_flow.py:1099,1299,1951 | confirmed submission PNG | none |
| safety.py:579 | sponsorship policy mismatch PNG | none |
| browser_automation.py:552–561 | console listener captures text and location | unbounded list, arbitrary strings |

`browser_automation.py:2111` screenshot bytes are live runtime visual reasoning,
not a persisted diagnostic; do not alter perception authority. `safety.py:564`
HTML fallback is in-memory policy detection. `page_agent.py:1458` and
`apply_flow.py:720` are live perception/recovery, not persisted copies.
`session_planner.py:89,123` writes request/response IPC for the explicitly selected
session planner, deletes completed requests, and is not a diagnostic export.
Profile/answer/checkpoint/login state writes and resume/cover-letter generation
are application materials/state, outside diagnostic cleanup.

Exact-format tests: test_phase4_features expects raw heading, console exception
text and reason; test_auto_submit expects comparison structure and paths;
test_page_recordings expects exact raw text. Those diagnostic expectations must
be updated to verify sanitized structure without weakening runtime tests.
test_secrets_out_of_snapshots_and_account_proof checks live perception separately;
its live email visibility must remain unchanged.

## Logging and event audit

`safety.py:808–865` redacts sk-ant keys, password/api-key/token/secret assignments,
email (retaining first two characters/domain), and phone (retaining final four
digits). It does not cover arbitrary name/address/answers, six-digit OTPs,
bearer/cookie/session/CSRF values, filename identity, query/fragment URLs, or
exception tracebacks. Installation filters the root and existing handlers plus
browser_automation, not every future handler/child logger.

High-risk value lines include page_agent.py:2788,3390,3433,3643,3658,4180,4202,
4686,4699,5101,5227,5354; browser_automation.py:866,869,1032,5551,5604;
apply_flow.py:839,886. Upload names: page_agent.py:3600,3733,6127;
browser_automation.py:4551; sites/successfactors.py:80. Workday logs selection
values/options at sites/workday.py:680–1192. Page shape/widget diagnostics log
live DOM and URLs at browser_automation.py:6013–6102. Exception strings are
widely logged across all audited modules, including Gmail/link failures.
OTP success messages themselves do not print the code, but exception/DOM/console
paths can. Verification link navigation remains authoritative in memory; URL
diagnostic copies need independent sanitation.

Both job_tracker.py:295 and db.py:202 accept unrestricted message/payload.
Production application event kinds are note (recovery/page/resume), auto_submit
(full comparison), action_outcome_unknown (entry/upload), action_validation_failed
(press/error count), handoff_required, resume_attached (filename), status_change.
B4 action/evidence metadata is useful and must remain. Submission safety events
already have strict enum schemas in job_tracker.py:318 and DB delegates to that
store: preserve them. Application records, saved answers and checkpoint identity
are authoritative state, not diagnostic URLs to rewrite.

## Migration boundary

Centralize persisted captures, bounded sanitized JSON/text, visual masking,
manifest verification, diagnostic routes, event copies and log filtering. Use
conservative structural projections where arbitrary text cannot be proven safe.
Do not mutate inputs, runtime snapshots, browser navigation, submission policy,
account rules or recovery semantics. Never write raw data on sanitizer failure.
Legacy artifacts require a new capture marker before exposure. Retention deletes
only recognized diagnostic files/directories, never a job folder or materials.
