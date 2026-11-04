"""Employer emails survive the tracker and gather in one inbox (f328).

f321 classified employer emails but the recording never survived: the event
kind allowlist rewrote "employer_email" to "other" and the signal rode in the
message, which the tracker always replaces. Now the kind is allowed, the
signal word rides in the payload (no free text, by the sanitizer's design),
the detail History reads it, and the dashboard lists the newest signals across
applications with a link to each one.
"""
import sqlite3
from types import SimpleNamespace

import pytest


@pytest.fixture
def tracker(tmp_path):
    from job_tracker import JobTracker
    db = tmp_path / "t.db"
    tracker = JobTracker(db)
    conn = sqlite3.connect(db)
    for dedup, company, title in (("k1", "Acme", "Network Engineer"), ("k2", "Globex", "NetOps Lead")):
        conn.execute(
            "INSERT INTO applications (dedup_key, title, company, created_at, updated_at)"
            " VALUES (?, ?, ?, 't', 't')",
            (dedup, title, company),
        )
    conn.commit()
    conn.close()
    return tracker


def test_the_signal_kind_and_word_survive_a_round_trip(tracker):
    tracker.record_event("k1", "employer_email", payload={"signal": "interview"})
    (event,) = tracker.events("k1")
    assert event["kind"] == "employer_email"
    assert event["payload"] == {"signal": "interview"}


def test_recording_keeps_the_classified_signal(tracker):
    import employer_signals
    assistant = SimpleNamespace(_last_employer_signal=("rejection", "not moving forward"))
    assert employer_signals.record_employer_signal(tracker, "k1", assistant) is True
    (event,) = tracker.events("k1")
    assert event["kind"] == "employer_email" and event["payload"] == {"signal": "rejection"}


def test_free_text_still_does_not_survive(tracker):
    tracker.record_event("k1", "employer_email", payload={"signal": "offer", "text": "Dear Jon"})
    (event,) = tracker.events("k1")
    assert event["payload"] == {"signal": "offer"}


def test_recent_signals_span_applications_newest_first(tracker):
    tracker.record_event("k1", "employer_email", payload={"signal": "interview"})
    tracker.record_event("k1", "status", "moved")
    tracker.record_event("k2", "employer_email", payload={"signal": "rejection"})
    signals = tracker.recent_employer_signals(10)
    assert [(s["dedup_key"], s["payload"]) for s in signals] == [
        ("k2", {"signal": "rejection"}), ("k1", {"signal": "interview"})]
    assert signals[0]["company"] == "Globex" and signals[0]["title"] == "NetOps Lead"
    assert signals[0]["application_id"]


def test_signals_read_as_words():
    import employer_signals
    assert employer_signals.label("interview") == "Interview"
    assert employer_signals.label("rejection") == "Rejection"
    assert employer_signals.label("bogus") == "Employer email"


def test_the_detail_history_reads_the_signal():
    import detail_view as dv
    rows = dv.group_events([{"kind": "employer_email", "payload": {"signal": "offer"},
                             "message": "event: employer_email", "created_at": None,
                             "screenshot_path": "", "html_path": ""}])
    assert rows[0]["label"] == "Employer email" and rows[0]["message"] == "Offer"


@pytest.fixture
def page(monkeypatch):
    import job_discovery
    import profile_setup
    import web_ui

    def render(tracker):
        monkeypatch.setattr(web_ui, "get_tracker", lambda: tracker)
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


def signal(**kw):
    from datetime import datetime, timezone
    base = dict(kind="employer_email", payload={"signal": "interview"},
                created_at=datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc),
                application_id=7, dedup_key="k1", company="Acme",
                title="Network Engineer", url="https://x.example/1")
    base.update(kw)
    return base


def test_the_inbox_lists_signals_with_links(page):
    tracker = SimpleNamespace(list_all=lambda status=None: [], documents_for=lambda _i: [],
                              answers_for=lambda _i: [], events=lambda *_a, **_k: [],
                              recent_employer_signals=lambda limit=10: [signal()])
    html = page(tracker)
    assert 'id="mail-h"' in html and "Interview" in html
    assert 'href="/application/7"' in html and "Acme" in html


def test_the_inbox_is_quiet_when_nothing_arrived(page):
    tracker = SimpleNamespace(list_all=lambda status=None: [], documents_for=lambda _i: [],
                              answers_for=lambda _i: [], events=lambda *_a, **_k: [],
                              recent_employer_signals=lambda limit=10: [])
    assert "No employer emails recorded yet" in page(tracker)


def test_a_tracker_without_the_inbox_query_still_serves_the_page(page):
    tracker = SimpleNamespace(list_all=lambda status=None: [], documents_for=lambda _i: [],
                              answers_for=lambda _i: [], events=lambda *_a, **_k: [])
    assert 'id="mail-h"' in page(tracker)
