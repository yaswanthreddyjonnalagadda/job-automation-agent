"""Each Greenhouse card says what the form will ask (f319).

Greenhouse publishes every job's application form (?questions=true): labels,
required flags, and options. The scan reads the form for top Greenhouse jobs
and the card offers to check the rest on demand; each required question runs
through the same answer stack the application will use (profile fact, upload,
or approved answer), and the card says "All 8 required answered" or names the
ones that need the owner. Nothing is guessed: a question the stack cannot
answer is listed, not assumed.
"""
import json
import sqlite3
from types import SimpleNamespace

import readiness


def owner():
    return SimpleNamespace(first_name="Jane", last_name="Doe", email="jane@example.com",
                           phone_mobile="555-0100", city="Austin", state="Texas", country="United States")


GH_FORM = {"questions": [
    {"label": "First Name", "required": True, "fields": [{"name": "first_name", "type": "input_text"}]},
    {"label": "Last Name", "required": True, "fields": [{"name": "last_name", "type": "input_text"}]},
    {"label": "Resume/CV", "required": True, "fields": [{"name": "resume", "type": "file"}]},
    {"label": "Why do you want to work here?", "required": True,
     "fields": [{"name": "q1", "type": "textarea"}]},
    {"label": "LinkedIn Profile", "required": False, "fields": [{"name": "li", "type": "input_text"}]},
]}


def test_fetch_reads_the_published_form(monkeypatch):
    import readiness as rd
    seen = {}

    def fake_get(url, **kw):
        seen["url"] = url
        seen["params"] = kw.get("params")
        return SimpleNamespace(status_code=200, json=lambda: GH_FORM)
    monkeypatch.setattr(rd.requests, "get", fake_get)
    questions = rd.fetch_greenhouse_questions("acme", "123")
    assert "questions=true" in seen["url"] and "/jobs/123" in seen["url"]
    assert [(q["label"], q["required"]) for q in questions] == [
        ("First Name", True), ("Last Name", True), ("Resume/CV", True),
        ("Why do you want to work here?", True), ("LinkedIn Profile", False)]


def test_assess_runs_the_same_answer_stack(monkeypatch, tmp_path):
    import answer_bank
    bank = tmp_path / "bank.json"
    bank.write_text(json.dumps({"entries": {answer_bank.key("Why do you want to work here?"):
                                            {"answer": "Because."}}}), encoding="utf-8")
    monkeypatch.setattr(readiness, "BANK_FILE", bank)
    got = readiness.assess(GH_FORM["questions"], owner(), company="Acme")
    assert got["required"] == 4 and got["answered"] == 4 and got["needs_owner"] == []


def test_unanswerable_required_questions_are_named(monkeypatch, tmp_path):
    bank = tmp_path / "bank.json"
    bank.write_text(json.dumps({"entries": {}}), encoding="utf-8")
    monkeypatch.setattr(readiness, "BANK_FILE", bank)
    got = readiness.assess(GH_FORM["questions"], owner(), company="Acme")
    assert got["answered"] == 3
    assert got["needs_owner"] == ["Why do you want to work here?"]


def test_the_line_states_what_was_checked():
    assert readiness.summarize({"required": 4, "answered": 4, "needs_owner": []}) == "All 4 required answered"
    assert readiness.summarize({"required": 4, "answered": 3, "needs_owner": ["X"]}) == \
        "3 of 4 required answered · 1 needs you"
    assert readiness.summarize({"required": 0, "answered": 0, "needs_owner": []}) == "No required questions found"


def test_job_id_comes_from_the_greenhouse_address():
    assert readiness.greenhouse_job_id("https://boards.greenhouse.io/acme/jobs/123") == "123"
    assert readiness.greenhouse_job_id("https://job-boards.greenhouse.io/acme/jobs/123?gh_jid=123") == "123"
    assert readiness.greenhouse_job_id("https://example.com/nope") is None


def _bank(monkeypatch, tmp_path, entries):
    bank = tmp_path / "bank.json"
    bank.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    monkeypatch.setattr(readiness, "BANK_FILE", bank)


def test_the_scan_reads_the_form_for_top_greenhouse_jobs(tmp_path, monkeypatch):
    import config
    import job_discovery as jd
    board = tmp_path / "boards.json"
    board.write_text(json.dumps([{"company": "Acme", "token": "acme", "ats": "greenhouse"}]),
                     encoding="utf-8")
    engine = jd.JobDiscoveryEngine(db_path=tmp_path / "d.db", config_path=board)
    engine.profile = {"target_titles": ["Network Engineer"], "avoid_title_words": [],
                      "locations": ["Remote"], "requires_visa_sponsorship": False,
                      "country_code": "US", "open_to_relocation": False}
    engine.resume_text, engine.history = "", []
    job = {"company_name": "Acme", "company_token": "acme", "ats_type": "greenhouse",
           "job_title": "Network Engineer", "location": "Remote", "is_remote": 1,
           "url": "https://boards.greenhouse.io/acme/jobs/123", "description": "",
           "updated_at": "t", "posted_at": "", "posted_precision": ""}
    monkeypatch.setattr(jd, "fetch_jobs_for_company", lambda entry, **kw: [dict(job)])
    monkeypatch.setattr(jd.sources, "reference_employers", lambda *a, **k: [])
    monkeypatch.setattr(readiness, "fetch_greenhouse_questions", lambda token, jid: GH_FORM["questions"])
    monkeypatch.setattr(config, "get_user_profile", owner)
    _bank(monkeypatch, tmp_path, {})
    engine.scan_all(min_match_threshold=60)
    stored = json.loads(sqlite3.connect(tmp_path / "d.db").execute(
        "SELECT readiness_json FROM discovered_jobs").fetchone()[0])
    assert (stored["required"], stored["answered"]) == (4, 3)


def test_the_card_offers_to_check_and_shows_the_line(tmp_path, monkeypatch):
    import job_discovery as jd
    import web_ui
    db = tmp_path / "d.db"
    conn = sqlite3.connect(db)
    jd.ensure_discovery_schema(conn)
    conn.execute("INSERT INTO discovered_jobs (dedup_key, company_name, company_token, ats_type, job_title,"
                 " location, is_remote, url, match_score, discovered_at, updated_at)"
                 " VALUES ('k1', 'Acme', 'acme', 'greenhouse', 'Network Engineer', 'Remote', 1,"
                 " 'https://boards.greenhouse.io/acme/jobs/123', 90, 't', 't')")
    conn.commit()
    job_id = conn.execute("SELECT id FROM discovered_jobs").fetchone()[0]
    conn.close()
    monkeypatch.setattr(jd, "DB_PATH", db)
    monkeypatch.setattr(readiness, "fetch_greenhouse_questions", lambda token, jid: GH_FORM["questions"])
    import config
    monkeypatch.setattr(config, "get_user_profile", owner)
    _bank(monkeypatch, tmp_path, {})
    web_ui.app.config["TESTING"] = True
    resp = web_ui.app.test_client().post(f"/api/discovered/{job_id}/readiness",
                                         headers={"X-Requested-With": "fetch"})
    assert resp.status_code == 200
    assert resp.get_json()["line"] == "3 of 4 required answered · 1 needs you"
    assert resp.get_json()["needs_owner"] == ["Why do you want to work here?"]
    assert "data-readiness" in web_ui.INDEX_HTML and "dj.readiness" in web_ui.INDEX_HTML


def test_preparation_passes_the_line_through():
    import discovered_view as dv
    line = json.dumps({"required": 2, "answered": 2, "needs_owner": []})
    job = {"dedup_key": "k", "company_name": "Acme", "ats_type": "greenhouse",
           "job_title": "Network Engineer", "location": "Remote", "is_remote": 1,
           "url": "https://x.example/1", "match_score": 90, "matched_skills": "[]",
           "status": "discovered", "discovered_at": "t", "readiness_json": line}
    assert dv.prepare([job], [])[0]["readiness"] == "All 2 required answered"
