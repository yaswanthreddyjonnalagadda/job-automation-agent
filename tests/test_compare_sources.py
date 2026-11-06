"""Tests for scripts/compare_sources.py (Card 7): a read-only comparison of the legacy
job_discovery feed against the new job-source service's open_jobs view, written as one
entry per day to a JSON file.

Offline only: both `fetch_old_jobs` and `fetch_new_jobs` are monkeypatched with fixed
in-memory lists rather than hitting a real database of either kind, so this never touches
job-agent-db, data/applications.db, or any SQLite file.
"""
from __future__ import annotations

import json

from scripts import compare_sources


OLD_JOBS = [
    {"company_name": "Acme Corp", "job_title": "Network Engineer", "location": "Remote"},
    {"company_name": "Acme Corp", "job_title": "Site Reliability Engineer", "location": "NYC"},
    {"company_name": "Globex", "job_title": "Cloud Engineer", "location": "Austin, TX"},
]

NEW_JOBS = [
    # Same role as OLD_JOBS[0], different casing/punctuation -- normalisation should match it.
    {"company_name": "ACME CORP.", "job_title": "network engineer", "location": "remote"},
    {"company_name": "Initech", "job_title": "Platform Engineer", "location": "Denver, CO"},
]


def test_compare_matches_by_normalised_company_title_location():
    result = compare_sources.compare(OLD_JOBS, NEW_JOBS)

    assert result["old_count"] == 3
    assert result["new_count"] == 2
    assert result["both"] == 1
    assert result["only_in_old"] == ["Acme Corp | Site Reliability Engineer", "Globex | Cloud Engineer"]
    assert result["only_in_new"] == ["Initech | Platform Engineer"]


def test_compare_caps_each_list_at_twenty():
    many_old = [
        {"company_name": f"Co{i}", "job_title": "Role", "location": "Remote"} for i in range(30)
    ]
    result = compare_sources.compare(many_old, [])
    assert len(result["only_in_old"]) == 20
    assert result["old_count"] == 30


def test_run_writes_one_entry_with_the_right_counts_and_lists(tmp_path, monkeypatch):
    monkeypatch.setattr(compare_sources, "fetch_old_jobs", lambda days, min_score: OLD_JOBS)
    monkeypatch.setattr(compare_sources, "fetch_new_jobs", lambda days, min_score: NEW_JOBS)
    out_path = tmp_path / "_source_compare.json"

    entry = compare_sources.run(days=7, min_score=60, out_path=out_path)

    assert entry["old_count"] == 3
    assert entry["new_count"] == 2
    assert entry["both"] == 1
    assert "date" in entry

    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written == [entry]


def test_run_twice_same_day_replaces_rather_than_duplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(compare_sources, "fetch_old_jobs", lambda days, min_score: OLD_JOBS)
    monkeypatch.setattr(compare_sources, "fetch_new_jobs", lambda days, min_score: NEW_JOBS)
    out_path = tmp_path / "_source_compare.json"

    compare_sources.run(days=7, min_score=60, out_path=out_path)
    monkeypatch.setattr(compare_sources, "fetch_new_jobs", lambda days, min_score: [])
    second = compare_sources.run(days=7, min_score=60, out_path=out_path)

    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert len(written) == 1
    assert written[0]["new_count"] == 0
    assert written[0] == second


def test_run_preserves_a_prior_days_entry(tmp_path, monkeypatch):
    out_path = tmp_path / "_source_compare.json"
    out_path.write_text(json.dumps([{"date": "2020-01-01", "old_count": 1, "new_count": 1,
                                      "only_in_old": [], "only_in_new": [], "both": 1}]), encoding="utf-8")
    monkeypatch.setattr(compare_sources, "fetch_old_jobs", lambda days, min_score: OLD_JOBS)
    monkeypatch.setattr(compare_sources, "fetch_new_jobs", lambda days, min_score: NEW_JOBS)

    compare_sources.run(days=7, min_score=60, out_path=out_path)

    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert len(written) == 2
    assert {e["date"] for e in written} >= {"2020-01-01"}
