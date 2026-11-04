"""Jobs are fetched and filtered by country (f312).

The owner asked to fetch jobs based on the country. Every discovered job now
carries the country its location names (a US state counts as the US, "Remote"
with no country stays unknown), old rows gain it on the next schema touch, and
both the engine and the page can ask for one country's jobs.
"""
import json
import sqlite3
from types import SimpleNamespace

import pytest

import geo_reference
import job_discovery as jd

PROFILE = {
    "target_titles": ["Network Engineer"],
    "avoid_title_words": [],
    "locations": ["Remote"],
    "requires_visa_sponsorship": False,
    "country_code": "US",
    "open_to_relocation": False,
}


@pytest.mark.parametrize("location,expected", [
    ("Bengaluru, India", {"IN"}),
    ("Austin, TX", {"US"}),                       # a US state is the US
    ("United States", {"US"}),
    ("Remote", set()),                            # no country named: unknown, not guessed
    ("Palo Alto", {"US"}),                              # f318: known cities resolve
    ("", set()),
    ("Remote - India", {"IN"}),
    ("Austin, TX; London, UK", {"US", "GB"}),     # several places: every country named
    ("Toronto, ON", {"CA"}),                         # f318: known cities resolve
])
def test_a_job_location_names_its_countries(location, expected):
    assert set(geo_reference.location_countries(location)) == expected


@pytest.mark.parametrize("location,home,expected", [
    ("Austin, TX", "US", "US"),
    ("Bengaluru, India", "US", "IN"),             # the one country named, even a foreign one
    ("Austin, TX; London, UK", "US", "US"),       # several: the owner's wins
    ("Austin, TX; London, UK", "", ""),           # several and no home: none wins, unknown
    ("Remote", "US", ""),
])
def test_one_country_is_kept_per_job(location, home, expected):
    assert geo_reference.primary_country(location, home) == expected


def make_engine(tmp_path, monkeypatch, jobs):
    board = tmp_path / "boards.json"
    board.write_text(json.dumps([{"company": "Acme", "token": "acme", "ats": "greenhouse"}]),
                     encoding="utf-8")
    engine = jd.JobDiscoveryEngine(db_path=tmp_path / "d.db", config_path=board)
    engine.profile, engine.resume_text, engine.history = dict(PROFILE), "", []
    monkeypatch.setattr(jd.sources, "reference_employers", lambda *a, **k: [])
    monkeypatch.setattr(jd, "fetch_jobs_for_company",
                        lambda entry, timeout=8.0: [dict(j) for j in jobs]
                        if entry.get("token") == "acme" else [])
    return engine


JOBS = [
    {"company_name": "Acme", "company_token": "acme", "ats_type": "greenhouse",
     "job_title": "Network Engineer", "location": "Austin, TX", "is_remote": 0,
     "url": "https://x.example/1", "description": "", "updated_at": "t",
     "posted_at": "", "posted_precision": ""},
    {"company_name": "Acme", "company_token": "acme", "ats_type": "greenhouse",
     "job_title": "Network Engineer", "location": "Remote", "is_remote": 1,
     "url": "https://x.example/2", "description": "", "updated_at": "t",
     "posted_at": "", "posted_precision": ""},
]


def test_the_scan_stores_each_jobs_country(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, JOBS)
    engine.scan_all()
    conn = sqlite3.connect(tmp_path / "d.db")
    try:
        rows = {r[0]: r[1] for r in
                conn.execute("SELECT location, country_code FROM discovered_jobs")}
    finally:
        conn.close()
    assert rows == {"Austin, TX": "US", "Remote": ""}


def test_jobs_are_fetched_for_one_country(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, JOBS)
    engine.scan_all()
    assert [j["location"] for j in engine.get_discovered_jobs(country_code="US")] == ["Austin, TX"]
    assert len(engine.get_discovered_jobs()) == 2


def test_old_rows_gain_a_country(tmp_path):
    db = tmp_path / "d.db"
    conn = sqlite3.connect(db)
    jd.ensure_discovery_schema(conn)
    conn.execute("INSERT INTO discovered_jobs (dedup_key, company_name, company_token, ats_type, job_title,"
                 " location, is_remote, url, match_score, discovered_at, updated_at, country_code)"
                 " VALUES ('k', 'Acme', 'acme', 'greenhouse', 'Network Engineer', 'Austin, TX', 0,"
                 " 'https://x.example/1', 90, 't', 't', NULL)")
    conn.commit()
    jd.ensure_discovery_schema(conn)          # the next touch backfills it
    row = conn.execute("SELECT country_code FROM discovered_jobs").fetchone()[0]
    conn.close()
    assert row == "US"


def test_a_country_named_nowhere_stays_out_of_reach():
    assert jd._in_reach("Bengaluru, India", "US") is False
    assert jd._in_reach("Austin, TX", "US") is True
    assert jd._in_reach("Remote", "US") is True


# -- the page ----------------------------------------------------------------------------------------------------


@pytest.fixture
def home(monkeypatch):
    import profile_setup
    import web_ui

    asked = {}

    def render(query=""):
        monkeypatch.setattr(web_ui, "get_tracker", lambda: SimpleNamespace(
            list_all=lambda: [], documents_for=lambda _i: [], answers_for=lambda _i: [],
            events=lambda *_a, **_k: []))
        monkeypatch.setattr(web_ui, "_current_runs", lambda: {})
        monkeypatch.setattr(web_ui, "waiting_runs", lambda _d: [])
        monkeypatch.setattr(web_ui, "last_scan", lambda: {})
        monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
        monkeypatch.setattr(jd.JobDiscoveryEngine, "__init__", lambda self, *a, **k: None)

        def fetch(self, **kw):
            asked.update(kw)
            return []
        monkeypatch.setattr(jd.JobDiscoveryEngine, "get_discovered_jobs", fetch)
        web_ui.app.config["TESTING"] = True
        return web_ui.app.test_client().get("/" + query).get_data(as_text=True)
    render.asked = asked
    return render


@pytest.mark.parametrize("query,expected", [("", None), ("?country=US", "US"), ("?country=us", "US"),
                                            ("?country=nonsense", None), ("?country=", None)])
def test_the_page_passes_the_chosen_country(home, query, expected):
    home(query)
    assert home.asked.get("country_code") == expected
