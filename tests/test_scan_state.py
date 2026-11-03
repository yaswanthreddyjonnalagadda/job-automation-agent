"""Every scan leaves a per-board record, and a scheduler can run one (f317).

A board that failed to load used to vanish into a debug line: "0 jobs" and
"board down" looked identical. Each scan now records, per board, when it was
read, how many jobs it gave, and the failure text when it gave none; the
summary counts failed boards. scripts/scheduled_scan.py runs one scan for
Task Scheduler or cron and reports the same summary.
"""
import json
import sqlite3

import job_discovery as jd


def test_each_board_leaves_a_state_row_with_its_error(tmp_path, monkeypatch):
    board = tmp_path / "boards.json"
    board.write_text(json.dumps([
        {"company": "Acme", "token": "acme", "ats": "greenhouse"},
        {"company": "Boom", "token": "boom", "ats": "greenhouse"},
    ]), encoding="utf-8")
    engine = jd.JobDiscoveryEngine(db_path=tmp_path / "d.db", config_path=board)
    engine.profile = {"target_titles": ["Network Engineer"], "avoid_title_words": [],
                      "locations": ["Remote"], "requires_visa_sponsorship": False,
                      "country_code": "US", "open_to_relocation": False}
    engine.resume_text, engine.history = "", []
    job = {"company_name": "Acme", "company_token": "acme", "ats_type": "greenhouse",
           "job_title": "Network Engineer", "location": "Remote", "is_remote": 1,
           "url": "https://x.example/1", "description": "", "updated_at": "t",
           "posted_at": "", "posted_precision": ""}

    def fetch(entry, **kw):
        if entry.get("token") == "boom":
            raise ConnectionError("reset by peer")
        return [dict(job)]
    monkeypatch.setattr(jd, "fetch_jobs_for_company", fetch)
    monkeypatch.setattr(jd.sources, "reference_employers", lambda *a, **k: [])
    stats = engine.scan_all()
    assert stats["boards_failed"] == 1
    conn = sqlite3.connect(tmp_path / "d.db")
    try:
        rows = {r[0]: (r[1], r[2], r[3]) for r in
                conn.execute("SELECT token, last_error, jobs_seen, last_scan FROM board_state")}
    finally:
        conn.close()
    assert rows["acme"][1] == 1 and rows["acme"][0] == "" and rows["acme"][2]
    assert "reset by peer" in rows["boom"][0] and rows["boom"][1] == 0


def test_the_last_scan_record_counts_failed_boards(tmp_path, monkeypatch):
    import web_ui
    monkeypatch.setattr(web_ui, "_LAST_SCAN", tmp_path / "last.json")
    web_ui._remember_scan({"companies_scanned": 5, "relevant_jobs_saved": 3, "boards_failed": 2})
    record = json.loads((tmp_path / "last.json").read_text(encoding="utf-8"))
    assert (record["boards"], record["saved"], record["failed"]) == (5, 3, 2)


def test_a_scheduler_runs_one_scan_and_reports_it(tmp_path, monkeypatch, capsys):
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "scripts"))
    import scheduled_scan
    board = tmp_path / "boards.json"
    board.write_text(json.dumps([{"company": "Acme", "token": "acme", "ats": "greenhouse"}]),
                     encoding="utf-8")
    monkeypatch.setattr(jd, "fetch_jobs_for_company", lambda entry, **kw: [])
    monkeypatch.setattr(jd.sources, "reference_employers", lambda *a, **k: [])
    code = scheduled_scan.main(["--db", str(tmp_path / "d.db"), "--companies", str(board)])
    out = capsys.readouterr().out
    assert code == 0 and "boards=1" in out and "failed=0" in out
