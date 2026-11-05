"""Tests for universal job fetching subsystem and adapters."""
import json
import pytest
from bs4 import BeautifulSoup
from unittest.mock import MagicMock, patch

from services.poller.generic_scraper import (
    extract_schema_org_jobs,
    extract_dom_heuristic_jobs,
    parse_schema_job_posting,
    clean_text,
    build_resilient_session,
)
from services.poller.smartrecruiters import poll_smartrecruiters
from services.poller.ai_job_extractor import clean_html_for_llm, _parse_llm_jobs
from scripts.fetch_jobs import detect_ats_from_url, format_table


SAMPLE_SCHEMA_JOB_HTML = """
<!DOCTYPE html>
<html>
<head>
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "title": "Senior Network Security Engineer",
    "description": "<p>Responsible for enterprise firewall and routing infrastructure.</p>",
    "identifier": {
      "@type": "PropertyValue",
      "name": "Acme Corp",
      "value": "REQ-1004"
    },
    "datePosted": "2026-11-01",
    "employmentType": "FULL_TIME",
    "jobLocation": {
      "@type": "Place",
      "address": {
        "@type": "PostalAddress",
        "addressLocality": "Dallas",
        "addressRegion": "TX",
        "addressCountry": "US"
      }
    },
    "baseSalary": {
      "@type": "MonetaryAmount",
      "currency": "USD",
      "value": {
        "@type": "QuantitativeValue",
        "minValue": 140000,
        "maxValue": 180000,
        "unitText": "YEAR"
      }
    },
    "url": "https://careers.acme.com/jobs/req-1004"
  }
  </script>
</head>
<body>
  <h1>Careers at Acme</h1>
</body>
</html>
"""

SAMPLE_DOM_JOB_HTML = """
<!DOCTYPE html>
<html>
<body>
  <div class="job-listings">
    <div class="job-card" data-job-id="NET-55">
      <h3>Staff Infrastructure Engineer</h3>
      <span class="location">Remote, US</span>
      <a href="/jobs/staff-infrastructure-engineer">View Position</a>
    </div>
    <div class="job-card" data-job-id="DEV-88">
      <h3>Cloud Security Architect</h3>
      <span class="location">New York, NY</span>
      <a href="/jobs/cloud-security-architect">View Position</a>
    </div>
  </div>
</body>
</html>
"""


def test_schema_org_job_posting_extraction():
    soup = BeautifulSoup(SAMPLE_SCHEMA_JOB_HTML, "html.parser")
    jobs = extract_schema_org_jobs(soup, base_url="https://careers.acme.com")

    assert len(jobs) == 1
    job = jobs[0]
    assert job["external_id"] == "REQ-1004"
    assert job["title"] == "Senior Network Security Engineer"
    assert job["location"] == "Dallas, TX, US"
    assert job["salary_min"] == 140000
    assert job["salary_max"] == 180000
    assert job["salary_currency"] == "USD"
    assert job["apply_url"] == "https://careers.acme.com/jobs/req-1004"
    assert "firewall and routing" in job["description"]


def test_schema_org_item_list_extraction():
    item_list_html = """
    <script type="application/ld+json">
    {
      "@type": "ItemList",
      "itemListElement": [
        {
          "@type": "JobPosting",
          "title": "Software Engineer",
          "jobLocation": "San Francisco, CA",
          "url": "/jobs/1"
        },
        {
          "@type": "JobPosting",
          "title": "DevOps Engineer",
          "jobLocation": "Remote",
          "url": "/jobs/2"
        }
      ]
    }
    </script>
    """
    soup = BeautifulSoup(item_list_html, "html.parser")
    jobs = extract_schema_org_jobs(soup, base_url="https://example.com")
    assert len(jobs) == 2
    assert jobs[0]["title"] == "Software Engineer"
    assert jobs[1]["remote"] is True
    assert jobs[1]["apply_url"] == "https://example.com/jobs/2"


def test_dom_heuristic_job_extraction():
    soup = BeautifulSoup(SAMPLE_DOM_JOB_HTML, "html.parser")
    jobs = extract_dom_heuristic_jobs(soup, base_url="https://example.com/careers")

    assert len(jobs) == 2
    assert jobs[0]["title"] == "Staff Infrastructure Engineer"
    assert jobs[0]["external_id"] == "NET-55"
    assert jobs[0]["remote"] is True
    assert jobs[0]["apply_url"] == "https://example.com/jobs/staff-infrastructure-engineer"

    assert jobs[1]["title"] == "Cloud Security Architect"
    assert jobs[1]["external_id"] == "DEV-88"
    assert "New York" in jobs[1]["location"]


def test_smartrecruiters_poller_parsing():
    fake_response = {
        "content": [
            {
                "id": "SR-1234",
                "name": "Principal Network Engineer",
                "location": {
                    "city": "Austin",
                    "region": "Texas",
                    "country": "United States",
                    "remote": True,
                },
                "department": {"label": "IT Infrastructure"},
                "releasedDate": "2026-11-03T10:00:00Z",
                "typeOfEmployment": {"label": "Full-time"},
            }
        ]
    }

    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = fake_response
    mock_session.get.return_value = mock_resp

    jobs = poll_smartrecruiters("visa", session=mock_session)
    assert len(jobs) == 1
    job = jobs[0]
    assert job["external_id"] == "SR-1234"
    assert job["title"] == "Principal Network Engineer"
    assert "Austin, Texas, United States" in job["location"]
    assert job["remote"] is True
    assert job["department"] == "IT Infrastructure"
    assert job["apply_url"] == "https://jobs.smartrecruiters.com/visa/SR-1234"


def test_ai_job_extractor_preprocessing():
    dirty_html = """
    <html>
      <head><style>.ad { color: red; }</style></head>
      <body>
        <nav><a href="/home">Home</a></nav>
        <main>
          <h1>Open Roles</h1>
          <a href="/roles/backend-eng">Backend Engineer</a>
          <p>Looking for python experts in Austin, TX.</p>
        </main>
        <footer>Copyright 2026</footer>
      </body>
    </html>
    """
    cleaned = clean_html_for_llm(dirty_html, "https://example.com")
    assert "Copyright" not in cleaned
    assert ".ad {" not in cleaned
    assert "[Backend Engineer](https://example.com/roles/backend-eng)" in cleaned


def test_ai_job_extractor_parsing():
    sample_json = json.dumps({
        "jobs": [
            {
                "title": "Security Operations Lead",
                "location": "Reston, VA",
                "department": "Cybersecurity",
                "url": "https://example.com/jobs/sec-ops",
                "remote": False,
                "req_id": "SEC-901"
            }
        ]
    })
    jobs = _parse_llm_jobs(sample_json, "https://example.com")
    assert len(jobs) == 1
    job = jobs[0]
    assert job["title"] == "Security Operations Lead"
    assert job["location"] == "Reston, VA"
    assert job["external_id"] == "SEC-901"
    assert job["apply_url"] == "https://example.com/jobs/sec-ops"


def test_detect_ats_from_url():
    assert detect_ats_from_url("https://boards.greenhouse.io/stripe") == ("greenhouse", "stripe")
    assert detect_ats_from_url("https://jobs.lever.co/netflix") == ("lever", "netflix")
    assert detect_ats_from_url("https://jobs.ashbyhq.com/openai") == ("ashby", "openai")
    assert detect_ats_from_url("https://jobs.smartrecruiters.com/visa") == ("smartrecruiters", "visa")
    assert detect_ats_from_url("https://acme.myworkdayjobs.com/Careers")[0] == "workday"
    assert detect_ats_from_url("https://company.com/careers")[0] == "generic"


def test_format_table():
    jobs = [{
        "title": "Network Engineer",
        "location": "Fairfax, VA",
        "remote": False,
        "apply_url": "https://example.com/job/1"
    }]
    table = format_table(jobs, limit=5)
    assert "Network Engineer" in table
    assert "Fairfax, VA" in table
    assert "https://example.com/job/1" in table

