"""Queued applications start from the dashboard one at a time (f327).

Bulk queueing (f324) left each queued row waiting on its own Start button.
"Start next queued (N)" starts the oldest queued application -- the same run
a Start press begins, so every submission gate still holds -- and the page
reloads to show it at the top.
"""
from types import SimpleNamespace

import pytest


class FakeThread:
    started = []

    def __init__(self, target=None, args=(), **kw):
        self._target = target
        self._args = args

    def start(self):
        FakeThread.started.append((self._target, self._args))


@pytest.fixture(autouse=True)
def no_threads(monkeypatch):
    import web_ui
    FakeThread.started = []
    monkeypatch.setattr(web_ui.threading, "Thread", FakeThread)
    monkeypatch.setattr(web_ui, "_RUNS", {})
    web_ui.app.config["TESTING"] = True


def queued(id_, company="Acme", title="Network Engineer", url="https://x.example/1", status="ready_to_apply"):
    return SimpleNamespace(id=id_, company=company, title=title, url=url, status=status,
                           location="", notes="", last_page_url="", updated_at=None,
                           created_at=None, dedup_key=f"k{id_}")


def client_with(monkeypatch, apps):
    import web_ui
    monkeypatch.setattr(web_ui, "get_tracker", lambda: SimpleNamespace(list_all=lambda status=None: apps))
    return web_ui.app.test_client()


def fetch(browser, *a, **k):
    k.setdefault("headers", {})["X-Requested-With"] = "fetch"
    return browser.post(*a, **k)


def test_starts_the_oldest_queued_application(monkeypatch):
    import web_ui
    apps = [queued(2, url="https://x.example/new"), queued(1, url="https://x.example/old")]
    resp = fetch(client_with(monkeypatch, apps), "/api/auto-apply")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True and body["started"] is True and body["id"] == 1
    assert len(FakeThread.started) == 1 and FakeThread.started[0][0] is web_ui._run_apply
    assert FakeThread.started[0][1] == ("https://x.example/old",)


def test_skips_queued_rows_without_a_posting(monkeypatch):
    apps = [queued(2, url=""), queued(1, url="https://x.example/old")]
    resp = fetch(client_with(monkeypatch, apps), "/api/auto-apply")
    assert resp.get_json()["ok"] is True and resp.get_json()["id"] == 1


def test_busy_and_empty_are_honest_errors(monkeypatch):
    import web_ui
    monkeypatch.setattr(web_ui, "_RUNS", {"https://x.example/9": {"state": "running", "proc": object()}})
    resp = fetch(client_with(monkeypatch, [queued(1)]), "/api/auto-apply")
    assert resp.get_json()["ok"] is False and "still running" in resp.get_json()["error"]
    monkeypatch.setattr(web_ui, "_RUNS", {})
    resp = fetch(client_with(monkeypatch, []), "/api/auto-apply")
    assert resp.get_json()["ok"] is False and "Nothing is queued" in resp.get_json()["error"]
    assert FakeThread.started == []


def test_without_javascript_it_redirects_and_starts(monkeypatch):
    resp = client_with(monkeypatch, [queued(1)]).post("/api/auto-apply")
    assert resp.status_code == 302 and resp.location.endswith("/")
    assert len(FakeThread.started) == 1


@pytest.fixture
def page(monkeypatch):
    import job_discovery
    import profile_setup
    import web_ui

    def render(apps):
        monkeypatch.setattr(web_ui, "get_tracker", lambda: SimpleNamespace(
            list_all=lambda status=None: list(apps), documents_for=lambda _i: [],
            answers_for=lambda _i: [], events=lambda *_a, **_k: []))
        monkeypatch.setattr(web_ui, "_current_runs", lambda: {})
        monkeypatch.setattr(web_ui, "waiting_runs", lambda _d: [])
        monkeypatch.setattr(web_ui, "last_scan", lambda: {})
        monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
        monkeypatch.setattr(job_discovery.JobDiscoveryEngine, "__init__", lambda self, *a, **k: None)
        monkeypatch.setattr(job_discovery.JobDiscoveryEngine, "get_discovered_jobs", lambda self, **k: [])
        monkeypatch.setattr(job_discovery, "get_discovered_countries", lambda: [])
        web_ui.app.config["TESTING"] = True
        return web_ui.app.test_client().get("/").get_data(as_text=True)
    return render


def test_the_button_shows_only_with_a_count_when_something_is_queued(page):
    html = page([queued(1), queued(2), queued(3, status="submitted")])
    assert 'action="/api/auto-apply"' in html and "Start next queued (2)" in html
    assert 'action="/api/auto-apply"' not in page([queued(3, status="submitted")])


def test_cockpit_starts_queued_in_place():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "static" / "cockpit.js").read_text(encoding="utf-8")
    assert "'autoApply' in form.dataset" in js
