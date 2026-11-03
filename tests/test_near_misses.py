"""Almost-matching jobs are kept and retrievable, and the feed pages (f317).

The scan dropped everything under the threshold, so a 55 never surfaced
anywhere -- not even as a near miss. Jobs scoring 30+ are now stored;
suggestions still filter at the display cutoff, near misses fill the band
below it, and the feed filters by company and pages past the first screen.
"""
import json
import sqlite3

import job_discovery as jd

PROFILE = {"target_titles": ["Network Engineer"], "avoid_title_words": [],
           "locations": ["Remote"], "requires_visa_sponsorship": False,
           "country_code": "US", "open_to_relocation": False}


def job(title, location, url):
    return {"company_name": "Acme", "company_token": "acme", "ats_type": "greenhouse",
            "job_title": title, "location": location, "is_remote": 0, "url": url,
            "description": "", "updated_at": "t", "posted_at": "", "posted_precision": ""}


JOBS = [
    job("Network Engineer", "Remote", "https://x.example/1"),          # 90: suggested
    job("Network Operations Engineer", "Palo Alto", "https://x.example/2"),  # 50: near miss
    job("Plumber", "Austin, TX", "https://x.example/3"),                # 0: dropped
]


def engine(tmp_path, monkeypatch):
    board = tmp_path / "boards.json"
    board.write_text(json.dumps([{"company": "Acme", "token": "acme", "ats": "greenhouse"}]),
                     encoding="utf-8")
    eng = jd.JobDiscoveryEngine(db_path=tmp_path / "d.db", config_path=board)
    eng.profile, eng.resume_text, eng.history = dict(PROFILE), "", []
    monkeypatch.setattr(jd.sources, "reference_employers", lambda *a, **k: [])
    monkeypatch.setattr(jd, "fetch_jobs_for_company", lambda entry, **kw: [dict(j) for j in JOBS])
    return eng


def test_near_misses_are_stored_but_not_suggested(tmp_path, monkeypatch):
    eng = engine(tmp_path, monkeypatch)
    eng.scan_all(min_match_threshold=60)
    assert [j["url"] for j in eng.get_discovered_jobs(min_score=60)] == ["https://x.example/1"]
    near = eng.get_near_misses(display_threshold=60)
    assert [(j["url"], j["match_score"]) for j in near] == [("https://x.example/2", 50)]


def test_the_feed_filters_by_company_and_pages(tmp_path, monkeypatch):
    eng = engine(tmp_path, monkeypatch)
    eng.scan_all(min_match_threshold=0)
    assert eng.get_discovered_jobs(min_score=0, company_like="acm")
    assert eng.get_discovered_jobs(min_score=0, company_like="nope") == []
    page1 = eng.get_discovered_jobs(min_score=0, limit=1, offset=0)
    page2 = eng.get_discovered_jobs(min_score=0, limit=1, offset=1)
    assert page1 and page2 and page1[0]["url"] != page2[0]["url"]


def test_dropping_everything_below_thirty(tmp_path, monkeypatch):
    eng = engine(tmp_path, monkeypatch)
    eng.scan_all(min_match_threshold=60)
    conn = sqlite3.connect(tmp_path / "d.db")
    try:
        urls = {r[0] for r in conn.execute("SELECT url FROM discovered_jobs")}
    finally:
        conn.close()
    assert urls == {"https://x.example/1", "https://x.example/2"}
