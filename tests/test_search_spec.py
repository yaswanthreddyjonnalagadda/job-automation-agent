"""Tests for Phase 1: Profile-Driven SearchSpec (Pydantic validation, Claude parsing, storage, and query expansion)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.diff_engine.storage import JobStorage
from services.matcher.search_spec import SearchSpec
from services.matcher.search_spec_parser import (
    bootstrap_search_spec_from_profile,
    parse_search_instructions,
)


def test_search_spec_pydantic_validation():
    """SearchSpec validates types and provides sensible defaults."""
    spec = SearchSpec(
        raw_instructions="Looking for Senior Network Engineer roles in Virginia or Remote paying at least 130k",
        target_titles=["Network Engineer", "Senior Network Engineer"],
        title_synonyms={"Network Engineer": ["Network Infrastructure Engineer"]},
        must_have_keywords=["cisco", "bgp"],
        nice_to_have_keywords=["python", "ansible"],
        locations=["Fairfax, VA", "Remote"],
        salary_floor_annual=130000,
        requires_visa_sponsorship=True,
    )
    assert spec.version == 1
    assert spec.salary_floor_annual == 130000
    assert "Network Engineer" in spec.target_titles
    assert spec.hard_criteria["title"] is True
    assert spec.hard_criteria["sponsorship"] is True


def test_search_spec_rejects_negative_salary():
    """Negative salary floors must fail validation."""
    with pytest.raises(ValueError):
        SearchSpec(
            raw_instructions="test",
            target_titles=["Network Engineer"],
            salary_floor_annual=-5000,
        )


def test_query_expansion_from_search_spec():
    """Generates cross-product of titles, synonyms, and location targets within budget."""
    spec = SearchSpec(
        raw_instructions="Target network engineering roles",
        target_titles=["Network Engineer", "Cloud Network Engineer"],
        title_synonyms={
            "Network Engineer": ["Network Deployment Engineer"],
        },
        locations=["Remote", "Fairfax, VA"],
    )
    queries = spec.get_expanded_queries(max_queries=10)
    assert len(queries) > 0
    assert any("Network Engineer" in q["title"] for q in queries)
    assert any(q["location"] == "Remote" for q in queries)
    assert any(q["location"] == "Fairfax, VA" for q in queries)


def test_parse_search_instructions_with_claude_mock():
    """Claude parser transforms free-text into a validated SearchSpec."""
    mock_claude_json = {
        "target_titles": ["Senior Network Engineer", "Network Security Engineer"],
        "title_synonyms": {
            "Senior Network Engineer": ["Lead Network Engineer"],
        },
        "adjacent_titles": ["Infrastructure Engineer"],
        "seniority_levels": ["Senior", "Lead"],
        "must_have_keywords": ["cisco", "bgp", "sd-wan"],
        "nice_to_have_keywords": ["terraform", "python"],
        "locations": ["Fairfax, VA", "Remote"],
        "workplace_types": ["remote", "hybrid"],
        "salary_floor_annual": 135000,
        "salary_currency": "USD",
        "employment_types": ["full-time"],
        "requires_visa_sponsorship": True,
        "excluded_companies": ["CyberCoders"],
        "excluded_keywords": ["security clearance", "polygraph", "sales"],
        "posting_age_window_hours": 72,
        "hard_criteria": {
            "sponsorship": True,
            "location": True,
            "title": True,
            "salary": False,
        },
    }

    raw_text = "I am a Senior Network Engineer looking for Remote or Fairfax VA roles. Need sponsorship, min 135k, no clearances."

    with patch("services.matcher.search_spec_parser._call_claude_api", return_value=mock_claude_json):
        spec = parse_search_instructions(raw_text, api_key="test-key")
        assert isinstance(spec, SearchSpec)
        assert spec.salary_floor_annual == 135000
        assert spec.target_titles == ["Senior Network Engineer", "Network Security Engineer"]
        assert spec.requires_visa_sponsorship is True
        assert spec.raw_instructions == raw_text


def test_storage_save_and_retrieve_search_spec(tmp_path):
    """SearchSpec is versioned and saved in database storage."""
    db_file = tmp_path / "test_storage.db"
    storage = JobStorage(dsn=f"sqlite:///{db_file}")

    spec1 = SearchSpec(
        version=1,
        raw_instructions="Version 1 instructions",
        target_titles=["Network Engineer"],
        locations=["Remote"],
    )
    spec_id1 = storage.save_search_spec(spec1)
    assert spec_id1 > 0

    active = storage.get_active_search_spec()
    assert active is not None
    assert active.version == 1
    assert active.target_titles == ["Network Engineer"]

    # Save Version 2
    spec2 = SearchSpec(
        version=2,
        raw_instructions="Version 2 instructions",
        target_titles=["Senior Network Engineer", "Cloud Network Engineer"],
        locations=["Fairfax, VA", "Remote"],
    )
    spec_id2 = storage.save_search_spec(spec2)
    assert spec_id2 > 0

    # Active spec should now be Version 2
    active2 = storage.get_active_search_spec()
    assert active2 is not None
    assert active2.version == 2
    assert "Cloud Network Engineer" in active2.target_titles

    # Version 1 should still exist in history
    versions = storage.list_search_spec_versions()
    assert len(versions) == 2
    assert versions[0]["version"] == 2
    assert versions[1]["version"] == 1


def test_bootstrap_from_profile_yaml():
    """Bootstraps a valid SearchSpec from existing config/user_profile.yaml."""
    spec = bootstrap_search_spec_from_profile()
    assert isinstance(spec, SearchSpec)
    assert len(spec.target_titles) > 0
    assert "Network Engineer" in spec.target_titles
    assert spec.is_active is True

