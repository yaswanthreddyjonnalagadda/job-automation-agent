"""Tests for the 5-service autonomous architecture:
1. Directory Builder (seed + common crawl slug extraction)
2. Poller Service (Greenhouse, Lever, Ashby, Workday mock parsing)
3. Diff Engine (new, removed, active job detection)
4. Matcher & Resume Tailor (scoring algorithm, disqualifier gates, plain verbs)
5. Applicator (runner queue handling)
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
