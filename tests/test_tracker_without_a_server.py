"""The agent runs with no database server: SQLite keeps everything Postgres keeps.

Anyone can run the agent on their own computer (29 September 2026), and needing a Postgres server (Docker) first
was a wall. job_tracker.JobTracker now has the Postgres tracker's whole surface, tracking.open_tracker() is the one
place that picks between them, and the dashboard reads through the tracker rather than in Postgres's own SQL.
"""
import inspect
import sqlite3

import pytest

import job_tracker
import tracking
from job_tracker import JobTracker


@pytest.fixture
def tracker(tmp_path):
    return JobTracker(tmp_path / "applications.db")


def test_the_sqlite_tracker_does_everything_the_postgres_one_does():
    db = pytest.importorskip("db")
    postgres = {n for n, _ in inspect.getmembers(db.PostgresTracker, inspect.isfunction) if not n.startswith("_")}
    sqlite = {n for n, _ in inspect.getmembers(JobTracker, inspect.isfunction) if not n.startswith("_")}
    assert postgres - sqlite == set()
    assert set(db.ApplicationRecord.__dataclass_fields__) == set(job_tracker.ApplicationRecord.__dataclass_fields__)


def create(tracker, key="k1", url="https://jobs.example.com/1?utm_source=LinkedIn", **kw):
    return tracker.create(dedup_key=key, title=kw.get("title", "Network Engineer"),
                          company=kw.get("company", "Acme"), location="Remote", url=url)


def test_an_application_is_recorded_once_and_its_title_can_be_corrected(tracker):
    first = create(tracker)
    again = create(tracker, title="Senior Network Engineer")
    assert first == again and tracker.is_duplicate("k1")
    record = tracker.get("k1")
    assert record.title == "Senior Network Engineer" and record.created_at.year >= 2026


def test_a_submitted_application_is_found_again_however_the_link_is_tracked(tracker):
    create(tracker)
    assert tracker.find_submitted(url="https://jobs.example.com/1") is None
    tracker.update_status("k1", "submitted", notes="confirmed")
    assert tracker.find_submitted(url="https://JOBS.example.com/1/?utm_campaign=x").dedup_key == "k1"
    assert tracker.find_submitted(company="acme", title="network engineer").dedup_key == "k1"


def test_documents_are_kept_as_bytes_and_matched(tracker, tmp_path):
    create(tracker)
    resume = tmp_path / "Jane_Doe_Resume.pdf"
    resume.write_bytes(b"%PDF-1.4 resume")
    doc_id = tracker.store_document("k1", "resume", resume, content_text="resume text")
    assert tracker.store_document("k1", "resume", resume) == doc_id           # the same bytes: no second copy
    assert tracker.latest_document("k1", "resume")["content"] == b"%PDF-1.4 resume"
    assert tracker.document_matches("k1", "resume", resume)
    assert tracker.document(doc_id)["filename"] == "Jane_Doe_Resume.pdf"
    record = tracker.get("k1")
    assert [d["kind"] for d in tracker.documents_for(record.id)] == ["resume"]


def test_the_run_history_the_last_page_and_accounts_are_kept(tracker):
    create(tracker)
    tracker.record_event("k1", "status", "reached review", payload={"step": 5})
    assert tracker.events("k1")[0]["payload"] == {"step": 5}
    tracker.update_last_page("k1", "https://acme.wd1.myworkdayjobs.com/apply/step3")
    assert tracker.get("k1").last_page_url.endswith("step3")
    tracker.record_ats_account("Acme", "jane@example.com", "acme.wd1.myworkdayjobs.com", "created")
    assert tracker.get_ats_account(" ACME ")["email"] == "jane@example.com"


def test_answers_are_recalled_by_their_words(tracker):
    create(tracker)
    tracker.record_answer("k1", "acme.com", "Are you willing to travel?", "Yes", answered_by="user")
    found = tracker.recall_answer("How much are you willing to travel?")
    assert found[0]["answer"] == "Yes"
    assert tracker.answers_for(tracker.get("k1").id)[0]["question"] == "Are you willing to travel?"


def test_deleting_an_application_removes_everything_filed_under_it(tracker, tmp_path):
    create(tracker)
    (tmp_path / "r.pdf").write_bytes(b"x")
    tracker.store_document("k1", "resume", tmp_path / "r.pdf")
    tracker.record_answer("k1", "acme.com", "Q?", "A")
    tracker.record_event("k1", "note", "n")
    assert tracker.delete("k1") and not tracker.is_duplicate("k1")
    assert tracker.recall_answer("Q?") == [] and not tracker.delete("k1")


def test_an_older_database_file_is_brought_up_to_date_in_place(tmp_path):
    old = tmp_path / "applications.db"
    with sqlite3.connect(old) as conn:
        conn.execute("""CREATE TABLE applications (id INTEGER PRIMARY KEY AUTOINCREMENT, dedup_key TEXT NOT NULL UNIQUE,
                        title TEXT NOT NULL, company TEXT NOT NULL, location TEXT, source_site TEXT, url TEXT,
                        status TEXT NOT NULL DEFAULT 'prepared', resume_path TEXT, cover_letter_path TEXT, notes TEXT,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        conn.execute("INSERT INTO applications (dedup_key, title, company, created_at, updated_at) "
                     "VALUES ('old', 'Engineer', 'Initech', '2026-09-01T10:00:00+00:00', '2026-09-01T10:00:00+00:00')")
    tracker = JobTracker(old)
    assert tracker.get("old").company == "Initech" and tracker.get("old").last_page_url is None
    tracker.update_last_page("old", "https://x.example.com")
    assert tracker.get("old").last_page_url == "https://x.example.com"


# --- which tracker ---------------------------------------------------------------------------------

@pytest.mark.parametrize("env, expected", [
    ({"TRACKER": "sqlite", "POSTGRES_PASSWORD": "set"}, "sqlite"),
    ({"TRACKER": "postgres"}, "postgres"),
    ({"POSTGRES_PASSWORD": "set"}, "postgres"),          # an existing setup keeps its data
    ({}, "sqlite"),                                       # a new person: nothing to install
])
def test_the_tracker_is_chosen_in_one_place(monkeypatch, env, expected):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    for name in ("TRACKER", "POSTGRES_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert tracking.chosen() == expected


def test_with_nothing_configured_the_tracker_is_sqlite(monkeypatch, tmp_path):
    monkeypatch.setattr(tracking, "chosen", lambda: "sqlite")
    assert isinstance(tracking.open_tracker(tmp_path / "a.db"), JobTracker)


# --- the dashboard on SQLite -------------------------------------------------------------------------

def test_the_dashboard_works_on_sqlite(monkeypatch, tmp_path):
    import profile_setup
    import web_ui
    tracker = JobTracker(tmp_path / "applications.db")
    create(tracker)
    (tmp_path / "r.pdf").write_bytes(b"%PDF-1.4 x")
    doc_id = tracker.store_document("k1", "resume", tmp_path / "r.pdf")
    tracker.record_answer("k1", "acme.com", "Are you willing to travel?", "Yes")
    monkeypatch.setattr(web_ui, "get_tracker", lambda: tracker)
    monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
    web_ui.app.config["TESTING"] = True
    client = web_ui.app.test_client()
    assert b"Network Engineer" in client.get("/").data
    detail = client.get(f"/application/{tracker.get('k1').id}")
    assert detail.status_code == 200 and b"willing to travel" in detail.data
    assert client.get(f"/document/{doc_id}").data == b"%PDF-1.4 x"
