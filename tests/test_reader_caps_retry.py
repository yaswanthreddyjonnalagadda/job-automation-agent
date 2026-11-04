"""Search-based readers look further and survive one hiccup (f317).

Workday stopped at 100 results and 40 details per title, iCIMS at 15 details,
and any single timeout or reset dropped the whole employer to zero jobs. The
caps are raised (200/100/50) and one retry covers a transient network failure;
a second failure still yields no jobs rather than an error, so one slow
employer never stops a scan.
"""
from types import SimpleNamespace

import discovery_sources as ds


def test_workday_searches_ten_pages_and_opens_a_hundred_details():
    assert ds.WORKDAY_PAGE * ds.WORKDAY_MAX_PAGES == 200
    import inspect
    assert inspect.signature(ds.fetch_workday_feed).parameters["max_details"].default == 100
    assert inspect.signature(ds.fetch_icims_feed).parameters["max_details"].default == 50


def test_one_transient_failure_is_retried_then_kept(monkeypatch):
    calls = []

    def flaky(url, **kw):
        calls.append(url)
        if len(calls) == 1:
            raise TimeoutError("read timed out")
        return SimpleNamespace(status_code=200, ok=True, text="",
                               json=lambda: {"jobs": [{"title": "Network Engineer", "location": "Remote",
                                                       "jobUrl": "https://x.example/1",
                                                       "publishedAt": "2026-11-02T09:00:00Z"}]})
    monkeypatch.setattr(ds.requests, "get", flaky)
    jobs = ds.fetch_ashby_feed("acme", "Acme")
    assert len(calls) == 2 and len(jobs) == 1


def test_two_failures_are_no_jobs_not_an_error(monkeypatch):
    def down(url, **kw):
        raise ConnectionError("reset by peer")
    monkeypatch.setattr(ds.requests, "get", down)
    assert ds.fetch_ashby_feed("acme", "Acme") == []
    assert ds.fetch_workable_feed("acme", "Acme") == []
