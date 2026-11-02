"""Suggested jobs are scored against the owner's resume and the posting's own text, not only its title (1 November 2026).

The percentage on a card was computed from the title, location and seniority; the posting's description was never
fetched for Greenhouse boards and matching_engine.evaluate (resume against posting) was never run on a suggestion.
Classes covered: a score is computed from what it claims to compare; a missing input gives no score (never a made-up
one); the stored table upgrades in place; the list is ranked by the better evidence first.
"""
import json
import sqlite3
from types import SimpleNamespace

import pytest

import job_discovery as jd
import matching_engine

PROFILE = {"target_titles": ["Network Engineer", "Senior Network Engineer"], "locations": ["Remote"],
           "requires_visa_sponsorship": False, "skills": []}
JD = "Requirements: BGP, OSPF and Terraform. Nice to have: Python."
RESUME_GOOD = "Senior network engineer. Designed BGP and OSPF networks, automated with Terraform and Python."
RESUME_BAD = "Pastry chef with ten years of experience in bakeries."


@pytest.fixture(autouse=True)
def vocabulary(monkeypatch):
    monkeypatch.setattr(matching_engine, "load_vocabulary", lambda: ["BGP", "OSPF", "Terraform", "Python", "Kubernetes"])


def make_engine(tmp_path, resume, jobs):
    db = tmp_path / "d.db"
    board = tmp_path / "boards.json"
    board.write_text(json.dumps([{"company": "Acme", "token": "acme", "ats": "greenhouse"}]), encoding="utf-8")
    engine = jd.JobDiscoveryEngine(db_path=db, config_path=board)
    engine.profile = PROFILE
    engine.resume_text = resume
    jd.fetch_jobs_for_company = lambda entry, timeout=8.0: jobs      # restored by the fixture below
    return engine


@pytest.fixture(autouse=True)
def restore_fetch():
    original = jd.fetch_jobs_for_company
    yield
    jd.fetch_jobs_for_company = original


def job(n=1, title="Senior Network Engineer", description=JD):
    return {"company_name": "Acme", "company_token": "acme", "ats_type": "greenhouse", "job_title": title,
            "location": "Remote", "is_remote": 1, "url": f"https://x/{n}", "description": description,
            "updated_at": "2026-11-01T00:00:00Z"}


def rows(engine, **kw):
    return engine.get_discovered_jobs(min_score=0, **kw)


# -- the posting's text ------------------------------------------------------------------------------------------

def test_html_from_a_board_becomes_plain_text():
    assert jd.html_to_text("&lt;p&gt;Requirements:&lt;/p&gt;&lt;ul&gt;&lt;li&gt;BGP&lt;/li&gt;&lt;li&gt;OSPF&lt;/li&gt;&lt;/ul&gt;") \
        == "Requirements: BGP OSPF"
    assert jd.html_to_text("<p>A &amp; B</p><script>x()</script>") == "A & B"
    assert jd.html_to_text("") == ""


def test_greenhouse_is_asked_for_the_description_and_returns_it(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen["url"] = url
        body = {"jobs": [{"title": "Network Engineer", "location": {"name": "Remote"}, "absolute_url": "https://x/1",
                          "content": "&lt;p&gt;Requirements: BGP&lt;/p&gt;"}]}
        return SimpleNamespace(status_code=200, json=lambda: body)
    monkeypatch.setattr(jd.requests, "get", fake_get)
    found = jd.fetch_greenhouse_feed("acme", "Acme")
    assert "content=true" in seen["url"]
    assert found[0]["description"] == "Requirements: BGP"


# -- the resume's text -------------------------------------------------------------------------------------------

def test_a_text_resume_is_read_and_a_missing_one_is_empty(tmp_path):
    path = tmp_path / "resume.txt"
    path.write_text("BGP and OSPF", encoding="utf-8")
    assert jd.load_resume_text(path) == "BGP and OSPF"
    assert jd.load_resume_text(tmp_path / "nope.pdf") == ""
    assert jd.load_resume_text(None) == "" or isinstance(jd.load_resume_text(None), str)


# -- the score ---------------------------------------------------------------------------------------------------

def test_the_table_of_an_older_install_gains_the_new_columns_in_place(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.execute("CREATE TABLE discovered_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, dedup_key TEXT UNIQUE, "
                 "company_name TEXT NOT NULL, company_token TEXT NOT NULL, ats_type TEXT NOT NULL, job_title TEXT NOT NULL, "
                 "location TEXT, is_remote INTEGER DEFAULT 0, url TEXT NOT NULL, match_score INTEGER DEFAULT 0, "
                 "matched_skills TEXT DEFAULT '[]', status TEXT DEFAULT 'discovered', discovered_at TEXT NOT NULL, "
                 "updated_at TEXT NOT NULL)")
    conn.execute("INSERT INTO discovered_jobs (dedup_key, company_name, company_token, ats_type, job_title, url, "
                 "discovered_at, updated_at) VALUES ('k','A','a','greenhouse','T','u','2026-10-01','2026-10-01')")
    jd.ensure_discovery_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(discovered_jobs)")}
    assert {"resume_score", "resume_matched", "resume_missing"} <= cols
    assert conn.execute("SELECT count(*) FROM discovered_jobs").fetchone()[0] == 1       # nothing lost


def test_a_scan_scores_the_resume_against_the_posting(tmp_path):
    engine = make_engine(tmp_path, RESUME_GOOD, [job()])
    engine.scan_all(min_match_threshold=0)
    got = rows(engine)[0]
    assert got["resume_score"] >= 60
    assert "BGP" in got["resume_matched"] and got["resume_missing"] == []


def test_a_resume_that_does_not_fit_scores_low_and_names_what_is_missing(tmp_path):
    engine = make_engine(tmp_path, RESUME_BAD, [job()])
    engine.scan_all(min_match_threshold=0)
    got = rows(engine)[0]
    assert got["resume_score"] < 25 and "BGP" in got["resume_missing"]


def test_a_posting_with_no_description_gets_no_resume_score_not_a_guess(tmp_path):
    engine = make_engine(tmp_path, RESUME_GOOD, [job(description="")])
    engine.scan_all(min_match_threshold=0)
    got = rows(engine)[0]
    assert got["resume_score"] is None and got["resume_matched"] == []


def test_no_resume_means_no_resume_score(tmp_path):
    engine = make_engine(tmp_path, "", [job()])
    engine.scan_all(min_match_threshold=0)
    assert rows(engine)[0]["resume_score"] is None


def test_a_rescan_updates_the_score_of_a_row_already_stored(tmp_path):
    engine = make_engine(tmp_path, RESUME_BAD, [job()])
    engine.scan_all(min_match_threshold=0)
    first = rows(engine)[0]["resume_score"]
    engine.resume_text = RESUME_GOOD
    engine.scan_all(min_match_threshold=0)
    assert rows(engine)[0]["resume_score"] > first


def test_the_better_resume_match_is_listed_first_and_unscored_last(tmp_path):
    engine = make_engine(tmp_path, RESUME_GOOD, [
        job(1, description="Requirements: Kubernetes only."),                     # little in common
        job(2, description=JD),                                                   # a close fit
        job(3, description=""),                                                   # nothing to compare
    ])
    engine.scan_all(min_match_threshold=0)
    assert [r["url"] for r in rows(engine)] == ["https://x/2", "https://x/1", "https://x/3"]


# -- the card ----------------------------------------------------------------------------------------------------

@pytest.fixture
def page(monkeypatch):
    import profile_setup
    import web_ui

    def render(jobs):
        monkeypatch.setattr(web_ui, "get_tracker", lambda: SimpleNamespace(
            list_all=lambda: [], documents_for=lambda _i: [], answers_for=lambda _i: [], events=lambda *_a, **_k: []))
        monkeypatch.setattr(web_ui, "_current_runs", lambda: {})
        monkeypatch.setattr(web_ui, "waiting_runs", lambda _d: [])
        monkeypatch.setattr(web_ui, "last_scan", lambda: {})
        monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
        monkeypatch.setattr(jd.JobDiscoveryEngine, "__init__", lambda self, *a, **k: None)
        monkeypatch.setattr(jd.JobDiscoveryEngine, "get_discovered_jobs", lambda self, **k: jobs)
        web_ui.app.config["TESTING"] = True
        return web_ui.app.test_client().get("/").get_data(as_text=True)
    return render


def card(**kw):
    base = dict(id=1, company_name="Acme", job_title="Network Engineer", url="https://x/1", location="Remote",
                is_remote=True, ats_type="greenhouse", match_score=90, matched_skills=["Network"],
                discovered_at="2026-11-01T09:00:00", resume_score=None, resume_matched=[], resume_missing=[])
    base.update(kw)
    return base


def test_a_card_shows_the_resume_match_and_what_is_matched_and_missing(page):
    html = page([card(resume_score=72, resume_matched=["BGP", "OSPF"], resume_missing=["Terraform"])])
    assert "Resume match 72%" in html and 'aria-label="Resume match 72 percent"' in html
    assert ">BGP<" in html and "Posting also asks for" in html and ">Terraform<" in html


def test_a_card_with_no_posting_text_says_there_is_no_resume_match(page):
    html = page([card()])
    assert "Resume match" not in html.split('class="job-card')[1].split("</article>")[0].replace("No resume match", "")
    assert "No resume match: the posting" in html and 'aria-label="Title match 90 percent"' in html
