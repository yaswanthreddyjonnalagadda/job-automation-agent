"""The suggested-jobs feed works in place: Apply, Pass, and bulk Queue (f324).

Pressing "Start application" reloaded the whole dashboard to show one card
gone, and each card could only be handled alone. Now Apply answers JSON and
the card shows its new state in place; checkboxes and a bulk bar Queue or
Pass several cards at once (queued cards leave the feed and wait under
Applications, where Start picks them up); the posting link is the card's
first action. Plain forms still redirect for no-JavaScript.
"""
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import job_discovery as jd


def seed(path, count=3, submitted_urls=()):
    conn = sqlite3.connect(path)
    jd.ensure_discovery_schema(conn)
    for n in range(1, count + 1):
        conn.execute(
            "INSERT INTO discovered_jobs (dedup_key, company_name, company_token, ats_type, job_title,"
            " location, is_remote, url, match_score, discovered_at, updated_at)"
            " VALUES (?, 'Acme', 'acme', 'greenhouse', ?, 'Austin, TX', 0,"
            " ?, 90, 't', 't')",
            (f"k{n}", f"Network Engineer {n}", f"https://x.example/{n}"),
        )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS applications (id INTEGER PRIMARY KEY, dedup_key TEXT, title TEXT,"
        " company TEXT, location TEXT, source_site TEXT, url TEXT, status TEXT, notes TEXT,"
        " created_at TEXT, updated_at TEXT)"
    )
    for url in submitted_urls:
        conn.execute(
            "INSERT INTO applications (dedup_key, title, company, url, status)"
            " VALUES ('old', 'Network Engineer 1', 'Acme', ?, 'submitted')",
            (url,),
        )
    conn.commit()
    conn.close()


def client(monkeypatch, tmp_path, **seed_kw):
    import web_ui
    db = tmp_path / "d.db"
    seed(db, **seed_kw)
    monkeypatch.setattr(jd, "DB_PATH", db)
    web_ui.app.config["TESTING"] = True
    return web_ui.app.test_client(), db


def status_of(db, job_id):
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT status FROM discovered_jobs WHERE id = ?", (job_id,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


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


def fetch(browser, *a, **k):
    k.setdefault("headers", {})["X-Requested-With"] = "fetch"
    return browser.post(*a, **k)


# -- one card's Apply -------------------------------------------------------------------------------

def test_apply_answers_json_and_starts_without_reload(monkeypatch, tmp_path):
    import web_ui
    monkeypatch.setattr(jd, "load_profile", lambda: (_ for _ in ()).throw(AssertionError("no profile")))
    monkeypatch.setattr(jd, "load_resume_text", lambda: (_ for _ in ()).throw(AssertionError("no resume")))
    browser, db = client(monkeypatch, tmp_path)
    resp = fetch(browser, "/api/discovered/1/apply")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True and body["started"] is True and body["job_id"] == 1
    assert status_of(db, 1) == "queued"
    assert len(FakeThread.started) == 1 and FakeThread.started[0][0] is web_ui._run_apply


def test_apply_to_a_gone_suggestion_is_an_honest_error(monkeypatch, tmp_path):
    browser, _db = client(monkeypatch, tmp_path)
    resp = fetch(browser, "/api/discovered/99/apply")
    assert resp.status_code == 404
    assert resp.get_json()["ok"] is False
    assert FakeThread.started == []


def test_apply_while_busy_starts_nothing(monkeypatch, tmp_path):
    import web_ui
    monkeypatch.setattr(web_ui, "_RUNS", {"https://x.example/9": {"state": "running", "proc": object()}})
    browser, db = client(monkeypatch, tmp_path)
    resp = fetch(browser, "/api/discovered/1/apply")
    assert resp.get_json()["ok"] is False and "still running" in resp.get_json()["error"]
    assert FakeThread.started == []
    assert status_of(db, 1) == "discovered"


def test_apply_to_an_already_submitted_posting_says_so(monkeypatch, tmp_path):
    browser, db = client(monkeypatch, tmp_path, submitted_urls=("https://x.example/1",))
    resp = fetch(browser, "/api/discovered/1/apply")
    assert resp.get_json()["ok"] is False and "already submitted" in resp.get_json()["error"]
    assert FakeThread.started == []
    assert status_of(db, 1) == "submitted"


def test_apply_without_javascript_still_redirects_and_starts(monkeypatch, tmp_path):
    browser, db = client(monkeypatch, tmp_path)
    resp = browser.post("/api/discovered/1/apply")
    assert resp.status_code == 302 and resp.location.endswith("/")
    assert status_of(db, 1) == "queued" and len(FakeThread.started) == 1


def test_the_engine_queue_still_marks_and_registers(monkeypatch, tmp_path):
    _browser, db = client(monkeypatch, tmp_path)
    engine = jd.JobDiscoveryEngine(db_path=db)
    queued = engine.queue_for_application(1)
    assert queued is not None and queued["url"] == "https://x.example/1"
    assert queued["matched_skills"] == [] and status_of(db, 1) == "queued"
    conn = sqlite3.connect(db)
    try:
        app = conn.execute("SELECT status FROM applications WHERE dedup_key = 'k1'").fetchone()
        assert app[0] == "ready_to_apply"
    finally:
        conn.close()


# -- bulk actions -----------------------------------------------------------------------------------

def test_bulk_pass_removes_several_at_once(monkeypatch, tmp_path):
    browser, db = client(monkeypatch, tmp_path)
    resp = fetch(browser, "/api/discovered/bulk", data={"action": "dismiss", "ids": ["1", "2", "99"]})
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "action": "dismiss", "done": [1, 2], "failed": [99]}
    assert status_of(db, 1) == "dismissed" and status_of(db, 2) == "dismissed"
    assert status_of(db, 3) == "discovered" and FakeThread.started == []


def test_bulk_queue_registers_without_starting(monkeypatch, tmp_path):
    browser, db = client(monkeypatch, tmp_path)
    resp = fetch(browser, "/api/discovered/bulk", data={"action": "queue", "ids": ["1", "2"]})
    assert resp.get_json() == {"ok": True, "action": "queue", "done": [1, 2], "failed": []}
    assert status_of(db, 1) == "queued" and status_of(db, 2) == "queued"
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT status FROM applications ORDER BY dedup_key").fetchall()
        assert [r[0] for r in rows] == ["ready_to_apply", "ready_to_apply"]
    finally:
        conn.close()
    assert FakeThread.started == []


def test_bulk_queue_skips_already_submitted(monkeypatch, tmp_path):
    browser, db = client(monkeypatch, tmp_path, submitted_urls=("https://x.example/1",))
    resp = fetch(browser, "/api/discovered/bulk", data={"action": "queue", "ids": ["1", "2"]})
    assert resp.get_json() == {"ok": True, "action": "queue", "done": [2], "failed": [1]}
    assert status_of(db, 1) == "submitted" and status_of(db, 2) == "queued"


def test_bulk_rejects_garbage(monkeypatch, tmp_path):
    browser, _db = client(monkeypatch, tmp_path)
    resp = fetch(browser, "/api/discovered/bulk", data={"action": "explode", "ids": ["1"]})
    assert resp.status_code == 400 and resp.get_json()["ok"] is False
    resp = fetch(browser, "/api/discovered/bulk", data={"action": "dismiss", "ids": ["x"]})
    assert resp.get_json() == {"ok": True, "action": "dismiss", "done": [], "failed": ["x"]}
    resp = fetch(browser, "/api/discovered/bulk", data={"action": "dismiss"})
    assert resp.status_code == 400 and resp.get_json()["ok"] is False


def test_bulk_without_javascript_redirects_and_applies(monkeypatch, tmp_path):
    browser, db = client(monkeypatch, tmp_path)
    resp = browser.post("/api/discovered/bulk", data={"action": "dismiss", "ids": ["1", "3"]})
    assert resp.status_code == 302 and resp.location.endswith("/")
    assert status_of(db, 1) == "dismissed" and status_of(db, 3) == "dismissed"


# -- the rendered feed -------------------------------------------------------------------------------

@pytest.fixture
def feed(monkeypatch):
    import job_discovery
    import profile_setup
    import web_ui

    def render(jobs):
        monkeypatch.setattr(web_ui, "get_tracker", lambda: SimpleNamespace(
            list_all=lambda: [], documents_for=lambda _i: [], answers_for=lambda _i: [],
            events=lambda *_a, **_k: []))
        monkeypatch.setattr(web_ui, "_current_runs", lambda: {})
        monkeypatch.setattr(web_ui, "waiting_runs", lambda _d: [])
        monkeypatch.setattr(web_ui, "last_scan", lambda: {})
        monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
        monkeypatch.setattr(job_discovery.JobDiscoveryEngine, "__init__", lambda self, *a, **k: None)
        monkeypatch.setattr(job_discovery.JobDiscoveryEngine, "get_discovered_jobs", lambda self, **k: jobs)
        web_ui.app.config["TESTING"] = True
        return web_ui.app.test_client().get("/").get_data(as_text=True)
    return render


def job(**kw):
    base = dict(id=1, company_name="Acme", job_title="Network Engineer",
                url="https://boards.example.com/acme/1", location="Austin, TX", is_remote=False,
                match_score=65, discovered_at=datetime.now(timezone.utc).isoformat(), ats_type="greenhouse",
                matched_skills=[])
    base.update(kw)
    return base


def test_the_posting_link_is_the_cards_first_action(feed):
    html = feed([job()])
    foot = html.split('class="job-foot"')[1].split("</article>")[0]
    link_at = foot.index("<a ")
    assert "boards.example.com/acme/1" in foot[link_at:foot.index(">", link_at + 200) + 200]
    assert link_at < foot.index("<form")


def test_cards_carry_checkboxes_and_a_bulk_bar(feed):
    html = feed([job(), job(id=2, job_title="NetOps Lead")])
    assert html.count('form="bulk-form" name="ids"') == 2
    assert 'id="bulk-form"' in html and 'action="/api/discovered/bulk"' in html
    assert 'name="action" value="queue"' in html and 'name="action" value="dismiss"' in html


def test_apply_form_marks_itself_for_an_in_place_start():
    import re

    import web_ui
    forms = re.findall(r"<form[^>]*>", web_ui.INDEX_HTML)
    apply = [f for f in forms if "/apply" in f and "discovered" in f]
    assert apply and all("data-apply-card" in f for f in apply)


def test_cockpit_handles_apply_and_bulk_in_place():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "static" / "cockpit.js").read_text(encoding="utf-8")
    assert "'applyCard' in form.dataset" in js and "'bulk' in form.dataset" in js
    assert "data-bulk-check" in js and "updateBulk" in js
