"""Unit tests for query-driven real-time job discovery and ATS resolution."""

import json
from unittest.mock import MagicMock, patch

import pytest

from services.poller.query_discovery import (
    DiscoveredQueryJob,
    clean_location_string,
    parse_linkedin_guest_html,
    search_arbeitnow_jobs,
    search_jobicy_jobs,
    search_jsearch_rapidapi,
    search_all_fresh_jobs,
)
from services.poller.ats_resolver import (
    detect_ats_from_url,
    register_discovered_company,
    resolve_and_seed_job,
)


SAMPLE_LINKEDIN_HTML = """
<ul class="jobs-search__results-list">
  <li>
    <div class="base-card job-search-card" data-entity-urn="urn:li:jobPosting:4123456789">
      <div class="base-search-card__info">
        <h3 class="base-search-card__title">Senior Cloud Network Engineer</h3>
        <h4 class="base-search-card__subtitle">
          <a class="hidden-nested-link" href="https://www.linkedin.com/company/cisco">Cisco Systems</a>
        </h4>
        <div class="base-search-card__metadata">
          <span class="job-search-card__location">Fairfax, VA Actively Hiring 4 hours ago</span>
          <time datetime="2026-11-04">Nov 4, 2026</time>
        </div>
        <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/senior-cloud-network-engineer-at-cisco-4123456789?refId=123"></a>
      </div>
    </div>
  </li>
  <li>
    <div class="base-card job-search-card">
      <div class="base-search-card__info">
        <h3 class="base-search-card__title">Network Security Engineer (Remote)</h3>
        <h4 class="base-search-card__subtitle">Palo Alto Networks</h4>
        <div class="base-search-card__metadata">
          <span class="job-search-card__location">Remote, US Be an early applicant</span>
        </div>
        <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/4987654321"></a>
      </div>
    </div>
  </li>
</ul>
"""


def test_parse_linkedin_guest_html():
    jobs = parse_linkedin_guest_html(SAMPLE_LINKEDIN_HTML)
    assert len(jobs) == 2

    job1 = jobs[0]
    assert job1.title == "Senior Cloud Network Engineer"
    assert job1.company_name == "Cisco Systems"
    assert "Fairfax, VA" in job1.location
    assert "Actively Hiring" not in job1.location  # Badges cleaned
    assert "4123456789" in job1.url
    assert job1.job_id == "4123456789"
    assert not job1.is_remote

    job2 = jobs[1]
    assert job2.title == "Network Security Engineer (Remote)"
    assert job2.company_name == "Palo Alto Networks"
    assert job2.is_remote
    assert "Be an early applicant" not in job2.location


def test_clean_location_string():
    assert clean_location_string("Fairfax, VA Actively Hiring 4 hours ago") == "Fairfax, VA"
    assert clean_location_string("New York, NY Be an early applicant Medical insurance") == "New York, NY"
    assert clean_location_string("Remote, US +3 benefits") == "Remote, US"
    assert clean_location_string("") == "United States"


def test_detect_ats_from_url():
    # Greenhouse
    ats, slug = detect_ats_from_url("https://boards.greenhouse.io/stripe/jobs/12345")
    assert ats == "greenhouse"
    assert slug == "stripe"

    # Lever
    ats, slug = detect_ats_from_url("https://jobs.lever.co/palantir/abc-123")
    assert ats == "lever"
    assert slug == "palantir"

    # Ashby
    ats, slug = detect_ats_from_url("https://jobs.ashbyhq.com/openai/xyz")
    assert ats == "ashby"
    assert slug == "openai"

    # Workday
    ats, slug = detect_ats_from_url("https://cisco.myworkdayjobs.com/Cisco_Jobs/job/123")
    assert ats == "workday"
    assert slug == "cisco"

    # SmartRecruiters
    ats, slug = detect_ats_from_url("https://www.smartrecruiters.com/Visa/7439999")
    assert ats == "smartrecruiters"
    assert slug == "visa"

    # Non-ATS
    ats, slug = detect_ats_from_url("https://example.com/careers")
    assert ats is None
    assert slug is None


def test_register_discovered_company(tmp_path):
    config_file = tmp_path / "target_companies.json"

    # Register new company
    added = register_discovered_company("Databricks", "databricks", "greenhouse", config_path=config_file)
    assert added is True

    # Register duplicate - should return False
    duplicate = register_discovered_company("Databricks Inc", "databricks", "greenhouse", config_path=config_file)
    assert duplicate is False

    # Check file contents
    content = json.loads(config_file.read_text(encoding="utf-8"))
    assert len(content) == 1
    assert content[0]["token"] == "databricks"
    assert content[0]["ats"] == "greenhouse"


def test_search_arbeitnow_jobs():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [
            {
                "title": "Cloud Infrastructure Engineer",
                "company_name": "Acme Corp",
                "location": "Berlin, Germany",
                "remote": True,
                "url": "https://example.com/job1",
                "created_at": 1730764800,
                "description": "AWS, Terraform, and Python infrastructure.",
            },
            {
                "title": "Marketing Manager",
                "company_name": "Sales Corp",
                "location": "Remote",
                "remote": True,
                "url": "https://example.com/job2",
                "created_at": 1730764800,
                "description": "Lead generation.",
            }
        ]
    }

    session = MagicMock()
    session.get.return_value = mock_resp

    jobs = search_arbeitnow_jobs(query="Cloud Infrastructure", session=session)
    assert len(jobs) == 1
    assert jobs[0].title == "Cloud Infrastructure Engineer"
    assert jobs[0].company_name == "Acme Corp"
    assert jobs[0].is_remote is True


def test_search_jobicy_jobs():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "jobs": [
            {
                "id": 991,
                "jobTitle": "Senior Network Security Engineer",
                "companyName": "Fortinet Partner",
                "url": "https://example.com/jobicy/991",
                "jobGeo": "USA",
                "pubDate": "2026-11-04 12:00:00",
                "jobExcerpt": "Firewalls and VPN management.",
            }
        ]
    }

    session = MagicMock()
    session.get.return_value = mock_resp

    jobs = search_jobicy_jobs(query="Network", session=session)
    assert len(jobs) == 1
    assert jobs[0].title == "Senior Network Security Engineer"
    assert jobs[0].company_name == "Fortinet Partner"
    assert jobs[0].job_id == "991"


def test_search_jsearch_rapidapi():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [
            {
                "job_id": "js_101",
                "job_title": "Lead Network Engineer",
                "employer_name": "Humana",
                "job_city": "Louisville",
                "job_state": "KY",
                "job_country": "US",
                "job_is_remote": False,
                "job_apply_link": "https://boards.greenhouse.io/humana/jobs/101",
                "job_description": "Cisco routers and switches.",
                "job_posted_at_datetime_utc": "2026-11-04T14:00:00.000Z",
            }
        ]
    }

    session = MagicMock()
    session.get.return_value = mock_resp

    jobs = search_jsearch_rapidapi(
        query="Network Engineer",
        rapidapi_key="test_key",
        session=session,
    )
    assert len(jobs) == 1
    assert jobs[0].title == "Lead Network Engineer"
    assert jobs[0].company_name == "Humana"
    assert "Louisville, KY, US" in jobs[0].location
    assert jobs[0].url == "https://boards.greenhouse.io/humana/jobs/101"


def test_resolve_and_seed_job():
    with patch("services.poller.ats_resolver.unroll_url") as mock_unroll:
        mock_unroll.return_value = "https://boards.greenhouse.io/stripe/jobs/998877"

        res = resolve_and_seed_job("Stripe", "https://redirect.com/job/123")
        assert res["ats_type"] == "greenhouse"
        assert res["company_token"] == "stripe"
        assert "stripe/jobs/998877" in res["canonical_url"]

