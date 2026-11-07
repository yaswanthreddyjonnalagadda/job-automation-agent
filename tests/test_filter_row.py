"""Suggested jobs filter by date, country, workplace, company and score (f325).

The page read `?country=` but offered no control for it, and picking a time
window silently dropped it; company and score had engine support with no
route wiring, and workplace only a client-side chip. Now one filter row
(plain GET, so no-JavaScript works) carries country, company, minimum score
and workplace, the time-window chips keep every other filter, and an active
filter shows what is on with a way to clear it.
"""
import sqlite3
from types import SimpleNamespace

import pytest

import job_discovery as jd


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
        monkeypatch.setattr(jd, "get_discovered_countries", lambda: ["CA", "US"])

        def fetch(self, **kw):
            asked.update(kw)
            return []
        monkeypatch.setattr(jd.JobDiscoveryEngine, "get_discovered_jobs", fetch)
        web_ui.app.config["TESTING"] = True
        return web_ui.app.test_client().get("/" + query).get_data(as_text=True)
    render.asked = asked
    return render


@pytest.mark.parametrize("query,expected", [
    ("", {"company_like": None, "title_like": None, "min_score": 60, "remote_only": None}),
    ("?company=acme", {"company_like": "acme", "title_like": None, "min_score": 60, "remote_only": None}),
    ("?company=%20%20", {"company_like": None, "title_like": None, "min_score": 60, "remote_only": None}),
    ("?title=Network", {"company_like": None, "title_like": "Network", "min_score": 60, "remote_only": None}),
    ("?title=%20%20", {"company_like": None, "title_like": None, "min_score": 60, "remote_only": None}),
    ("?min_score=80", {"company_like": None, "title_like": None, "min_score": 80, "remote_only": None}),
    ("?min_score=banana", {"company_like": None, "title_like": None, "min_score": 60, "remote_only": None}),
    ("?min_score=140", {"company_like": None, "title_like": None, "min_score": 100, "remote_only": None}),
    ("?workplace=remote", {"company_like": None, "title_like": None, "min_score": 60, "remote_only": True}),
    ("?workplace=onsite", {"company_like": None, "title_like": None, "min_score": 60, "remote_only": False}),
    ("?workplace=moon", {"company_like": None, "title_like": None, "min_score": 60, "remote_only": None}),
])
def test_the_page_passes_each_filter_to_the_query(home, query, expected):
    home(query)
    for key, value in expected.items():
        assert home.asked.get(key) == value, key


def test_time_window_chips_keep_every_other_filter(home):
    html = home("?country=CA&company=acme&min_score=80&workplace=remote")
    assert "No jobs match those filters" in html
    for key in ("1h", "24h", "3d", "7d"):
        assert f"/?within={key}&amp;country=CA&amp;company=acme&amp;min_score=80&amp;workplace=remote#jobs-h" in html


def test_the_summary_names_the_filters_and_clears_them(home):
    html = home("?country=CA&workplace=remote&min_score=70")
    assert "Canada" in html and "Remote" in html and "70%+" in html
    assert 'href="/#jobs-h">Clear</a>' in html
    plain = home("")
    assert "Clear</a>" not in plain.split('id="jobs-h"')[1].split("</section>")[0]


def test_the_country_select_lists_countries_with_suggestions(home):
    html = home("?country=CA")
    assert '<select name="country"' in html
    assert '<option value="">All countries</option>' in html
    assert '<option value="CA" selected>Canada</option>' in html
    assert '<option value="US">United States</option>' in html


def test_the_row_carries_company_score_and_workplace(home):
    html = home("?title=Network&company=acme&min_score=85&workplace=onsite")
    assert '<form method="get" action="/" id="job-filters"' in html
    assert 'name="title"' in html and 'value="Network"' in html
    assert 'name="company"' in html and 'value="acme"' in html
    assert '<option value="85" selected>' in html
    assert '<option value="onsite" selected>' in html
    assert 'name="within" value="7d"' in html


def seed(path):
    conn = sqlite3.connect(path)
    jd.ensure_discovery_schema(conn)
    rows = [
        ("k1", "Acme", "Network Engineer", 0, "US", 90),
        ("k2", "Acme", "NetOps Lead", 1, "US", 65),
        ("k3", "Globex", "Network Engineer", 1, "CA", 90),
        ("k4", "Ac_me", "Network Engineer", 0, "GB", 90),
        ("k5", "AcXme", "Network Engineer", 0, "", 90),
        ("k6", "Umbrella", "Network Engineer", 0, "US", 40),
    ]
    for dedup, company, title, remote, code, score in rows:
        conn.execute(
            "INSERT INTO discovered_jobs (dedup_key, company_name, company_token, ats_type, job_title,"
            " location, is_remote, url, match_score, discovered_at, updated_at, country_code, status)"
            " VALUES (?, ?, 't', 'greenhouse', ?, 'Somewhere', ?, ?, ?, 't', 't', ?, 'discovered')",
            (dedup, company, title, remote, f"https://x.example/{dedup}", score, code or None),
        )
    conn.execute("UPDATE discovered_jobs SET status = 'dismissed' WHERE dedup_key = 'k6'")
    conn.execute("UPDATE discovered_jobs SET country_code = 'US' WHERE dedup_key = 'k6'")
    conn.commit()
    conn.close()


@pytest.fixture
def engine(monkeypatch, tmp_path):
    db = tmp_path / "d.db"
    seed(db)
    monkeypatch.setattr(jd, "DB_PATH", db)
    return jd.JobDiscoveryEngine(db_path=db)


def test_workplace_filters_remote_and_on_site(engine):
    assert {j["dedup_key"] for j in engine.get_discovered_jobs(min_score=0, remote_only=True)} == {"k2", "k3"}
    assert {j["dedup_key"] for j in engine.get_discovered_jobs(min_score=0, remote_only=False)} == {"k1", "k4", "k5"}
    assert len(engine.get_discovered_jobs(min_score=0)) == 5


def test_company_search_treats_wildcards_as_letters(engine):
    assert {j["company_name"] for j in engine.get_discovered_jobs(min_score=0, company_like="Ac_me")} == {"Ac_me"}
    assert {j["company_name"] for j in engine.get_discovered_jobs(min_score=0, company_like="acm")} == {"Acme"}
    assert engine.get_discovered_jobs(min_score=0, company_like="%") == []


def test_title_search_treats_wildcards_as_letters(engine):
    assert {j["job_title"] for j in engine.get_discovered_jobs(min_score=0, title_like="NetOps")} == {"NetOps Lead"}
    assert {j["job_title"] for j in engine.get_discovered_jobs(min_score=0, title_like="Network")} == {"Network Engineer"}
    assert engine.get_discovered_jobs(min_score=0, title_like="%") == []


def test_countries_lists_only_countries_with_live_suggestions(engine):
    assert jd.get_discovered_countries(engine.db_path) == ["CA", "GB", "US"]


def test_country_display_names_come_from_the_geo_data():
    import geo_reference
    assert geo_reference.display_name("US") == "United States"
    assert geo_reference.display_name("CA") == "Canada"
    assert geo_reference.display_name("XX") == "XX"
