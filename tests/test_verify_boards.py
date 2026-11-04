"""Candidate boards join the scan list only after a live check (f317).

Slugs collide across companies, so reference/candidate_tokens.json is never
scanned directly: scripts/verify_boards.py probes each token, and only a
token whose API answers with jobs AND whose board page names the company is
merged into the owner's list. HTTP is injected, so these tests run offline;
the live run itself is the owner's one command.
"""
import json
from types import SimpleNamespace

from scripts.verify_boards import merge_verified, page_names_company, verify_candidate


def fetch_for(api_payload, api_status=200, title="Jobs"):
    def fetch(url, **kw):
        if "boards-api" in url or "api.lever" in url or "posting-api" in url:
            return SimpleNamespace(status_code=api_status, json=lambda: api_payload, text="")
        return SimpleNamespace(status_code=200, text=f"<html><head><title>{title}</title></head></html>")
    return fetch


def test_a_board_whose_api_and_page_agree_is_verified():
    entry = verify_candidate(
        {"company": "Anthropic", "ats": "greenhouse", "tokens": ["anthropic"]},
        fetch_for({"jobs": [{"id": 1}]}, title="Jobs at Anthropic"))
    assert entry == {"company": "Anthropic", "ats": "greenhouse", "token": "anthropic"}


def test_a_token_without_jobs_is_rejected():
    entry = verify_candidate(
        {"company": "Anthropic", "ats": "greenhouse", "tokens": ["anthropic"]},
        fetch_for({"jobs": []}, title="Jobs at Anthropic"))
    assert entry is None


def test_a_shared_brand_name_still_needs_a_human_eyeball():
    # Two companies can share one brand word ("Lovable" the retailer vs the
    # software company): the page check cannot tell them apart, so the script
    # prints every addition and the owner reviews the diff before it is scanned.
    entry = verify_candidate(
        {"company": "Lovable", "ats": "greenhouse", "tokens": ["lovable"]},
        fetch_for({"jobs": [{"id": 1}]}, title="Lovable Italia Lavora Con Noi"))
    assert entry == {"company": "Lovable", "ats": "greenhouse", "token": "lovable"}


def test_a_page_for_the_wrong_company_is_rejected():
    entry = verify_candidate(
        {"company": "Anthropic", "ats": "greenhouse", "tokens": ["anthropic"]},
        fetch_for({"jobs": [{"id": 1}]}, title="Jobs at Acme Retail"))
    assert entry is None


def test_the_first_working_token_wins_and_failures_move_on():
    calls = []

    def fetch(url, **kw):
        calls.append(url)
        if "grafana/" in url and "boards.greenhouse" in url and "boards-api" not in url:
            return SimpleNamespace(status_code=200, text="<title>Jobs at Grafana Labs</title>")
        if "grafanalabs" in url:
            return SimpleNamespace(status_code=404, json=lambda: {}, text="")
        return SimpleNamespace(status_code=200, json=lambda: {"jobs": [{"id": 1}]},
                               text="<title>Jobs at Grafana Labs</title>")
    entry = verify_candidate(
        {"company": "Grafana Labs", "ats": "greenhouse", "tokens": ["grafana", "grafanalabs"]}, fetch)
    assert entry["token"] == "grafana"


def test_page_titles_need_every_long_word():
    assert page_names_company("<title>Jobs at Grafana Labs</title>", "Grafana Labs") is True
    assert page_names_company("<title>Jobs</title>", "Grafana Labs") is False
    assert page_names_company("no title here", "Grafana Labs") is False


def test_merge_adds_each_employer_once_sorted(tmp_path):
    targets = tmp_path / "t.json"
    targets.write_text(json.dumps([{"company": "Mine", "ats": "greenhouse", "token": "mine"}]),
                       encoding="utf-8")
    merged = merge_verified(targets, [
        {"company": "Anthropic", "ats": "greenhouse", "token": "anthropic"},
        {"company": "Mine (again)", "ats": "greenhouse", "token": "mine"},
    ])
    assert [e["company"] for e in merged] == ["Anthropic", "Mine"]


def test_candidates_are_shaped_and_exclude_services_firms():
    from tests.test_reference_employers import NOT_WANTED
    rows = json.loads((__import__("pathlib").Path(__file__).resolve().parent.parent
                       / "reference" / "candidate_tokens.json").read_text(encoding="utf-8"))["candidates"]
    assert len(rows) >= 50
    for row in rows:
        assert row["company"].strip() and row["ats"] in {"greenhouse", "ashby", "lever"} and row["tokens"]
        name = row["company"].lower()
        assert not any(bad in name for bad in NOT_WANTED), row["company"]
