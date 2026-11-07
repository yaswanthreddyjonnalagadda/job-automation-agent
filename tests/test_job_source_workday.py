"""Tests for Card 3: Workday CXS Reader Rewrite.

Requirements under test:
- Parse both host shapes and drop locale segment correctly.
- Uncapped board pagination (e.g. 45 postings) yields complete True.
- Total count latching: page 2 sending total=0 preserves page 1 total.
- Capped board (total 2000) splits by optimal facet dimension without duplicates.
- Facets with sum < 2000 (such as distance=5) are never chosen.
- Exhausting depth (depth >= 3) or unresolvable dimensions yields complete False, error "capped_unsplittable".
- Failed detail GET calls keep posting with description="" and complete True.
- known_ids callback bypasses detail fetching.
- external_id strictly equals externalPath.
- Tests are strictly offline using local fixtures and SQLite temp storage.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from services.diff_engine.storage import JobStorage
from services.poller.http import BoardNotFound, HttpError
from services.poller.reader_result import ReaderResult
from services.poller.workday import (
    CappedBoardError,
    parse_workday_board,
    pick_split_dimension,
    read_workday_board,
    splittable_facets,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "job_source" / "workday"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. Host shapes and locale segment parsing
# ---------------------------------------------------------------------------

def test_parse_workday_board_shapes_and_locale():
    # Shape 1: host label tenant, without locale
    host1, tenant1, site1, prefix1 = parse_workday_board("https://acme.wd1.myworkdayjobs.com/Careers")
    assert host1 == "acme.wd1.myworkdayjobs.com"
    assert tenant1 == "acme"
    assert site1 == "Careers"
    assert prefix1 == "/Careers"

    # Shape 1: with locale /en-US/
    host2, tenant2, site2, prefix2 = parse_workday_board("https://acme.wd1.myworkdayjobs.com/en-US/Careers")
    assert host2 == "acme.wd1.myworkdayjobs.com"
    assert tenant2 == "acme"
    assert site2 == "Careers"
    assert prefix2 == "/Careers"

    # Shape 1: no https scheme, lowercase locale
    host3, tenant3, site3, prefix3 = parse_workday_board("acme-corp.wd5.myworkdayjobs.com/fr/External_Site")
    assert host3 == "acme-corp.wd5.myworkdayjobs.com"
    assert tenant3 == "acme-corp"
    assert site3 == "External_Site"
    assert prefix3 == "/External_Site"

    # Shape 2: myworkdaysite.com recruiting path
    host4, tenant4, site4, prefix4 = parse_workday_board("https://wd1.myworkdaysite.com/recruiting/acme/Site")
    assert host4 == "wd1.myworkdaysite.com"
    assert tenant4 == "acme"
    assert site4 == "Site"
    assert prefix4 == "/recruiting/acme/Site"

    # Shape 2: with locale /en-US/
    host5, tenant5, site5, prefix5 = parse_workday_board("https://wd2.myworkdaysite.com/recruiting/acme/en-US/Site")
    assert host5 == "wd2.myworkdaysite.com"
    assert tenant5 == "acme"
    assert site5 == "Site"
    assert prefix5 == "/recruiting/acme/Site"

    # Invalid hosts should raise BoardNotFound
    with pytest.raises(BoardNotFound):
        parse_workday_board("https://notworkday.com/Careers")

    with pytest.raises(BoardNotFound):
        parse_workday_board("https://evil.wd1.myworkdayjobs.com.attacker.org/Careers")


# ---------------------------------------------------------------------------
# 2. Uncapped board -> 45 postings, complete True
# ---------------------------------------------------------------------------

def test_uncapped_board_reads_all_45_postings():
    p1 = load_fixture("uncapped_p1.json")
    p2 = load_fixture("uncapped_p2.json")
    detail = load_fixture("detail_ok.json")

    def mock_post(session, url, body=None):
        offset = body.get("offset", 0) if body else 0
        if offset == 0:
            return p1
        elif offset == 20:
            return p2
        return {"total": 45, "jobPostings": []}

    with patch("services.poller.workday.post_json", side_effect=mock_post):
        with patch("services.poller.workday.get_json", return_value=detail):
            res = read_workday_board("https://acme.wd1.myworkdayjobs.com/Careers")
            assert isinstance(res, ReaderResult)
            assert len(res.postings) == 45
            assert res.complete is True
            assert res.error is None
            # Check detail enrichment
            assert res.postings[0]["description"] == "Build high-scale systems.\nWork with Kubernetes & Python."
            assert res.postings[0]["remote"] is True
            assert res.postings[0]["posted_at"] == "2026-11-01"


# ---------------------------------------------------------------------------
# 3. Latch case -> all 30 postings, not 20
# ---------------------------------------------------------------------------

def test_latch_case_reads_all_30_postings():
    p1 = load_fixture("latch_p1.json")  # total: 30, len 20
    p2 = load_fixture("latch_p2.json")  # total: 0, len 10
    detail = load_fixture("detail_ok.json")

    def mock_post(session, url, body=None):
        offset = body.get("offset", 0) if body else 0
        if offset == 0:
            return p1
        return p2

    with patch("services.poller.workday.post_json", side_effect=mock_post):
        with patch("services.poller.workday.get_json", return_value=detail):
            res = read_workday_board("https://acme.wd1.myworkdayjobs.com/Careers")
            assert isinstance(res, ReaderResult)
            assert len(res.postings) == 30
            assert res.complete is True
            assert res.error is None


# ---------------------------------------------------------------------------
# 4. Capped board -> splits by facet, deduplicates
# ---------------------------------------------------------------------------

def test_capped_board_splits_facets_and_deduplicates():
    root = load_fixture("capped_root.json")
    slice_eng = load_fixture("slice_eng.json")
    slice_sales = load_fixture("slice_sales.json")
    slice_reg = load_fixture("slice_sales_regular.json")
    slice_con = load_fixture("slice_sales_contract.json")
    detail = load_fixture("detail_ok.json")

    def mock_post(session, url, body=None):
        facets = body.get("appliedFacets", {}) if body else {}
        if not facets:
            return root
        if facets.get("jobFamilyGroup") == ["eng"]:
            return slice_eng
        if facets.get("jobFamilyGroup") == ["sales"]:
            if facets.get("workerSubType") == ["regular"]:
                return slice_reg
            if facets.get("workerSubType") == ["contract"]:
                return slice_con
            return slice_sales
        return {"total": 0, "jobPostings": []}

    with patch("services.poller.workday.post_json", side_effect=mock_post):
        with patch("services.poller.workday.get_json", return_value=detail):
            res = read_workday_board("https://acme.wd1.myworkdayjobs.com/Careers")
            assert isinstance(res, ReaderResult)
            assert res.complete is True
            assert res.error is None
            # eng (2) + sales_regular (1) + sales_contract (1) = 4 postings
            assert len(res.postings) == 4
            ids = {p["external_id"] for p in res.postings}
            assert ids == {"/job/eng-1", "/job/eng-2", "/job/sales-reg-1", "/job/sales-con-1"}


# ---------------------------------------------------------------------------
# 5. Facet with sum < 2000 (distance=5) is never chosen
# ---------------------------------------------------------------------------

def test_distance_facet_under_2000_is_never_chosen():
    root = load_fixture("capped_root.json")
    facets = root["facets"]
    # With no facets used:
    # jobFamilyGroup: largest=2600, locations: largest=2700, distance: sum=5
    dim = pick_split_dimension(facets, used=set())
    assert dim is not None
    assert dim.parameter == "jobFamilyGroup"

    # With jobFamilyGroup already used:
    dim2 = pick_split_dimension(facets, used={"jobFamilyGroup"})
    assert dim2 is not None
    assert dim2.parameter == "locations"

    # With both jobFamilyGroup and locations used: only distance remains (sum 5 < 2000)
    dim3 = pick_split_dimension(facets, used={"jobFamilyGroup", "locations"})
    assert dim3 is None


# ---------------------------------------------------------------------------
# 6. Still capped at depth 3 -> complete False, error capped_unsplittable
# ---------------------------------------------------------------------------

def test_still_capped_at_depth_3_returns_capped_unsplittable():
    # Board always returns total 2000 and the same split dimension
    infinite_capped = {
        "total": 2000,
        "jobPostings": [{"title": "Job", "externalPath": "/job/inf-1"}],
        "facets": [
            {
                "facetParameter": "category",
                "values": [
                    {"id": "c1", "count": 2100, "descriptor": "Cat 1"},
                    {"id": "c2", "count": 2100, "descriptor": "Cat 2"},
                ]
            }
        ]
    }

    with patch("services.poller.workday.post_json", return_value=infinite_capped):
        res = read_workday_board("https://acme.wd1.myworkdayjobs.com/Careers")
        assert isinstance(res, ReaderResult)
        assert res.complete is False
        assert res.error == "capped_unsplittable"
        assert res.postings == []


# ---------------------------------------------------------------------------
# 7. Detail fails -> posting kept, description "", complete True
# ---------------------------------------------------------------------------

def test_detail_fails_keeps_posting_with_empty_description():
    p1 = {
        "total": 1,
        "jobPostings": [
            {"title": "Fail Detail Job", "externalPath": "/job/fail-detail-1", "locationsText": "Remote"}
        ]
    }

    with patch("services.poller.workday.post_json", return_value=p1):
        with patch("services.poller.workday.get_json", side_effect=HttpError(status=404, message="Not Found")):
            res = read_workday_board("https://acme.wd1.myworkdayjobs.com/Careers")
            assert isinstance(res, ReaderResult)
            assert res.complete is True
            assert res.error is None
            assert len(res.postings) == 1
            assert res.postings[0]["external_id"] == "/job/fail-detail-1"
            assert res.postings[0]["description"] == ""


# ---------------------------------------------------------------------------
# 8. known_ids -> no detail request made
# ---------------------------------------------------------------------------

def test_known_ids_skips_detail_requests():
    p1 = {
        "total": 2,
        "jobPostings": [
            {"title": "Known Job 1", "externalPath": "/job/known-1", "locationsText": "Remote"},
            {"title": "Unknown Job 2", "externalPath": "/job/unknown-2", "locationsText": "Remote"},
        ]
    }
    detail = load_fixture("detail_ok.json")

    mock_get = MagicMock(return_value=detail)
    with patch("services.poller.workday.post_json", return_value=p1):
        with patch("services.poller.workday.get_json", mock_get):
            known_set = {"/job/known-1"}
            res = read_workday_board(
                "https://acme.wd1.myworkdayjobs.com/Careers",
                known_ids=lambda ext_id: ext_id in known_set,
            )
            assert isinstance(res, ReaderResult)
            assert res.complete is True
            assert len(res.postings) == 2

            # Detail called only for /job/unknown-2, not /job/known-1
            assert mock_get.call_count == 1
            call_url = mock_get.call_args[0][1]
            assert "/job/unknown-2" in call_url

            # /job/known-1 has description ""
            by_id = {p["external_id"]: p for p in res.postings}
            assert by_id["/job/known-1"]["description"] == ""
            assert by_id["/job/unknown-2"]["description"] != ""


# ---------------------------------------------------------------------------
# 9. external_id equals externalPath
# ---------------------------------------------------------------------------

def test_external_id_strictly_equals_external_path():
    p1 = {
        "total": 1,
        "jobPostings": [
            {
                "title": "Complex Path Job",
                "externalPath": "/job/US-WA-Seattle/Software-Engineer_R102030",
                "locationsText": "Seattle, WA",
            }
        ]
    }
    with patch("services.poller.workday.post_json", return_value=p1):
        with patch("services.poller.workday.get_json", side_effect=HttpError(status=404)):
            res = read_workday_board("https://acme.wd1.myworkdayjobs.com/Careers")
            assert len(res.postings) == 1
            # MUST equal the full externalPath, NOT the bullet field or suffix
            assert res.postings[0]["external_id"] == "/job/US-WA-Seattle/Software-Engineer_R102030"


# ---------------------------------------------------------------------------
# 10. Offline SQLite JobStorage safety
# ---------------------------------------------------------------------------

def test_workday_results_applied_to_sqlite_temp_storage(tmp_path):
    db_path = tmp_path / "workday_test.db"
    storage = JobStorage(dsn=f"sqlite:///{db_path}")

    # Verify storage registers and inserts postings safely in offline SQLite mode
    posting = {
        "external_id": "/job/test-1",
        "ats_platform": "workday",
        "company_slug": "acme",
        "title": "Site Reliability Engineer",
        "apply_url": "https://acme.wd1.myworkdayjobs.com/Careers/job/test-1",
        "status": "active",
    }
    job_id = storage.insert_job(posting)
    assert job_id > 0
    active_ids = storage.get_active_job_ids("workday", "acme")
    assert active_ids == {"/job/test-1"}
