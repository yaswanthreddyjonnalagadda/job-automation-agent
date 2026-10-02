"""Every dashboard page shares one layout, and every status the tracker can hold is shown as a readable pill.

Before 29 September each page drew its own header (a line of inline links, or none), and a status the stylesheet
did not know -- DISQUALIFIED_POLICY_MISMATCH, interviewing, offer -- showed as bare capitals with no pill.
"""
import re
from types import SimpleNamespace

import pytest

import profile_setup
import ui_shell
import web_ui

STATUSES = ["prepared", "form_filled", "ready_to_submit", "needs_user_review", "submitted", "skipped",
            "DISQUALIFIED_POLICY_MISMATCH", "BLOCKED_VALIDATION_LOOP", "interviewing", "rejected", "offer"]


def test_every_tracker_status_has_a_label_and_a_pill_colour():
    for status in STATUSES:
        assert ui_shell.status_label(status) and "_" not in ui_shell.status_label(status)
        assert re.search(r"\.pill\." + re.escape(ui_shell.status_class(status)) + r"\b", ui_shell.BASE_CSS), status


@pytest.mark.parametrize("status, group", [("needs_user_review", "needs"), ("BLOCKED_VALIDATION_LOOP", "needs"),
                                           ("offer", "done"), ("DISQUALIFIED_POLICY_MISMATCH", "skipped"),
                                           ("form_filled", "working")])
def test_status_groups(status, group):
    assert ui_shell.status_group(status) == group


@pytest.fixture
def client(monkeypatch):
    rows = [SimpleNamespace(id=i + 1, company=f"Company {i}", location="Austin, Texas", title="Engineer", status=s,
                            notes="", url=f"https://jobs.example.com/{i}", last_page_url="", updated_at=None,
                            created_at=None, dedup_key=f"k{i}") for i, s in enumerate(STATUSES)]
    tracker = SimpleNamespace(list_all=lambda: rows, documents_for=lambda _i: [], answers_for=lambda _i: [],
                              events=lambda *_a, **_k: [])
    monkeypatch.setattr(web_ui, "get_tracker", lambda: tracker)
    monkeypatch.setattr(web_ui, "_current_runs", lambda: {})
    monkeypatch.setattr(web_ui, "waiting_runs", lambda _d: [])
    monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
    monkeypatch.setattr(web_ui, "latest_validation", lambda _r: {})
    web_ui.app.config["TESTING"] = True
    return web_ui.app.test_client()


@pytest.mark.parametrize("path, active", [("/", "Applications"), ("/progress", "Progress"),
                                          ("/application/1", "Applications")])
def test_pages_share_the_header_and_mark_where_you_are(client, path, active):
    page = client.get(path).get_data(as_text=True)
    assert 'rel="icon"' in page and 'name="viewport"' in page
    for tab in ("Applications", "Progress", "Profile", "Saved answers", "Settings"):
        assert f">{tab}</a>" in page
    assert re.search(r'class="active">' + active + "</a>", page)


def test_every_status_on_the_list_is_a_pill_with_words(client):
    page = client.get("/?show=20").get_data(as_text=True)
    for status in STATUSES:
        assert f'<span class="pill {ui_shell.status_class(status)}"' in page
    assert "DISQUALIFIED POLICY MISMATCH" not in page and "Skipped (policy)" in page


def test_an_application_page_links_its_runs_log(client, monkeypatch, tmp_path):
    log = tmp_path / "ui_run_x.log"
    log.write_text("READ: application form")
    monkeypatch.setitem(web_ui._RUNS, "https://jobs.example.com/0", {"state": "ended", "log": str(log)})
    assert f'href="/log?path={log}"' in client.get("/application/1").get_data(as_text=True)
    assert "View log" not in client.get("/application/2").get_data(as_text=True)     # no run, no link


def test_the_filter_counts_match_the_groups(client):
    page = client.get("/").get_data(as_text=True)
    assert re.search(r'data-filter="needs"[^>]*>Needs you<span class="n">3</span>', page)
