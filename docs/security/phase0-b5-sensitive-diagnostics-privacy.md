# P0-B5: sensitive diagnostics and privacy

Branch: `fix/privacy-diagnostics-hardening`, base `a967976`. This implements B5
only; no merge, B6, main integration, new adapters, or authority redesign.
Discovery was completed before production changes in `phase0-b5-discovery.md`.
Unrelated local reviews/settings were preserved.

## Data classification and discovery

Diagnostic copies are distinct from authoritative application data. Credentials,
OTP/link/channel authority, profiles, approved answers, applicant documents,
application records, session-planner IPC and recovery checkpoints keep their
existing gates. B5 does not sanitize a live navigation URL or PageAgent snapshot.
It sanitizes the copies written for debugging or events.

Raw writers covered: forensic failures; pre-decision evidence; review packages;
comparison/validation JSON; stopped/account pages; dropdown dumps; page recordings;
submission confirmation images; sponsorship mismatch images; console exports;
normal/structured logs and dashboard worker stdout/stderr. The source audit below
classifies the remaining source occurrences individually.

The old `/evidence` and `/log` routes already used `Path.resolve()` and containment
beneath output/ and logs/. No traversal vulnerability is claimed. The confirmed
defect was executable `.html` serving and raw content exposure. `/document` remains
unchanged: authoritative stored resume/cover-letter bytes with fixed content types.

## Central privacy layer

`diagnostics.py` contains context/pattern text redaction, URL copies, structural
DOM/accessibility/JSON projections, screenshot covering, bounded console exports,
safe writers, manifests, verified reads, log/event projections and retention.
Sanitization/capture errors omit artifacts. No raw fallback exists.

Arbitrary text is not made trustworthy by a regex. Persisted page text and JSON
therefore use conservative structural projections. Only exact generic field/
navigation labels, roles, required/disabled/checked state, counts and safe enum
metadata survive. Unknown headings, validation details and screening answers
become placeholders. Boolean/numeric answer values are masked too.

### Text, logs and URLs

Text copies remove known values supplied through `PrivacyContext`, complete email/
phone values, auth-context OTP/passcodes, bearer/API/session/cookie/CSRF secrets,
document filenames and street-address patterns. This supplements the existing
`safety.redact()` filters without changing their policy or implementation.

Runtime log records are sanitized before handlers, including subsequently added
handlers. Source-literal templates may remain; nonnumeric interpolated values and
exception bodies/tracebacks are omitted. Dynamic templates are not trusted.
48 value-bearing info calls were replaced with operation messages. Unstructured
worker stdout/stderr is projected before disk writes to safe operation categories;
it retains source identity, not local directory/interpreter paths. JSON run logs
are bounded and manifest-marked. The public runtime identity endpoint remains
unchanged because its source/checkout comparison is an existing authority guard.

URL copies keep scheme/host and a small set of safe path segments. Unknown path
segments, query and fragment contents are masked; userinfo is discarded. Example:
`https://example.invalid/verify?token=abc#secret` becomes
`https://example.invalid/verify?<redacted>#<redacted>`. Runtime URLs are untouched.

### HTML/DOM and accessibility

DOM exports strip all input values, textarea/select/contenteditable contents,
uploaded paths, arbitrary text and attributes. Script, style, template, bootstrap
JSON, metadata, srcdoc, embedded documents/media and event handlers are removed.
No href/src or javascript URL survives. Structural tags, known labels, roles and
boolean states remain. HTML is source for inspection, never an executable page.

Saved aria/page text preserves roles/known names and states; values and untrusted
free-text lines are omitted. Live perception, parsing, grouping, approved answer
selection and recovery evidence are unchanged. The ten-run recording cap remains.
New recordings are intentionally less detailed than the legacy replay corpus.

### Screenshot masking

Capture installs a temporary visual stylesheet and an opaque document-sized
cover. It checks geometry and installation before capture, uses Playwright's
native mask over that cover, checks it again, and removes temporary nodes in
`finally`. It changes no values, dispatches no input/change events and performs no
control click or navigation. Failures omit the screenshot.

Masking is deliberately conservative: screenshots retain geometry, not readable
pixels. This covers visible personal text, controls, demographic/legal answers,
document previews, canvases, closed/open shadow roots, frames and CSS-rendered
content. A pixel test verifies the entire image is opaque, without OCR. Structural
DOM/accessibility exports provide the useful diagnostic detail. The live visual
reasoner's in-memory screenshot remains outside persisted diagnostics.

### Console and events

Console messages are untrusted: only type and a fixed exception category survive,
never message bodies, location URLs or arbitrary objects. The listener and export
retain at most the latest 200 entries. This is stricter than 2 KiB/entry and a
512 KiB total budget.

SQLite and Postgres application events use the same safe projection on writes
and reads. Stable kinds, action/target/evidence/result/stage/category enums,
eligibility, step/error counts and unapproved/mismatched-field counts remain.
Full questions/answers, filenames, exceptions, URLs and comparison reasons do not.
Evidence paths require verified B5 artifacts. Existing submission safety events
already enforce strict enum schemas and are unchanged. B4 handoff/checkpoint
authority stays precise; only diagnostic copies lose private details.

## Viewer, manifest and legacy policy

`/evidence` and `/log` retain resolved-root containment. Types are allowlisted;
escape paths, unsupported types, outside roots and unmarked artifacts fail closed.
HTML is `text/plain`, with `nosniff` and CSP `default-src 'none'; script-src 'none';
sandbox`. PNG uses image/png. The viewer serves the same bounded bytes whose hash
it verified, avoiding a separate unchecked reread. Legacy validation reports are
also withheld from dashboard diagnostics.

Every centralized capture directory receives `manifest.json`: schema_version,
capture_version=5, UTC created_at, fixed reason_code, hashed application/directory
identity, sanitization_status and artifact-name entries with SHA-256/truncated.
No raw reason, query, applicant answer or document path is included. Hashes bind
the marker to actual bytes. The marker is a local provenance boundary, not a
signature against a hostile local account that can rewrite both files.

Legacy raw screenshots/HTML are not grandfathered in, converted by OCR or treated
as safe. They are withheld from trusted viewers and eventually cleaned under
retention. No historical artifact migration or application-record migration runs.

## Limits and retention

| Artifact | Maximum |
| --- | --- |
| DOM source | 2 MiB |
| Text/accessibility/JSON/run logs | 1 MiB |
| Console | 512 KiB, latest 200 category-only entries |
| PNG | 16 MiB; larger capture omitted |
| Manifest | 64 KiB, 500 artifact entries |

Only sanitized output is truncated. JSON stays valid by replacing oversized
output with truncation metadata; manifests record truncation. Raw data is never
used after a failure or overflow.

`DIAGNOSTIC_RETENTION_DAYS`: default 7, accepted integer range 0..30, invalid input
uses 7. Zero retains current-use artifacts until the next cleanup boundary, with
cleanup when the dashboard worker finishes. Normal runtime/dashboard startup also
cleans expired diagnostics. Cleanup visits only fixed output/, runs/, logs/ roots,
known job evidence/step/account/page-run subtrees and diagnostic filenames, with a
10,000-entry budget. Symlinks and NTFS junctions are not followed. Only empty
diagnostic directories are removed; no recursive deletion of job/material folders.
Documents, DBs, profiles/config, checkpoints, source materials, application records
and unrelated files are protected even inside a diagnostic directory.

## Privacy sentinel matrix and regression coverage

Only synthetic values are used: name, email, phone, address, password, OTP, JWT-like
token, resume text, sensitive answer and identity-bearing filename. They appear in
form controls, hidden fields, editable content, visible text, bootstrap script,
console, URL copies, events, exceptions and captured logs.

| Boundary | Verification |
| --- | --- |
| DOM/text/JSON | no sentinel; scripts/active attributes absent; role/label/state retained |
| Capture failure | no raw fallback file |
| Screenshot | unchanged values, no input/change events, restored nodes, continued filling |
| Complex visual content | full-image opaque pixels, frames/shadows/canvas/overflow |
| Console | sentinel-free, bounded count/bytes, no arbitrary object serialization |
| URL | query/fragment masked; original navigation copy unchanged |
| Logs/events | sentinels absent; both trackers preserve action/evidence metadata |
| Viewer | plain-text safe HTML, manifest/hash validation, legacy rejection |
| Paths | traversal, absolute/outside root, unsupported extension, symlink/junction escape |
| Retention | old removed/new retained, zero/default bounds, material and outside-link protection |

Existing raw-format artifact tests were updated to assert sanitized structure and
privacy, while retaining runtime submission/status checks. Tests of live
password/email perception were left intact. A temporary affected-test run also
tripped the existing dashboard stale-source guard because source changed while
the process ran; source was held fixed for the subsequent run.

## B1–B4 preservation and validation

B1 dispatch/gateway, capability and AUTHORIZED/DISPATCHED/CONFIRMED/UNCERTAIN code
were not changed. B2 account state, channel gates, CAPTCHA/MFA/OTP authority and
credential policy were not changed. B3 reconciliation, identity and sticky merge/
creation lifecycle were not changed. B4 write verification and human handoff
remain intact. The only safety.py edit delegates its policy-mismatch diagnostic
screenshot; the owner must approve that small change when reviewing a PR.

Clean baseline (one run): **2316 passed, 4 failed, 3 skipped, 0 errors**, 1496.00s.
Exact baseline failures:

- tests/test_page_agent.py::test_a_whole_application_is_read_answered_and_submitted
- tests/test_page_agent.py::test_a_carried_over_no_to_sponsorship_is_corrected_from_the_profile
- tests/test_page_agent.py::test_a_submit_button_on_a_step_before_the_last_just_moves_on
- tests/test_page_agent.py::test_the_cover_letter_is_attached_where_the_form_asks_for_one

No baseline error IDs. Two skips are unavailable DB tests, one an opt-in desktop
test. Initial B5 Windows run: **28 passed, 3 skipped** (symlink privilege).
Supplementary Linux path/retention run: **16 passed**, including actual symlinks;
third-party plugin auto-loading was disabled after a Windows-only Hypothesis
native dependency prevented that runner's summary (all 16 cases had run).
Windows junction coverage was added separately.

Focused B5 Windows tests: **34 passed, 3 skipped**, 74.69s after the final
retention tightening (previous run: 40.42s); file symlinks
require Windows privilege, while NTFS junction tests passed. Supplementary Linux
viewer/retention tests: **19 passed**, 6.27s after the final retention tightening
(previous run: 11.25s), including actual symlink cases.
Directly affected runtime/viewer tests: **148 passed, 3 skipped**, 111.01s.

B4 (action/navigation/handoff/adapter verification): **93 passed**, 121.95s.
The first concurrent run had one setter-fallback timeout (92 passed); that case
passed alone and the entire group passed with two workers. B3 (checkpoint,
reconciliation, resume): **60 passed**, 47.12s.

B2 initial authentication regression: **400 passed, 2 failed**, 295.48s. One
artifact-format expectation needed the new manifest added to its assertion;
the updated test also verifies both account artifacts' B5 provenance. The other
failure followed an intermittent sign-in-state file write failure; no login-guard
authority code changed. Both affected files then passed: **42 passed**, 63.88s.
Thus all 402 selected B2 cases have passed, with the 400 initial successes and
the focused confirmation covering both initial failures.

B1 plus failure-catalogue/source hygiene: **646 passed, 1 failed**, 326.96s.
All submission-authority tests passed. The hygiene guard interpreted the HTML
`meta` tag literal as an ISO subdivision name; the tag vocabulary now uses the
same space-delimited representation as other structural vocabularies. No place
data or guard exception was introduced. DOM/privacy and geographic-guard
confirmation: **19 passed**, 19.09s.
First final full suite: **2354 passed, 7 failed, 6 skipped, 0 errors**, 1069.82s.
The four baseline failure IDs were unchanged. Additional failures were:

- tests/test_emailed_code_rule.py::test_the_reader_stops_at_the_limit
- tests/test_gemini_answers.py::test_a_key_that_does_not_look_like_a_google_key_is_said_so_without_being_shown
- tests/test_page_agent.py::test_an_upload_is_recorded_for_later_runs

Focused reproduction: **1 passed, 2 failed**, 18.89s. The emailed-code-limit case
passed; its full-suite trace had a failed test-state file write, as in the earlier
B2 transient. No channel/limit authority changed. The two reproducible failures
were diagnostic expectations: Gemini's dynamic format guidance was omitted by
the privacy filter, and the mock tracker expected a personal resume filename.
The Gemini warning now uses a static, secret-free format hint; key validation and
AI behavior are untouched. The upload test expects the safe event category and
asserts the filename is absent. Related confirmation (all emailed-code rules,
Gemini-answer tests, private upload event and B5 text/log/event projections):
**130 passed**, 100.68s. The document's allowed single final full-suite
confirmation used `-n auto` and `PYTEST_XDIST_AUTO_NUM_WORKERS=4` to reduce Windows
I/O pressure: **2352 passed, 4 failed, 11 skipped, 0 errors**, 1074.04s.
The exact four failing node IDs match the baseline list above; no new failing or
error IDs remain. All production files were held fixed during confirmation.
Final saved-page replay ran once through the existing commit hook: **430 saved
pages, 231 distinct, zero differences**. Imports and replay hooks passed without
bypass or replay override. This is reduced coverage compared with the previous
phase's 1270-page reference; it does not replace privacy sentinel tests.

The eleven skips comprise the two unavailable DB cases, one opt-in desktop case,
three Windows file-symlink cases (covered on Linux), and five missing historical
recording fixtures. These five cases passed in the first final full suite, before
startup retention removed their old source recordings. The missing recordings
are OCC page_53/page_02/page_08, WinChoice page_09, and Rubrik page_01 named by
tests/test_page_agent.py. The confirmation therefore has reduced historical
fixture coverage, not an unexplained new passing-test decrease.

A final retention review found that the initial account-directory cleanup rule
accepted every `.txt`/`.png` filename. Expanded coverage reproduced deletion of
an unrelated `account/resume.txt`. Cleanup now reuses the recognized diagnostic
filename policy; the regression checks account-folder resume/cover-letter
preservation alongside deletion of an expired, named account-state diagnostic.

## Closure correction: a retention-cleanup defect deleted real recordings

Independent pre-merge verification (2026-10-08, after the run above) found that
`cleanup_expired_diagnostics()`'s traversal entered every job's `pages/` folder and
matched files there by the `page_\d+\.txt` filename pattern alone. That pattern is
shared by two unrelated things: the new, run-scoped, genuinely ephemeral snapshots
`PageAgent._save()` writes under a timestamped subfolder (already retained/pruned by
the separate, pre-existing, count-based `page_agent.keep_latest_runs()`), and this
project's old-style, flat, **permanent** real-application-page recordings that
`replay_guard.py` and `tests/test_page_agent.py` depend on. A filename-only match
cannot tell the two apart, so the first production/dashboard-startup cleanup pass
against the real `output/` tree deleted the flat recordings once they aged past the
retention cutoff.

Timeline evidence places this before, not during, the verification above: 51 of the
repository's `output/<job>/pages/` directories are now entirely empty (no files, no
subfolders), with mtimes clustered in an 11.6-second window at 19:01:55-19:02:07 on
2026-10-08, roughly 40 minutes before the `295d8eb` commit that introduced the defect
(19:42:37) and well before this correction's own test runs. A further 14 directories
hold only run-scoped subfolders and were not necessarily affected (they may never
have held flat recordings). `output/` is listed in `.gitignore`, confirmed via
`git check-ignore -v`, so none of the 51 can be recovered from git history. This is
an already-occurred, irreversible loss of real application-page data, not a
hypothetical risk.

The fix removes `pages/` entirely from the set of directory names
`cleanup_expired_diagnostics()` will descend into, at any depth, under any retention
setting -- the function now never looks inside any `pages/` folder, so it can neither
delete a flat recording nor duplicate `keep_latest_runs()`'s own handling of
run-scoped subfolders. Two new regression tests
(`tests/test_diagnostic_retention.py::test_flat_page_recordings_in_pages_are_never_deleted_however_old`
and `::test_run_scoped_page_snapshots_under_pages_are_also_left_to_keep_latest_runs`)
assert both shapes survive unconditionally, including at `days=0`, the most
aggressive setting; both would have failed against the pre-fix code. A failure-catalogue
entry, `reference/failures/f122-retention-cleanup-entered-pages-and-deleted-real-recordings.json`,
records the defect class per the project's "fix the class, not the instance" rule.

Consequences that cannot be undone by this fix: the five historical-recording skips
already noted above (OCC page_53/page_02/page_08, WinChoice page_09, Rubrik page_01)
remain skipped in every subsequent run -- confirmed by re-running
`tests/test_page_agent.py` after the fix: the same 4 baseline failures, 92 passed,
and the same 5 skips, unchanged from before the fix, because the fix stops future
deletion but cannot resurrect what is already gone.

Post-fix validation (run after the fix above, in this order): focused B5 tests --
**36 passed, 3 skipped** (same Windows symlink-privilege skips as before); B1-B4
regression (`test_auto_submit.py`, `test_account_step.py`, `test_phase4_features.py`,
`test_page_recordings.py`, `test_visible_desktop.py`) -- **74 passed**; full
`test_page_agent.py` -- **4 failed, 92 passed, 5 skipped** (unchanged, as above); one
final full suite (`-n auto`, `PYTEST_XDIST_AUTO_NUM_WORKERS=4`) -- **4 failed, 2356
passed, 11 skipped**, exactly the same four baseline node IDs, no new failing or
error IDs; one final replay through the actual pre-commit hook (staged fix against
`295d8eb`) -- **446 saved pages, 231 distinct, read the same before and after** --
zero differences, confirming the fix changes no question's reading, grouping or
answer. The replay's page count differs from the 430 recorded in the run above it
because real application activity between the two checks added new recordings; it
does not indicate further loss.

## Residual limitations

No live employer/application submission was performed. Screenshot/console/text
exports intentionally lose visual and free-text detail; review answers in the live
browser or authoritative application records. Local users with write access can
forge manifests; this is not a credential vault or multi-user trust system.
Legacy private artifacts remain on disk until authorized diagnostic retention
removes them. Runtime IPC and authoritative profile/documents/checkpoints retain
their existing privacy/authority model. Real Postgres server coverage is limited
by local availability; the event-boundary parity regression uses a synthetic DB
connection. The baseline's four submission-fixture failures are outside B5.

Final review found a test-isolation interaction: the pre-existing visible-desktop
startup tests call `web_ui.serve()`, which now includes retention. I initially
missed isolating those tests, so they ran cleanup against workspace diagnostics.
Both tests now point BASE_DIR at temporary directories. Direct startup/retention
verification: **23 passed, 1 skipped**, 31.98s (Windows file-symlink privilege;
already covered on Linux). This was a test-only isolation fix after full-suite
confirmation; production code did not change. The final replay found 430 saved
pages (231 distinct), compared with the prior phase's
1270-page reference. A pre-cleanup corpus count was not recorded; the five
recording-fixture skips confirm older diagnostic sources were pruned during
those tests. Missing historical diagnostic
pages cannot be assumed available for replay. Report actual surviving-corpus
coverage and preserve it; do not claim the prior 1270-page coverage.

## Post-implementation source audit

The audit below includes each occurrence of the requested production search
terms. Comments/schema/perception mentions are classified too; TEST-ONLY matches
are confined to tests/ and excluded from the production table. Source/replay
tooling is not a runtime diagnostic capture and is classified separately.

<!-- SOURCE_AUDIT -->

| Source occurrence | Search match | Classification | Rationale |
| --- | --- | --- | --- |
| `ai_models.py:107` | write_text( | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `apply.py:144` | write_text( | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `apply_flow.py:1043` | console_logs, dump_forensic_failure | SAFE CENTRALIZED | Safe capture/event copy or reference to that capture boundary |
| `apply_flow.py:1076` | screenshot( | SAFE CENTRALIZED | Safe capture/event copy or reference to that capture boundary |
| `apply_flow.py:1092` | console_logs, dump_forensic_failure | SAFE CENTRALIZED | Safe capture/event copy or reference to that capture boundary |
| `apply_flow.py:1276` | screenshot( | SAFE CENTRALIZED | Safe capture/event copy or reference to that capture boundary |
| `apply_flow.py:1847` | save_review_package | SAFE CENTRALIZED | Safe capture/event copy or reference to that capture boundary |
| `apply_flow.py:1929` | screenshot( | SAFE CENTRALIZED | Safe capture/event copy or reference to that capture boundary |
| `apply_flow.py:263` | write_bytes( | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `apply_flow.py:328` | write_text( | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `apply_flow.py:425` | write_text( | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `apply_flow.py:463` | write_text( | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `apply_flow.py:532` | collect_evidence | SAFE CENTRALIZED | Safe capture/event copy or reference to that capture boundary |
| `apply_flow.py:583` | screenshot( | SAFE CENTRALIZED | Safe capture/event copy or reference to that capture boundary |
| `apply_flow.py:585` | aria_snapshot | SAFE CENTRALIZED | Safe capture/event copy or reference to that capture boundary |
| `apply_flow.py:67` | dump_forensic_failure | SAFE CENTRALIZED | Safe capture/event copy or reference to that capture boundary |
| `apply_flow.py:698` | aria_snapshot | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `apply_flow.py:903` | collect_evidence | SAFE CENTRALIZED | Safe capture/event copy or reference to that capture boundary |
| `apply_flow.py:940` | screenshot_path | SAFE CENTRALIZED | Safe capture/event copy or reference to that capture boundary |
| `apply_flow.py:941` | html_path | SAFE CENTRALIZED | Safe capture/event copy or reference to that capture boundary |
| `browser_automation.py:2112` | screenshot( | NON-DIAGNOSTIC | In-memory runtime visual reasoning, no persisted image |
| `browser_automation.py:3186` | /evidence | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `browser_automation.py:4829` | accessibility | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `browser_automation.py:4840` | screenshot( | SAFE CENTRALIZED | Safe capture, bounded console/event copy or sanitized review |
| `browser_automation.py:4842` | aria_snapshot | SAFE CENTRALIZED | Safe capture, bounded console/event copy or sanitized review |
| `browser_automation.py:490` | dump_forensic_failure | SAFE CENTRALIZED | Safe capture, bounded console/event copy or sanitized review |
| `browser_automation.py:491` | console_logs | SAFE CENTRALIZED | Safe capture, bounded console/event copy or sanitized review |
| `browser_automation.py:492` | console_logs, dump_forensic_failure | SAFE CENTRALIZED | Safe capture, bounded console/event copy or sanitized review |
| `browser_automation.py:554` | console_logs | SAFE CENTRALIZED | Safe capture, bounded console/event copy or sanitized review |
| `browser_automation.py:555` | console_logs | SAFE CENTRALIZED | Safe capture, bounded console/event copy or sanitized review |
| `browser_automation.py:558` | console_logs | SAFE CENTRALIZED | Safe capture, bounded console/event copy or sanitized review |
| `browser_automation.py:561` | console_logs | SAFE CENTRALIZED | Safe capture, bounded console/event copy or sanitized review |
| `browser_automation.py:6091` | outerHTML | SAFE CENTRALIZED | Widget log argument omitted by central log factory before handlers |
| `browser_automation.py:6170` | console_logs | SAFE CENTRALIZED | Safe capture, bounded console/event copy or sanitized review |
| `browser_automation.py:7313` | save_review_package | SAFE CENTRALIZED | Safe capture, bounded console/event copy or sanitized review |
| `browser_automation.py:7329` | screenshot_path | SAFE CENTRALIZED | Safe capture, bounded console/event copy or sanitized review |
| `browser_automation.py:7331` | screenshot(, screenshot_path | SAFE CENTRALIZED | Safe capture, bounded console/event copy or sanitized review |
| `browser_automation.py:7363` | screenshot_path | SAFE CENTRALIZED | Safe capture, bounded console/event copy or sanitized review |
| `browser_automation.py:7459` | write_text( | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `claude_integration.py:464` | accessibility | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `claude_integration.py:540` | accessibility | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `db.py:195` | screenshot_path | SAFE CENTRALIZED | Event copies and artifact paths pass privacy boundary; schemas unchanged |
| `db.py:196` | html_path | SAFE CENTRALIZED | Event copies and artifact paths pass privacy boundary; schemas unchanged |
| `db.py:204` | html_path, screenshot_path | SAFE CENTRALIZED | Event copies and artifact paths pass privacy boundary; schemas unchanged |
| `db.py:211` | screenshot_path | SAFE CENTRALIZED | Event copies and artifact paths pass privacy boundary; schemas unchanged |
| `db.py:212` | html_path | SAFE CENTRALIZED | Event copies and artifact paths pass privacy boundary; schemas unchanged |
| `db.py:220` | html_path, screenshot_path | SAFE CENTRALIZED | Event copies and artifact paths pass privacy boundary; schemas unchanged |
| `db.py:222` | screenshot_path | SAFE CENTRALIZED | Event copies and artifact paths pass privacy boundary; schemas unchanged |
| `db.py:223` | html_path | SAFE CENTRALIZED | Event copies and artifact paths pass privacy boundary; schemas unchanged |
| `diagnostics.py:134` | console_logs | SAFE CENTRALIZED | Central projection, masking, bounded writer or verified reader |
| `diagnostics.py:196` | aria_snapshot | SAFE CENTRALIZED | Central projection, masking, bounded writer or verified reader |
| `diagnostics.py:215` | aria_snapshot | SAFE CENTRALIZED | Central projection, masking, bounded writer or verified reader |
| `diagnostics.py:270` | write_text( | SAFE CENTRALIZED | Central projection, masking, bounded writer or verified reader |
| `diagnostics.py:281` | write_text( | SAFE CENTRALIZED | Central projection, masking, bounded writer or verified reader |
| `diagnostics.py:330` | page.content( | SAFE CENTRALIZED | Central projection, masking, bounded writer or verified reader |
| `diagnostics.py:344` | screenshot( | SAFE CENTRALIZED | Central projection, masking, bounded writer or verified reader |
| `diagnostics.py:381` | screenshot( | SAFE CENTRALIZED | Central projection, masking, bounded writer or verified reader |
| `diagnostics.py:388` | write_bytes( | SAFE CENTRALIZED | Central projection, masking, bounded writer or verified reader |
| `diagnostics.py:404` | console_logs | SAFE CENTRALIZED | Central projection, masking, bounded writer or verified reader |
| `diagnostics.py:407` | screenshot( | SAFE CENTRALIZED | Central projection, masking, bounded writer or verified reader |
| `diagnostics.py:412` | aria_snapshot | SAFE CENTRALIZED | Central projection, masking, bounded writer or verified reader |
| `diagnostics.py:413` | aria_snapshot | SAFE CENTRALIZED | Central projection, masking, bounded writer or verified reader |
| `diagnostics.py:416` | console_logs | SAFE CENTRALIZED | Central projection, masking, bounded writer or verified reader |
| `diagnostics.py:417` | console_logs | SAFE CENTRALIZED | Central projection, masking, bounded writer or verified reader |
| `diagnostics.py:436` | console_logs | SAFE CENTRALIZED | Central projection, masking, bounded writer or verified reader |
| `diagnostics.py:589` | html_path, screenshot_path | SAFE CENTRALIZED | Central projection, masking, bounded writer or verified reader |
| `field_requirements.py:1` | accessibility | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `form_fields.py:4` | accessibility | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `interaction.py:101` | accessibility | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `job_tracker.py:114` | screenshot_path | SAFE CENTRALIZED | Event copies and artifact paths pass privacy boundary; schemas unchanged |
| `job_tracker.py:115` | html_path | SAFE CENTRALIZED | Event copies and artifact paths pass privacy boundary; schemas unchanged |
| `job_tracker.py:297` | html_path, screenshot_path | SAFE CENTRALIZED | Event copies and artifact paths pass privacy boundary; schemas unchanged |
| `job_tracker.py:301` | screenshot_path | SAFE CENTRALIZED | Event copies and artifact paths pass privacy boundary; schemas unchanged |
| `job_tracker.py:302` | html_path | SAFE CENTRALIZED | Event copies and artifact paths pass privacy boundary; schemas unchanged |
| `job_tracker.py:309` | html_path, screenshot_path | SAFE CENTRALIZED | Event copies and artifact paths pass privacy boundary; schemas unchanged |
| `job_tracker.py:311` | html_path, screenshot_path | SAFE CENTRALIZED | Event copies and artifact paths pass privacy boundary; schemas unchanged |
| `login_guard.py:62` | write_text( | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `model_ladder.py:109` | write_text( | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `next_project_knowledge.py:93` | write_text( | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `page_agent.py:1457` | accessibility | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `page_agent.py:1460` | aria_snapshot | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `page_agent.py:1837` | screenshot( | SAFE CENTRALIZED | Safe persisted capture; live snapshots remain authoritative |
| `page_agent.py:255` | accessibility | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `page_agent.py:3053` | accessibility | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `page_agent.py:375` | accessibility | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `page_agent.py:4979` | accessibility | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `page_agent.py:5593` | write_text( | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `page_agent.py:5841` | console_logs | SAFE CENTRALIZED | Safe persisted capture; live snapshots remain authoritative |
| `page_agent.py:7` | accessibility | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `perception.py:90` | accessibility | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `profile_setup.py:355` | write_text( | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `profile_setup.py:429` | write_text( | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `repeated_entries.py:9` | accessibility | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `safety.py:555` | screenshot_path | SAFE CENTRALIZED | Diagnostic capture delegation only; policy unchanged |
| `safety.py:564` | page.content( | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `safety.py:572` | screenshot_path | SAFE CENTRALIZED | Diagnostic capture delegation only; policy unchanged |
| `safety.py:580` | screenshot( | SAFE CENTRALIZED | Diagnostic capture delegation only; policy unchanged |
| `safety.py:581` | screenshot_path | SAFE CENTRALIZED | Diagnostic capture delegation only; policy unchanged |
| `safety.py:585` | screenshot_path | SAFE CENTRALIZED | Diagnostic capture delegation only; policy unchanged |
| `session_planner.py:123` | write_text( | INTENTIONALLY OUT OF SCOPE | Authorized runtime planner IPC, not diagnostic export |
| `session_planner.py:89` | write_text( | INTENTIONALLY OUT OF SCOPE | Authorized runtime planner IPC, not diagnostic export |
| `state_machine.py:126` | dump_forensic_failure | SAFE CENTRALIZED | Forensic capture delegates to diagnostics |
| `state_machine.py:130` | console_logs | SAFE CENTRALIZED | Forensic capture delegates to diagnostics |
| `state_machine.py:138` | console_logs | SAFE CENTRALIZED | Forensic capture delegates to diagnostics |
| `state_machine.py:196` | console_logs | SAFE CENTRALIZED | Forensic capture delegates to diagnostics |
| `state_machine.py:203` | console_logs, dump_forensic_failure | SAFE CENTRALIZED | Forensic capture delegates to diagnostics |
| `state_machine.py:9` | console_logs | SAFE CENTRALIZED | Forensic capture delegates to diagnostics |
| `web_setup.py:72` | write_bytes( | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `web_ui.py:1227` | /evidence | SAFE CENTRALIZED | Viewer retains containment and verifies B5 content provenance |
| `web_ui.py:1422` | /evidence | SAFE CENTRALIZED | Viewer retains containment and verifies B5 content provenance |
| `web_ui.py:1423` | /evidence | SAFE CENTRALIZED | Viewer retains containment and verifies B5 content provenance |
| `web_ui.py:1467` | /evidence | SAFE CENTRALIZED | Viewer retains containment and verifies B5 content provenance |
| `web_ui.py:1518` | /evidence, screenshot_path | SAFE CENTRALIZED | Viewer retains containment and verifies B5 content provenance |
| `web_ui.py:1519` | html_path, screenshot_path | SAFE CENTRALIZED | Viewer retains containment and verifies B5 content provenance |
| `web_ui.py:1520` | /evidence, html_path | SAFE CENTRALIZED | Viewer retains containment and verifies B5 content provenance |
| `web_ui.py:342` | write_text( | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `web_ui.py:578` | write_text( | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `web_ui.py:598` | write_text( | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `web_ui.py:725` | screenshot_path | SAFE CENTRALIZED | Viewer retains containment and verifies B5 content provenance |
| `web_ui.py:726` | screenshot_path | SAFE CENTRALIZED | Viewer retains containment and verifies B5 content provenance |
| `web_ui.py:759` | screenshot_path | SAFE CENTRALIZED | Viewer retains containment and verifies B5 content provenance |
| `web_ui.py:760` | screenshot_path | SAFE CENTRALIZED | Viewer retains containment and verifies B5 content provenance |
| `web_ui.py:78` | write_text( | NON-DIAGNOSTIC | Live perception/interaction, comment, or authoritative state/material |
| `web_ui.py:828` | /evidence | SAFE CENTRALIZED | Viewer retains containment and verifies B5 content provenance |

`tests/` matches are TEST-ONLY. `replay_guard.py` and reference generation are INTENTIONALLY OUT OF SCOPE (owner-requested development verification/reference data, not production diagnostic capture). No unexplained raw production diagnostic writer remains.
