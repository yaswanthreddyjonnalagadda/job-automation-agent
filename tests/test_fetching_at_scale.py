"""Tests for Phase 2: Fetching at Scale (Pagination, BambooHR, Unified Job Schema, Deduplication, Multi-Source Isolation)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from services.diff_engine.storage import JobStorage
from services.matcher.search_spec import SearchSpec
from services.poller.bamboohr import fetch_bamboohr_jobs
from services.poller.query_discovery import (
    DiscoveredQueryJob,
    search_all_fresh_jobs_at_scale,
    search_linkedin_guest_jobs_paginated,
)


def test_bamboohr_poller_parses_job_list():
    """BambooHR poller parses public career JSON feed."""
    mock_response = {
        "result": [
            {
                "id": "101",
                "jobOpeningName": "Senior Network Engineer",
                "location": {"city": "Reston", "state": "VA"},
                "isRemote": True,
                "department": "Engineering",
                "description": "Lead network infrastructure.",
            },
            {
                "id": "102",
                "jobOpeningName": "Cloud Network Architect",
                "location": {"city": "Fairfax", "state": "VA"},
                "isRemote": False,
                "department": "Infrastructure",
                "description": "Design cloud networks.",
            },
        ]
    }
    with patch("services.poller.bamboohr._http_get_resilient", return_value=json.dumps(mock_response)):
        jobs = fetch_bamboohr_jobs("acme-networks", "Acme Networks")
        assert len(jobs) == 2
        assert jobs[0]["title"] == "Senior Network Engineer"
        assert jobs[0]["ats_type"] == "bamboohr"
        assert jobs[0]["is_remote"] == 1
        assert "Reston, VA" in jobs[0]["location"]


def test_linkedin_guest_jobs_paginated_exhausts_pages():
    """Paginates through start=0, 25, 50 until empty or max_pages reached."""
    page1_html = """
    <ul>
      <li>
        <div class="base-card">
          <h3 class="base-search-card__title">Network Engineer 1</h3>
          <h4 class="base-search-card__subtitle">Cisco</h4>
          <span class="job-search-card__location">Fairfax, VA</span>
          <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/1001"></a>
        </div>
      </li>
    </ul>
    """
    page2_html = """
    <ul>
      <li>
        <div class="base-card">
          <h3 class="base-search-card__title">Network Engineer 2</h3>
          <h4 class="base-search-card__subtitle">Juniper</h4>
          <span class="job-search-card__location">Remote</span>
          <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/1002"></a>
        </div>
      </li>
    </ul>
    """
    with patch("services.poller.query_discovery._http_get_resilient", side_effect=[page1_html, page2_html, ""]):
        jobs = search_linkedin_guest_jobs_paginated(
            keywords="Network Engineer",
            location="Fairfax, VA",
            max_pages=3,
        )
        assert len(jobs) == 2
        assert jobs[0].title == "Network Engineer 1"
        assert jobs[1].title == "Network Engineer 2"


def test_one_failing_source_never_stops_others():
    """If LinkedIn throws an exception or network error, Arbeitnow and Jobicy still return jobs."""
    with patch("services.poller.query_discovery.search_linkedin_guest_jobs_paginated", side_effect=RuntimeError("LinkedIn rate limit")):
        with patch("services.poller.query_discovery.search_jobicy_jobs") as mock_jobicy:
            mock_jobicy.return_value = [
                DiscoveredQueryJob(
                    title="Cloud Network Engineer",
                    company_name="CloudCo",
                    location="Remote",
                    url="https://cloudco.example/jobs/1",
                    source="jobicy",
                    is_remote=True,
                )
            ]
            jobs, stats = search_all_fresh_jobs_at_scale(
                keywords="Cloud Network Engineer",
                location="Remote",
            )
            assert len(jobs) >= 1
            assert jobs[0].company_name == "CloudCo"
            assert stats["linkedin_guest"]["error"] is True
            assert stats["jobicy"]["count"] == 1


def test_unified_dedup_and_source_tracking(tmp_path):
    """Deduplicates on company + title + location while tracking all sources seen."""
    db_file = tmp_path / "scale_test.db"
    storage = JobStorage(dsn=f"sqlite:///{db_file}")

    job_a = {
        "external_id": "job-101",
        "ats_platform": "greenhouse",
        "company_slug": "acme",
        "company_name": "Acme Corp",
        "title": "Senior Network Engineer",
        "location": "Fairfax, VA",
        "apply_url": "https://boards.greenhouse.io/acme/jobs/101",
        "remote": 0,
    }
    # Second posting from LinkedIn pointing to the same role
    job_b = {
        "external_id": "job-101",
        "ats_platform": "greenhouse",
        "company_slug": "acme",
        "company_name": "Acme Corp",
        "title": "Senior Network Engineer",
        "location": "Fairfax, VA",
        "apply_url": "https://linkedin.com/jobs/view/9999",
        "remote": 0,
    }

    id1 = storage.insert_or_merge_job(job_a, source="greenhouse")
    id2 = storage.insert_or_merge_job(job_b, source="linkedin")

    assert id1 == id2  # Deduplicated into the same row!
    stored = storage.get_job_by_id(id1)
    assert stored["title"] == "Senior Network Engineer"
    # Both sources recorded in source tracking
    sources_seen = storage.get_job_sources(id1)
    assert "greenhouse" in sources_seen
    assert "linkedin" in sources_seen

