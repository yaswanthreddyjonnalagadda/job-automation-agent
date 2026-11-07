"""Tests for the 5-service autonomous architecture:
1. Directory Builder (seed + common crawl slug extraction)
2. Poller Service (Greenhouse, Lever, Ashby, Workday mock parsing)
3. Diff Engine (new, removed, active job detection)
4. Matcher & Resume Tailor (scoring algorithm, disqualifier gates, plain verbs)
5. Applicator (runner queue handling, rate limiting, and daily caps)
"""
from __future__ import annotations

import tempfile
import pytest

from services.diff_engine.storage import JobStorage
from services.diff_engine.diff_engine import process_poll_batch
from services.directory_builder.bootstrap_seeds import seed_directory, SEED_COMPANIES
from services.directory_builder.common_crawl import extract_slug
from services.matcher.scoring import score_job, load_user_profile
from services.matcher.resume_tailor import clean_forbidden_verbs, estimate_cost
from services.applicator.runner import ApplicatorRunner


@pytest.fixture
def temp_storage():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    storage = JobStorage(dsn=f"sqlite:///{db_path}")
    yield storage


def test_directory_builder_seeds_companies(temp_storage):
    count = seed_directory(temp_storage)
    assert count == len(SEED_COMPANIES)
    companies = temp_storage.get_companies_to_poll()
    assert len(companies) >= len(SEED_COMPANIES)
    platforms = {c["ats_platform"] for c in companies}
    assert {"greenhouse", "lever", "ashby", "workday"}.issubset(platforms)


def test_common_crawl_slug_extraction():
    gh_url = "https://boards.greenhouse.io/stripe/jobs/12345"
    assert extract_slug("greenhouse", gh_url) == "stripe"

    lever_url = "https://jobs.lever.co/palantir/abc-def"
    assert extract_slug("lever", lever_url) == "palantir"

    ashby_url = "https://jobs.ashbyhq.com/openai/987"
    assert extract_slug("ashby", ashby_url) == "openai"

    wd_url = "https://analogdevices.wd1.myworkdayjobs.com/en-US/Analog_Devices_External/job/123"
    assert extract_slug("workday", wd_url) == "analogdevices"


def test_diff_engine_detects_new_removed_and_active_jobs(temp_storage):
    company_slug = "testco"
    platform = "greenhouse"

    # Poll 1: 2 incoming jobs
    batch_1 = {
        "ats_platform": platform,
        "company_slug": company_slug,
        "jobs": [
            {"external_id": "job_1", "title": "Senior Network Engineer", "location": "Remote"},
            {"external_id": "job_2", "title": "Cloud Infrastructure Engineer", "location": "Fairfax, VA"},
        ],
    }
    res_1 = process_poll_batch(batch_1, temp_storage)
    assert res_1["new_count"] == 2
    assert res_1["removed_count"] == 0
    assert res_1["still_active_count"] == 0

    active_ids = temp_storage.get_active_job_ids(platform, company_slug)
    assert active_ids == {"job_1", "job_2"}

    # Poll 2: job_1 stays, job_2 removed, job_3 new
    batch_2 = {
        "ats_platform": platform,
        "company_slug": company_slug,
        "jobs": [
            {"external_id": "job_1", "title": "Senior Network Engineer", "location": "Remote"},
            {"external_id": "job_3", "title": "Network Automation Engineer", "location": "New York, NY"},
        ],
    }
    res_2 = process_poll_batch(batch_2, temp_storage)
    assert res_2["new_count"] == 1
    assert res_2["removed_count"] == 1
    assert res_2["still_active_count"] == 1

    active_ids_2 = temp_storage.get_active_job_ids(platform, company_slug)
    assert active_ids_2 == {"job_1", "job_3"}


def test_matcher_scores_high_matching_role():
    profile = {
        "target_roles": ["Senior Network Engineer"],
        "preferred_locations": ["Remote", "Fairfax, VA"],
        "salary_range": {"min": 120000, "max": 180000},
        "must_have_keywords": ["network", "cloud", "infrastructure"],
        "nice_to_have_keywords": ["Cisco", "Python", "BGP"],
        "disqualifiers": ["security clearance", "no sponsorship"],
    }
    job = {
        "title": "Senior Network Engineer",
        "location": "Remote",
        "salary_min": 130000,
        "salary_max": 160000,
        "description": "Enterprise cloud network infrastructure with Cisco routing, Python automation, and BGP protocols.",
    }
    result = score_job(job, profile)
    assert result["score"] >= 80
    assert result["status"] == "queue"


def test_matcher_disqualifier_skips_immediately():
    profile = {
        "target_roles": ["Senior Network Engineer"],
        "preferred_locations": ["Remote"],
        "salary_range": {"min": 120000, "max": 180000},
        "must_have_keywords": ["network"],
        "nice_to_have_keywords": ["Cisco"],
        "disqualifiers": ["security clearance", "no sponsorship", "us citizen only"],
    }
    job = {
        "title": "Senior Network Engineer",
        "location": "Remote",
        "salary_max": 150000,
        "description": "Active Top Secret Security Clearance required for government network deployment.",
    }
    result = score_job(job, profile)
    assert result["score"] == 0.0
    assert result["status"] == "skipped"
    assert "security clearance" in result["disqualified_by"]


def test_plain_verbs_requirement_cleans_buzzwords():
    buzzword_text = "Spearheaded enterprise migration, orchestrated cloud failover, and leveraged Terraform."
    cleaned = clean_forbidden_verbs(buzzword_text)
    assert "Spearheaded" not in cleaned
    assert "orchestrated" not in cleaned
    assert "leveraged" not in cleaned
    assert "Led enterprise migration, built cloud failover, and used Terraform." == cleaned


def test_applicator_runner_batch_execution(temp_storage):
    runner = ApplicatorRunner(temp_storage)
    processed = runner.run_batch(limit=5)
    assert processed == 0  # No queued apps initially


def test_end_to_end_diff_match_and_apply_flow(temp_storage):
    # 1. Diff engine detects new job
    batch = {
        "ats_platform": "greenhouse",
        "company_slug": "stripe",
        "jobs": [
            {
                "external_id": "job_e2e_100",
                "title": "Senior Network Engineer",
                "location": "Remote",
                "salary_min": 140000,
                "salary_max": 170000,
                "description": "Cloud network infrastructure engineer with Cisco and Python experience.",
                "apply_url": "https://boards.greenhouse.io/stripe/jobs/job_e2e_100",
            }
        ],
    }
    diff_res = process_poll_batch(batch, temp_storage)
    assert diff_res["new_count"] == 1
    job_id = diff_res["new_job_ids"][0]

    # Verify job initially has no match score
    unmatched = temp_storage.get_unmatched_jobs(limit=10)
    assert any(j["id"] == job_id for j in unmatched)

    # 2. Matcher processes job
    job = temp_storage.get_job_by_id(job_id)
    profile = load_user_profile()
    scoring_res = score_job(job, profile)
    assert scoring_res["status"] == "queue"
    temp_storage.update_match_score(job_id, scoring_res["score"])

    # Create queued application
    app_id = temp_storage.create_application({
        "job_id": job_id,
        "match_score": scoring_res["score"],
        "resume_version": "storage/resumes/stripe_resume.pdf",
        "cover_letter": "I am excited to apply...",
        "tailoring_prompt": "Tailor for Stripe",
        "claude_tokens_used": 1500,
        "claude_cost_usd": 0.045,
        "status": "queued",
        "portal_used": "greenhouse",
    })
    assert app_id > 0

    # 3. Applicator processes queued application
    runner = ApplicatorRunner(temp_storage, max_apps_per_day=10)
    processed = runner.run_batch(limit=5)
    assert processed == 1

    # Verify application is submitted
    cur = temp_storage._conn.cursor()
    cur.execute("SELECT status, submitted_at, notes FROM applications WHERE id = ?", (app_id,))
    row = dict(cur.fetchone())
    assert row["status"] == "submitted"
    assert row["submitted_at"] is not None

    # Verify job status is updated to applied
    updated_job = temp_storage.get_job_by_id(job_id)
    assert updated_job["status"] == "applied"


def test_applicator_runner_respects_daily_limit(temp_storage):
    # Setup job
    job_id = temp_storage.insert_job({
        "external_id": "job_limit_1",
        "ats_platform": "lever",
        "company_slug": "netflix",
        "title": "Network Engineer",
        "location": "Remote",
        "apply_url": "https://jobs.lever.co/netflix/1",
        "status": "active",
    })
    # Create 2 queued applications
    for i in range(2):
        temp_storage.create_application({
            "job_id": job_id,
            "match_score": 85.0,
            "resume_version": "res.pdf",
            "cover_letter": "letter",
            "tailoring_prompt": "prompt",
            "claude_tokens_used": 100,
            "claude_cost_usd": 0.005,
            "status": "queued",
            "portal_used": "lever",
        })

    # Set runner daily limit to 1
    runner = ApplicatorRunner(temp_storage, max_apps_per_day=1)
    first_run = runner.run_batch(limit=5)
    assert first_run == 1
    assert runner.count_today_applications() == 1

    # Second batch attempt should be blocked by daily limit
    second_run = runner.run_batch(limit=5)
    assert second_run == 0
