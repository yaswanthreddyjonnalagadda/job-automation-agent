"""The Suggested jobs page shows fresh jobs by when they were posted, from the last hour to the last week (f299).

The owner asked to run the search and see the jobs posted from the last hour to the last week, with nothing already
applied to or rejected on the page. The window is a plain choice on the page; the label on each card says how exactly
the system dated the job, and never claims more.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import discovered_view as dv

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def at(hours=0.0, days=0.0):
    return (NOW - timedelta(hours=hours, days=days)).isoformat()


@pytest.mark.parametrize("posted_at,precision,label", [
    (at(hours=0.2), "minute", "posted 12 minutes ago"),
    (at(hours=0.01), "minute", "posted just now"),
    (at(hours=1), "minute", "posted 1 hour ago"),
    (at(hours=5), "minute", "posted 5 hours ago"),
    (at(hours=30), "minute", "posted yesterday"),
    (at(days=3), "minute", "posted 3 days ago"),
    (at(days=1), "day", "posted within the last day"),         # a day-level date never claims an hour
    (at(days=2), "day", "posted within the last 2 days"),
    (at(days=4), "day", "posted within the last 4 days"),
    ("", "", ""),
    (None, None, ""),
    ("not a date", "minute", ""),
])
def test_the_label_says_how_exactly_the_job_was_dated(posted_at, precision, label):
    assert dv.posted_label(posted_at, precision, NOW) == label


def job(n, **kw):
    base = dict(id=n, company_name=f"Co{n}", job_title="Network Engineer", url=f"https://x.example/{n}", location="Remote",
                is_remote=True, match_score=70, discovered_at=at(days=9), ats_type="greenhouse", matched_skills=[])
    base.update(kw)
    return base


def test_with_dates_the_newest_job_is_first_whatever_its_score():
    jobs = [job(1, match_score=95, posted_at=at(hours=30), posted_precision="minute"),
            job(2, match_score=60, posted_at=at(hours=1), posted_precision="minute"),
            job(3, match_score=80, posted_at=at(hours=10), posted_precision="minute")]
    assert [j["id"] for j in dv.prepare(jobs, [], NOW, newest_first=True)] == [2, 3, 1]


def test_without_asking_for_newest_first_the_best_score_still_leads():
    jobs = [job(1, match_score=95, posted_at=at(hours=30), posted_precision="minute"),
            job(2, match_score=60, posted_at=at(hours=1), posted_precision="minute")]
    assert [j["id"] for j in dv.prepare(jobs, [], NOW)] == [1, 2]


def test_a_card_uses_the_posting_label_and_falls_back_to_when_it_was_found():
    dated = dv.prepare([job(1, posted_at=at(hours=5), posted_precision="minute")], [], NOW)[0]
    undated = dv.prepare([job(2)], [], NOW)[0]
    assert dated["age"] == "posted 5 hours ago"
    assert undated["age"].startswith("found")


# -- the page ----------------------------------------------------------------------------------------------------

@pytest.fixture
def home(monkeypatch):
    import job_discovery
    import profile_setup
    import web_ui

    asked = {}

    def render(query="", jobs=(), applications=()):
        monkeypatch.setattr(web_ui, "get_tracker", lambda: SimpleNamespace(
            list_all=lambda: list(applications), documents_for=lambda _i: [], answers_for=lambda _i: [],
            events=lambda *_a, **_k: []))
        monkeypatch.setattr(web_ui, "_current_runs", lambda: {})
        monkeypatch.setattr(web_ui, "waiting_runs", lambda _d: [])
        monkeypatch.setattr(web_ui, "last_scan", lambda: {})
        monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
        monkeypatch.setattr(job_discovery.JobDiscoveryEngine, "__init__", lambda self, *a, **k: None)

        def fetch(self, **kw):
            asked.update(kw)
            return list(jobs)
        monkeypatch.setattr(job_discovery.JobDiscoveryEngine, "get_discovered_jobs", fetch)
        web_ui.app.config["TESTING"] = True
        return web_ui.app.test_client().get("/" + query).get_data(as_text=True)
    render.asked = asked
    return render


@pytest.mark.parametrize("query,hours", [("", 168), ("?within=1h", 1), ("?within=24h", 24), ("?within=3d", 72),
                                         ("?within=7d", 168), ("?within=nonsense", 168), ("?within=", 168)])
def test_the_window_chosen_on_the_page_is_the_window_asked_for(home, query, hours):
    home(query)
    assert home.asked["within_hours"] == hours


def test_the_page_offers_the_four_windows_and_marks_the_chosen_one(home):
    html = home("?within=24h").split('id="jobs-h"')[1]
    for label in ("Last hour", "Last 24 hours", "Last 3 days", "Last week"):
        assert label in html
    chosen = html.split('aria-current="true"')[1].split("<")[0]
    assert "Last 24 hours" in chosen
    assert html.count('aria-current="true"') == 1


def test_the_page_says_what_a_window_cannot_show(home):
    html = home("?within=1h").split('id="jobs-h"')[1]
    assert "no posting date" in html.lower() or "not dated" in html.lower()


def test_an_empty_window_says_so_and_does_not_suggest_pressing_find_new_jobs_when_jobs_exist_but_are_older(home):
    html = home("?within=1h", jobs=[]).split('id="jobs-h"')[1]
    assert "No jobs posted in the last hour" in html


def test_a_job_with_a_posting_date_shows_it(home):
    five_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=5, minutes=10)).isoformat()
    html = home("", jobs=[job(1, posted_at=five_hours_ago, posted_precision="minute")]).split('id="jobs-h"')[1]
    assert "posted 5 hours ago" in html


def test_the_scan_reads_the_employers_from_the_applications_already_made(monkeypatch):
    import job_discovery
    import web_ui

    app_record = SimpleNamespace(company="Rackspace", url="https://rackspace.wd1.myworkdayjobs.com/External/job/X/Y_R1")
    monkeypatch.setattr(web_ui, "get_tracker", lambda: SimpleNamespace(list_all=lambda: [app_record]))
    seen = {}
    monkeypatch.setattr(job_discovery.JobDiscoveryEngine, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(job_discovery.JobDiscoveryEngine, "scan_all",
                        lambda self, **k: seen.update(history=list(self.history)) or {"companies_scanned": 1})
    monkeypatch.setattr(web_ui, "_remember_scan", lambda stats: None)
    web_ui.app.config["TESTING"] = True
    client = web_ui.app.test_client()
    client.post("/api/discovered/scan", data={"csrf_token": web_ui.web_guard.TOKEN})
    assert seen["history"] == [("Rackspace", app_record.url)]


# -- a scan takes minutes: the button must not hang the page -----------------------------------------------------

@pytest.fixture
def scanning(monkeypatch):
    import threading
    import job_discovery
    import profile_setup
    import web_ui

    gate, calls, saved = threading.Event(), [], []
    monkeypatch.setattr(web_ui, "get_tracker", lambda: SimpleNamespace(
        list_all=lambda: [], documents_for=lambda _i: [], answers_for=lambda _i: [], events=lambda *_a, **_k: []))
    monkeypatch.setattr(web_ui, "_current_runs", lambda: {})
    monkeypatch.setattr(web_ui, "waiting_runs", lambda _d: [])
    monkeypatch.setattr(web_ui, "last_scan", lambda: {})
    monkeypatch.setattr(web_ui, "_remember_scan", lambda stats: saved.append(stats))
    monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
    monkeypatch.setattr(job_discovery.JobDiscoveryEngine, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(job_discovery.JobDiscoveryEngine, "get_discovered_jobs", lambda self, **k: [])

    def scan_all(self, **kw):
        calls.append(1)
        gate.wait(10)
        return {"companies_scanned": 111}
    monkeypatch.setattr(job_discovery.JobDiscoveryEngine, "scan_all", scan_all)
    web_ui.app.config["TESTING"] = True
    yield SimpleNamespace(client=web_ui.app.test_client(), gate=gate, calls=calls, saved=saved, web_ui=web_ui)
    gate.set()
    web_ui.wait_for_scan(10)


def press(env):
    return env.client.post("/api/discovered/scan", data={"csrf_token": env.web_ui.web_guard.TOKEN})


def test_pressing_find_new_jobs_returns_at_once_and_the_page_says_it_is_checking(scanning):
    import time
    started = time.time()
    response = press(scanning)
    assert response.status_code == 302 and time.time() - started < 3          # not seven minutes
    assert scanning.web_ui.scan_running() is True
    html = scanning.client.get("/").get_data(as_text=True).split('id="jobs-h"')[1]
    assert "Checking job boards since" in html and "This takes a few minutes" in html


def test_a_second_press_while_one_scan_runs_starts_no_second_scan(scanning):
    press(scanning)
    press(scanning)
    press(scanning)
    assert len(scanning.calls) == 1


def test_when_the_scan_ends_it_is_remembered_and_the_page_stops_saying_it_is_checking(scanning):
    press(scanning)
    scanning.gate.set()
    scanning.web_ui.wait_for_scan(10)
    assert scanning.saved == [{"companies_scanned": 111}]
    assert scanning.web_ui.scan_running() is False
    assert "This takes a few minutes" not in scanning.client.get("/").get_data(as_text=True).split('id="jobs-h"')[1]


def test_a_scan_that_fails_ends_cleanly_and_a_new_one_can_start(scanning, monkeypatch):
    import job_discovery

    def broken(self, **kw):
        raise RuntimeError("the network dropped")
    monkeypatch.setattr(job_discovery.JobDiscoveryEngine, "scan_all", broken)
    press(scanning)
    scanning.web_ui.wait_for_scan(10)
    assert scanning.web_ui.scan_running() is False and scanning.saved == []
