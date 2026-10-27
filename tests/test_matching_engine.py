"""Four-stage match scoring (matching_engine.py / P3-2)."""
import json

import matching_engine as me

PROFILE = {
    "requires_visa_sponsorship": True,
    "locations": ["Dallas, TX", "Ashburn, VA", "Remote"],
    "skills": ["BGP", "Terraform", "Python"],
}
RESUME = "Network engineer. BGP, OSPF, MPLS, Terraform, Python automation on AWS."
GOOD_JD = """Senior Network Engineer
Requirements:
- Strong BGP and MPLS experience
- Python automation, Terraform
Preferred:
- Golang, GCP
"""


def test_sponsorship_exclusion_disqualifies_a_sponsorship_seeker():
    r = me.evaluate("We are unable to sponsor visas. BGP.", PROFILE, RESUME)
    assert not r.is_eligible and r.overall_score == 0
    assert "sponsor" in r.disqualification_reason.lower()


def test_sponsorship_exclusion_ignored_when_owner_needs_none():
    r = me.evaluate("Unable to sponsor visas. BGP required.", {**PROFILE, "requires_visa_sponsorship": False}, RESUME)
    assert r.is_eligible


def test_clearance_disqualifies():
    r = me.evaluate("Active TS/SCI clearance required. BGP.", PROFILE, RESUME)
    assert not r.is_eligible and "clearance" in r.disqualification_reason.lower()


def test_onsite_elsewhere_disqualifies_but_remote_and_local_pass():
    assert not me.check_hard_gates("Onsite role.", PROFILE, "Springfield, ZZ")[0]
    assert me.check_hard_gates("Onsite role.", PROFILE, "Dallas, TX")[0]
    assert me.check_hard_gates("Remote friendly.", PROFILE, "Springfield, ZZ")[0]


def test_lexical_weights_required_over_preferred_and_reports_missing():
    score, matched, missing = me.score_lexical(GOOD_JD, RESUME, PROFILE["skills"])
    assert {"BGP", "MPLS", "Python", "Terraform"} <= set(matched)
    assert {"Golang", "GCP"} <= set(missing)
    assert 80 < score < 100  # 12 of 14 weighted points


def test_skill_match_is_whole_word():
    _, matched, _ = me.score_lexical("Requirements: Go", "I use Django and Golang tools", [], ["Go"])
    assert matched == []


def test_semantic_identical_high_disjoint_zero():
    assert me.score_semantic("bgp mpls routing", "bgp mpls routing") > 99
    assert me.score_semantic("bgp mpls routing", "cooking baking pastry") == 0


def test_llm_stage_blends_and_bad_reply_is_dropped():
    good = me.evaluate(GOOD_JD, PROFILE, RESUME, llm=lambda p: '{"score": 100, "summary": "Strong."}')
    none = me.evaluate(GOOD_JD, PROFILE, RESUME)
    bad = me.evaluate(GOOD_JD, PROFILE, RESUME, llm=lambda p: "no json here")
    boom = me.evaluate(GOOD_JD, PROFILE, RESUME, llm=lambda p: 1 / 0)
    assert good.stage_breakdown["llm"] == 100 and good.summary == "Strong."
    assert "llm" not in bad.stage_breakdown and bad.overall_score == none.overall_score
    assert boom.overall_score == none.overall_score


def test_high_fit_role_scores_high_and_irrelevant_low():
    hi = me.evaluate(GOOD_JD, PROFILE, RESUME)
    lo = me.evaluate("Requirements: Java, Kubernetes, Docker, Jenkins", PROFILE, RESUME)
    assert hi.is_eligible and hi.overall_score >= 70
    assert lo.overall_score < hi.overall_score


def test_vocabulary_file_is_valid():
    assert me.load_vocabulary() and all(isinstance(s, str) for s in me.load_vocabulary())


def test_discovery_skips_a_posting_whose_text_fails_a_gate(tmp_path):
    from unittest.mock import patch
    import job_discovery as jd
    posting = {"company_name": "Acme", "company_token": "acme", "ats_type": "lever",
               "job_title": "Senior Network Engineer", "location": "Dallas, TX", "is_remote": 0,
               "url": "https://x/1", "description": "We are unable to sponsor visas."}
    ok = dict(posting, url="https://x/2", description="Great team. BGP.")
    engine = jd.JobDiscoveryEngine.__new__(jd.JobDiscoveryEngine)
    engine.profile = PROFILE | {"target_titles": ["Senior Network Engineer"]}
    engine.db_path = tmp_path / "a.db"
    engine.config_path = tmp_path / "none.json"
    engine._get_connection = lambda: __import__("sqlite3").connect(str(engine.db_path))
    conn = engine._get_connection(); jd.ensure_discovery_schema(conn); conn.commit(); conn.close()
    engine.get_target_companies = lambda: [{"company": "Acme", "token": "acme", "ats": "lever"}]
    with patch.object(jd, "fetch_jobs_for_company", return_value=[posting, ok]):
        stats = engine.scan_all(min_match_threshold=10)
    assert stats["relevant_jobs_saved"] == 1
