"""
Tests for Aggregator-to-Direct-Careers URL Resolution (TDD).
Ensures discovered LinkedIn/aggregator jobs resolve to direct ATS or careers URLs
so clicking Apply does not fail with 'social site blocked'.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from services.poller.ats_resolver import (
    is_aggregator_url,
    extract_aggregator_posting_details,
    resolve_direct_career_url,
    resolve_and_seed_job,
)


def test_is_aggregator_url():
    assert is_aggregator_url("https://www.linkedin.com/jobs/view/12345") is True
    assert is_aggregator_url("https://www.indeed.com/viewjob?jk=abc") is True
    assert is_aggregator_url("https://www.dice.com/job-detail/xyz") is True
    assert is_aggregator_url("https://boards.greenhouse.io/stripe/jobs/123") is False
    assert is_aggregator_url("https://jobs.lever.co/palantir/456") is False
    assert is_aggregator_url("https://careers.massachusetts.edu/mob/en-us/job/530411") is False


def test_extract_aggregator_posting_details_from_json_ld():
    sample_html = """
    <html>
      <head>
        <script type="application/ld+json">
        {
          "@context": "http://schema.org",
          "@type": "JobPosting",
          "title": "Senior Network Engineer",
          "identifier": {
            "@type": "PropertyValue",
            "name": "UMass Boston",
            "value": "530411"
          },
          "hiringOrganization": {
            "@type": "Organization",
            "name": "UMass Boston"
          },
          "description": "Apply online at https://careers.massachusetts.edu/mob/en-us/job/530411/senior-network-engineer"
        }
        </script>
      </head>
      <body></body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = sample_html

    session = MagicMock()
    session.get.return_value = mock_resp

    details = extract_aggregator_posting_details(
        "https://www.linkedin.com/jobs/view/senior-network-engineer-at-umass-boston-4486561138",
        session=session,
    )
    assert details["job_id"] == "530411"
    assert details["organization"] == "UMass Boston"
    assert "careers.massachusetts.edu" in details["direct_url"]


def test_resolve_direct_career_url_maps_linkedin_to_direct_careers():
    with patch("services.poller.ats_resolver.extract_aggregator_posting_details") as mock_extract:
        mock_extract.return_value = {
            "job_id": "530411",
            "organization": "UMass Boston",
            "direct_url": "https://careers.massachusetts.edu/mob/en-us/job/530411/senior-network-engineer",
        }
        direct = resolve_direct_career_url(
            company="UMass Boston",
            title="Senior Network Engineer",
            raw_url="https://www.linkedin.com/jobs/view/senior-network-engineer-at-umass-boston-4486561138",
        )
        assert direct == "https://careers.massachusetts.edu/mob/en-us/job/530411/senior-network-engineer"


def test_resolve_and_seed_job_resolves_aggregator_when_possible():
    with patch("services.poller.ats_resolver.resolve_direct_career_url") as mock_resolve:
        mock_resolve.return_value = "https://careers.massachusetts.edu/mob/en-us/job/530411/senior-network-engineer"
        res = resolve_and_seed_job(
            company_name="UMass Boston",
            raw_url="https://www.linkedin.com/jobs/view/senior-network-engineer-at-umass-boston-4486561138",
            job_title="Senior Network Engineer",
        )
        assert res["canonical_url"] == "https://careers.massachusetts.edu/mob/en-us/job/530411/senior-network-engineer"
        assert res["is_direct_careers"] is True

