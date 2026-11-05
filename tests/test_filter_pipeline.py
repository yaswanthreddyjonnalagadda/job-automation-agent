"""Tests for Phase 3: Filter Rewrite (Typed 3-Valued Pipeline, Robust Salary Parsing, Northern Virginia Locations, Rejections)."""
from __future__ import annotations

import pytest

from services.matcher.filter_pipeline import (
    FilterVerdict,
    JobFilterPipeline,
    SalaryFilter,
    LocationFilter,
    TitleFilter,
    parse_salary_to_annual,
)
from services.matcher.search_spec import SearchSpec


@pytest.fixture
def default_spec():
    return SearchSpec(
        raw_instructions="Senior Network Engineer in Fairfax or Remote, min 125k, sponsorship required",
        target_titles=["Network Engineer", "Senior Network Engineer", "Cloud Network Engineer"],
        title_synonyms={"Network Engineer": ["Network Infrastructure Engineer"]},
        locations=["Fairfax, VA", "Arlington, VA", "Remote"],
        salary_floor_annual=125000,
        requires_visa_sponsorship=True,
        excluded_keywords=["security clearance", "public trust", "sales"],
        hard_criteria={
            "sponsorship": True,
            "title": True,
            "location": True,
            "salary": False,  # Soft criterion
        },
    )


def test_salary_parsing_formats():
    """Robust parsing of hourly, annual, k-notation, and ranges."""
    assert parse_salary_to_annual("$135k") == (135000, 135000)
    assert parse_salary_to_annual("$120,000 - $150,000") == (120000, 150000)
    # Hourly $65/hr * 2080 hrs = $135,200
    min_ann, max_ann = parse_salary_to_annual("$65 - $75 per hour")
    assert min_ann == 65 * 2080
    assert max_ann == 75 * 2080
    assert parse_salary_to_annual("Competitive") == (None, None)


def test_missing_data_returns_unknown_never_drops(default_spec):
    """Missing salary or location yields UNKNOWN verdict and passes with a flag."""
    pipeline = JobFilterPipeline(default_spec)
    job = {
        "title": "Senior Network Engineer",
        "location": "",  # Missing location
        "description": "Cisco, BGP, SD-WAN networking.",
        "salary_min": None,
        "salary_max": None,
    }
    outcome = pipeline.evaluate(job)
    # Job should not be dropped silently
    assert outcome.passed is True
    # Filter result for location and salary should be UNKNOWN
    loc_res = next(r for r in outcome.results if r.filter_name == "location")
    sal_res = next(r for r in outcome.results if r.filter_name == "salary")
    assert loc_res.verdict == FilterVerdict.UNKNOWN
    assert sal_res.verdict == FilterVerdict.UNKNOWN


def test_northern_virginia_and_dc_metro_locations(default_spec):
    """Understands Northern Virginia variants (Fairfax, Arlington, Reston, Ashburn, DMV)."""
    loc_filter = LocationFilter(default_spec)
    assert loc_filter.evaluate({"location": "Fairfax, Virginia"}).verdict == FilterVerdict.PASS
    assert loc_filter.evaluate({"location": "Arlington, VA"}).verdict == FilterVerdict.PASS
    assert loc_filter.evaluate({"location": "Northern Virginia"}).verdict == FilterVerdict.PASS
    assert loc_filter.evaluate({"location": "Reston, VA"}).verdict == FilterVerdict.PASS
    assert loc_filter.evaluate({"location": "Remote - US"}).verdict == FilterVerdict.PASS
    assert loc_filter.evaluate({"location": "London, UK"}).verdict == FilterVerdict.FAIL


def test_hard_vs_soft_criteria_behavior(default_spec):
    """Hard criterion fails the job; soft criterion lowers score without failing."""
    pipeline = JobFilterPipeline(default_spec)

    # Job below salary floor (salary is soft)
    job_low_salary = {
        "title": "Senior Network Engineer",
        "location": "Fairfax, VA",
        "description": "Network operations. Salary $110k.",
        "salary_min": 110000,
        "salary_max": 110000,
    }
    outcome_low_salary = pipeline.evaluate(job_low_salary)
    assert outcome_low_salary.passed is True  # Passed because salary is soft!
    assert outcome_low_salary.score < 85  # But penalized for below floor

    # Job requiring security clearance (sponsorship/clearance is hard)
    job_clearance = {
        "title": "Senior Network Engineer",
        "location": "Fairfax, VA",
        "description": "Active Top Secret Security Clearance required.",
    }
    outcome_clearance = pipeline.evaluate(job_clearance)
    assert outcome_clearance.passed is False  # Failed hard criterion!
    assert "security clearance" in outcome_clearance.rejection_reasons[0].lower()


def test_regression_missed_jobs(default_spec):
    """
    Jobs the user should have received but were previously dropped by naive string matching:
    1. 'Lead Network Infrastructure Engineer' (synonym match)
    2. 'Cloud Network Engineer - $70/hr' (hourly wage above $125k)
    3. 'Sr. Network Engineer (Reston/Northern VA)' (location variant)
    """
    pipeline = JobFilterPipeline(default_spec)
    missed_jobs = [
        {
            "title": "Lead Network Infrastructure Engineer",
            "location": "Remote",
            "description": "Lead Cisco enterprise networks.",
        },
        {
            "title": "Cloud Network Engineer",
            "location": "Fairfax, VA",
            "description": "AWS & Cisco cloud networks, rate $70/hr.",
        },
        {
            "title": "Sr. Network Engineer",
            "location": "Reston, Northern Virginia",
            "description": "Enterprise routing and switching.",
        },
    ]
    for j in missed_jobs:
        outcome = pipeline.evaluate(j)
        assert outcome.passed is True, f"Job '{j['title']}' was incorrectly dropped: {outcome.rejection_reasons}"

