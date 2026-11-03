"""Two more public boards join the scan: SmartRecruiters and Workable (f317).

Both publish the same JSON their own career pages read: no login, no key.
SmartRecruiters paginates (totalFound + offset) with per-posting detail calls
for the text; Workable answers in one call (?details=true), flagging remote
with `telecommuting`. Shapes follow the vendors' public endpoints as
documented in October 2026; a live check against a real board is still owed.
Lever's EU-hosted boards (404 on api.lever.co) fall back to api.eu.lever.co.
"""
from types import SimpleNamespace

import discovery_sources as ds
import job_discovery as jd


def reply(payload, status=200):
    return SimpleNamespace(status_code=status, ok=status < 400, json=lambda: payload, text="")


SR_PAGE = {
    "totalFound": 2, "limit": 100, "offset": 0,
    "content": [
        {"id": "a1", "name": "Network Engineer",
         "location": {"city": "Austin", "region": "Texas", "country": "United States",
                       "fullLocation": "Austin, TX", "remote": False},
         "releasedDate": "2026-11-01T09:00:00Z"},
        {"id": "b2", "name": "Helpdesk Analyst",
         "location": {"city": "London", "region": "", "country": "United Kingdom",
                       "fullLocation": "London, UK", "remote": False},
         "releasedDate": "2026-10-20"},
    ],
}
SR_DETAIL = {"jobAd": {"sections": {
    "company": {"title": "Co", "text": "We hire."},
    "job": {"title": "Role", "text": "BGP and OSPF required."}}}}


def test_smartrecruiters_searches_by_title_and_opens_each_match(monkeypatch):
    seen = {}

    def fake_get(url, **kw):
        seen.setdefault("params", []).append(kw.get("params"))
        if url.endswith("/postings"):
            return reply(SR_PAGE)
        return reply(SR_DETAIL)
    monkeypatch.setattr(ds.requests, "get", fake_get)
    jobs = ds.fetch_smartrecruiters_feed("acme", "Acme", titles=["Network Engineer"])
    assert [j["job_title"] for j in jobs] == ["Network Engineer", "Helpdesk Analyst"]
    assert jobs[0]["location"] == "Austin, TX"
    assert jobs[0]["url"] == "https://jobs.smartrecruiters.com/acme/a1"
    assert jobs[0]["posted_precision"] == "minute" and jobs[1]["posted_precision"] == "day"
    assert "BGP" in jobs[0]["description"]
    assert any((p or {}).get("q") == "Network Engineer" for p in seen["params"])


def test_smartrecruiters_walks_every_page_and_skips_no_description(monkeypatch):
    calls = []

    def fake_get(url, **kw):
        params = kw.get("params") or {}
        if url.endswith("/postings"):
            calls.append(params.get("offset", 0))
        page = dict(SR_PAGE, totalFound=150, offset=params.get("offset", 0),
                    content=[] if params.get("offset", 0) else SR_PAGE["content"])
        return reply(page) if url.endswith("/postings") else reply({})
    monkeypatch.setattr(ds.requests, "get", fake_get)
    jobs = ds.fetch_smartrecruiters_feed("acme", "Acme", max_details=1)
    assert calls == [0, 100]
    assert jobs[0]["description"] == "" and jobs[1]["description"] == ""


def test_smartrecruiters_empty_or_broken_is_no_jobs(monkeypatch):
    monkeypatch.setattr(ds.requests, "get", lambda url, **kw: reply({"totalFound": 0, "content": []}))
    assert ds.fetch_smartrecruiters_feed("nope", "Nope") == []
    monkeypatch.setattr(ds.requests, "get", lambda url, **kw: reply({}, status=404))
    assert ds.fetch_smartrecruiters_feed("nope", "Nope") == []


def test_smartrecruiters_passes_a_country_filter(monkeypatch):
    seen = {}

    def fake_get(url, **kw):
        seen.update(kw.get("params") or {})
        return reply({"totalFound": 0, "content": []})
    monkeypatch.setattr(ds.requests, "get", fake_get)
    ds.fetch_smartrecruiters_feed("acme", "Acme", country="US")
    assert seen.get("country") == "US"


WORKABLE = {"jobs": [
    {"title": "Network Engineer", "city": "Austin", "country": "United States",
     "url": "https://apply.workable.com/acme/j/11/", "published_on": "2026-11-02",
     "telecommuting": False, "description": "<p>BGP.</p>"},
    {"title": "Support Lead", "city": "", "country": "",
     "url": "https://apply.workable.com/acme/j/12/", "published_on": "not a date",
     "telecommuting": True},
]}


def test_workable_reads_one_call_with_remote_flag(monkeypatch):
    monkeypatch.setattr(ds.requests, "get", lambda url, **kw: reply(WORKABLE))
    jobs = ds.fetch_workable_feed("acme", "Acme")
    assert [j["job_title"] for j in jobs] == ["Network Engineer", "Support Lead"]
    assert jobs[0]["location"] == "Austin, United States"
    assert jobs[0]["description"] == "BGP."
    assert jobs[0]["posted_precision"] == "day"
    assert jobs[1]["is_remote"] == 1 and jobs[1]["posted_at"] == ""


def test_workable_broken_is_no_jobs(monkeypatch):
    monkeypatch.setattr(ds.requests, "get", lambda url, **kw: (_ for _ in ()).throw(TimeoutError()))
    assert ds.fetch_workable_feed("acme", "Acme") == []


def test_lever_eu_boards_fall_back_to_the_eu_host(monkeypatch):
    hosts = []

    def fake_get(url, **kw):
        hosts.append(url)
        if "api.eu.lever.co" in url:
            return reply([{"text": "Network Engineer", "categories": {"location": "Berlin"},
                           "hostedUrl": "https://x.example/eu1", "createdAt": 1762060000000}])
        return reply({}, status=404)
    monkeypatch.setattr(jd.requests, "get", fake_get)
    jobs = jd.fetch_lever_feed("acme-eu", "Acme EU")
    assert len(jobs) == 1 and jobs[0]["job_title"] == "Network Engineer"
    assert any("api.eu.lever.co" in h for h in hosts[1:])


def test_history_addresses_yield_the_new_boards():
    entries = ds.companies_from_history([
        ("Acme", "https://jobs.smartrecruiters.com/Acme/a1-network-engineer"),
        ("Beta", "https://apply.workable.com/beta/j/11/"),
    ])
    by_ats = {e["ats"]: e for e in entries}
    assert by_ats["smartrecruiters"]["token"] == "acme"
    assert by_ats["workable"]["token"] == "beta"


def test_fetch_dispatch_reaches_the_new_readers(monkeypatch):
    monkeypatch.setattr(ds, "fetch_smartrecruiters_feed", lambda *a, **k: [{"job_title": "SR"}])
    monkeypatch.setattr(ds, "fetch_workable_feed", lambda *a, **k: [{"job_title": "W"}])
    assert jd.fetch_jobs_for_company({"ats": "smartrecruiters", "token": "s", "company": "S"})[0]["job_title"] == "SR"
    assert jd.fetch_jobs_for_company({"ats": "workable", "token": "w", "company": "W"})[0]["job_title"] == "W"
