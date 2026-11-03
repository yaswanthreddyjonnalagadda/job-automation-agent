"""The detail page is the application's audit record (f322).

Documents, answers and the auto-submit decision were already passed to the
page; what was missing was the two newest signals: employer emails (from the
mail check) read as "Employer email" in the history, and the submit-policy
line ("Hybrid auto-submit", "Review mode is on", "Daily cap reached") under
the decision, so the owner sees not just what was decided but under which
rule.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import detail_view as dv
import profile_setup
import web_ui


def test_employer_email_reads_as_words():
    assert dv.event_label("employer_email") == "Employer email"


def test_the_decision_carries_its_policy_line():
    view = dv.decision_view({"eligible": True, "reasons": [], "policy": "Hybrid auto-submit (1 of 10 submitted today)."},
                            "done")
    assert view["policy"] == "Hybrid auto-submit (1 of 10 submitted today)."


def render_detail(monkeypatch, decision):
    rec = SimpleNamespace(id=1, company="Acme", title="Engineer", status="submitted", notes="",
                          url="https://j.example.com/1", last_page_url="", updated_at=None,
                          created_at=None, dedup_key="k1", location="")
    events = [{"kind": "employer_email", "message": "interview: Acme interview invite",
               "created_at": datetime.now(timezone.utc), "screenshot_path": None, "html_path": None}]
    if decision is not None:
        events.append({"kind": "auto_submit", "message": "m", "created_at": datetime.now(timezone.utc),
                       "payload": decision})
    monkeypatch.setattr(web_ui, "get_tracker", lambda: SimpleNamespace(
        list_all=lambda: [rec], documents_for=lambda _i: [], answers_for=lambda _i: [],
        events=lambda *_a, **_k: events, read_checkpoint_by_dedup_key=lambda _k: None))
    monkeypatch.setattr(web_ui, "_current_runs", lambda: {})
    monkeypatch.setattr(web_ui, "latest_validation", lambda _r: {})
    monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
    web_ui.app.config["TESTING"] = True
    return web_ui.app.test_client().get("/application/1").get_data(as_text=True)


def test_the_page_shows_the_new_signals(monkeypatch):
    html = render_detail(monkeypatch, {"eligible": True, "reasons": [],
                                       "policy": "Review mode is on: every application waits for you."})
    assert "Employer email" in html
    assert "Review mode is on" in html
