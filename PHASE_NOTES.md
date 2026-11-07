# Engineering Implementation Phase Notes

## Phase 0 — Audit & System Baseline
- Developed comprehensive map across 9 subsystems (Entrypoints, Form Perception, Answer Resolution, Portal Adapters, Auth, Safety, AI Providers, Persistence, Handoff).
- Identified root cause of filter failures: dual-engine discrepancy between SQLite `job_discovery.py` and Postgres `services/matcher/scoring.py`, rigid substring matching on locations like "Fairfax, VA" vs "Northern Virginia", and unnormalized title tokenization.
- Identified root cause of low volume: single-page guest limits, missing pagination on LinkedIn, hardcoded 50-company board lists, and lack of cross-product query expansion.
- Verified system safety: `job-agent-db` (port 5432) contains 90 submitted applications and is strictly isolated; new architecture services bind to PostgreSQL port 5433 / SQLite fallback.

## Phase 1 — Profile-Driven Search Spec
- Developed `SearchSpec` data model in `services/matcher/search_spec.py` using Pydantic v2. Validates target titles, title synonyms, adjacent titles, seniority levels, must-have/nice-to-have keywords, target locations, workplace types, salary floor, employment types, sponsorship needs, company/keyword exclusions, and hard/soft flags.
- Developed `services/matcher/search_spec_parser.py` with Claude API parser (`_call_claude_api`) and deterministic heuristic fallback for offline/test environments.
- Developed `bootstrap_search_spec_from_profile()` to automatically initialize version 1 from `config/user_profile.yaml` so the system never starts with an empty spec.
- Added database persistence and versioning to `services/diff_engine/storage.py`:
  - Created `search_specs` table supporting both PostgreSQL (JSONB) and SQLite (JSON string).
  - Added `save_search_spec(spec)`, `get_active_search_spec()`, and `list_search_spec_versions()`.
  - Saving a new version automatically deactivates prior versions while preserving full audit history.
- Built query expansion engine (`get_expanded_queries`) yielding the cross-product of titles, synonyms, and location targets with configurable query budgeting.
- Integrated `load_search_spec()` into `job_discovery.py` (`JobDiscoveryEngine`) and `services/poller/poller_service.py`, replacing static hardcoded queries.
- Added full test suite in `tests/test_search_spec.py` (6/6 passing).

## Phase 2 — Fetching at Scale
- Built `services/poller/bamboohr.py`: Direct polling adapter for BambooHR public career API endpoints (`/jobs/embed2List/` and `/careers/list`).
- Built `search_linkedin_guest_jobs_paginated` in `services/poller/query_discovery.py`: Exhaustive pagination across offsets (`start=0, 25, 50...`) up to `max_pages` with resilient fallbacks.
- Built `search_all_fresh_jobs_at_scale`: Multi-source querying with strict per-source isolation (LinkedIn Guest, JSearch, Jobicy, Arbeitnow). Failures or rate limits on one provider never interrupt or block others.
- Built `insert_or_merge_job` and `get_job_sources` in `services/diff_engine/storage.py`: Normalizes job entities, deduplicates cross-source postings on `(company_slug, title, location)`, and tracks all origin sources in the `job_sources` table.
- Added comprehensive unit tests in `tests/test_fetching_at_scale.py` (4/4 passing).

## Phase 3 — Filter Rewrite
- Developed `services/matcher/filter_pipeline.py`: Typed 3-valued filter pipeline returning `FilterVerdict.PASS`, `FilterVerdict.FAIL`, and `FilterVerdict.UNKNOWN` with structured reasons and penalties.
- Built `SalaryFilter` with `parse_salary_to_annual`: Handles `$135k`, `$120,000 - $150,000`, and hourly rates `$65/hr` ($\times 2080 = \$135,200$). Missing salary is classified as `UNKNOWN` and passes neutrally rather than being dropped.
- Built `LocationFilter`: Robust Northern Virginia and DC Metro support (Fairfax, Arlington, Reston, Ashburn, Herndon, Tysons, Alexandria, DMV, NoVA). Missing location passes as `UNKNOWN`.
- Built `SponsorshipFilter`: Scans for disqualifiers (`security clearance`, `polygraph`, `US citizen only`, `no sponsorship`) and enforces hard-rejection.
- Built `JobFilterPipeline`: Computes overall outcome, accumulates filter details, and maintains funnel statistics (`total_evaluated`, `passed`, `failed`, per-filter rejections).
- Added comprehensive unit tests and regression suite for previously missed jobs in `tests/test_filter_pipeline.py` (5/5 passing).

## Phase 4 — Answer Engine
- Developed `services/matcher/answer_engine.py`: Canonical question intent ontology and answer resolver for universal job application forms.
- Seeded candidate approved rules in `get_approved_seed_answers()`:
  - Full Name: Yaswanth Reddy Jonnalagadda
  - Phone Type: Mobile
  - County: Fairfax
  - Referral Source: LinkedIn
  - Prior Employment: No
  - Relatives Employed: No
  - Age 18 or older: Yes
  - Full Time: Full Time
  - Start Date: Immediately
  - Weekends & Holidays: Yes
  - Minimum Salary: $125,000 annually
  - EEO fields (Male, Not a protected veteran, No disability, Asian).
- Built semantic paraphrase matcher linking variations of sponsorship, work authorization, and former employment questions to identical canonical intents.
- Built semantic control option mapper (`resolve_question_for_control`) matching canonical answers to target dropdowns/radios (e.g. mapping "LinkedIn" to "Social Media / Job Board").
- Developed Human Review Queue (`review_queue` table): Questions requiring facts not in the profile (supervisor phone numbers, secret references, shift preferences) are paused safely and never guessed or fabricated.
- Built self-learning resolution (`submit_user_answer`): Resolving an item learns the approved answer, stores it in `custom_answers`, and resumes the application.
- Added comprehensive unit tests in `tests/test_answer_engine.py` (5/5 passing).

## Phase 4B — UI (Local Control Panel)
- Developed local web dashboard integration in `services/web_control_panel.py` supporting 7 operational screens:
  1. Profile & Search Instructions (`/search-spec`): Free-text prompt parser via Claude API, visual JSON/Pydantic editor, and active versioning.
  2. Jobs Feed (`/`): High-density table with multi-criteria search, title filter, and match scores.
  3. Funnel & Sources (`/funnel`): Rejection explorer breakdown, per-source volume counters, and ATS directory status.
  4. Review Queue (`/review-queue`): Interactive approval, direct answers, and reusable learning for paused applications without fabrication.
  5. Answer Bank (`/answer-bank`): Canonical intent rules preview and interactive question test resolver (`/api/answer-bank/test-question`).
  6. Applications Tracker (`/tracker`): Full history view of submitted applications across both database engines.
  7. Run Control & Logs (`/run-control`): Live start/pause, dry-run safety toggle, and log streaming.
- Registered `control_panel_bp` in `web_ui.py` and updated `ui_shell.py` navigation bar with links to Search Spec, Funnel, Review Queue, and Answer Bank.
- Added comprehensive unit tests in `tests/test_web_control_panel.py` (6/6 passing).

## Phase 5 — System Verification, Replay Suite & Safety Confirmation
- Built 35-question real-world replay test in `tests/test_question_replay_30.py` verifying:
  - Exact intent mapping and answers for diverse question paraphrases (work authorization, sponsorship, prior employment, relatives employed, referral sources, age verification, full-time commitment, immediate availability, weekend/holiday flexibility, salary floor).
  - Sensitive questions (supervisor contact numbers, references, shift preferences, clearance levels) routing strictly to the Review Queue with `answer=None` and `needs_user_review=True`.
  - Zero fabrication and zero hallucination verified across all 35 cases.
- Verified Hard Constraints:
  - Container `job-agent-db` (port 5432) tracked applications count remains strictly at 90 (100% intact, no destructive operations).
  - Credentials remain strictly in `.env`.
  - Default execution mode verified as dry-run.
- Ran regression guardrails:
  - `python replay_guard.py`: 1,115 saved pages across all portal runs remain 100% regression-free.
  - `tests/test_no_place_literals.py`: 7/7 passed.
  - Combined Phase 1-5 test suite: 27/27 passed in 7.09s.

