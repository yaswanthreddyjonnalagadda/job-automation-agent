"""Detail page, Progress and System Health read plainly and do not overstate (PR E of the 1 November 2026 UI review).

Classes: a raw machine name is never the label; a repeated row is one row with a count; a decision is explained in
words; a worker that has not really reported is not called healthy; a percentage that is 100% everywhere says nothing."""
import re
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import detail_view as dv
import health_view as hv
import profile_setup
import web_ui


# -- history -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind, label", [("auto_submit", "Auto-submit check"), ("handoff_started", "Stopped for you"),
                                         ("note", "Note"), ("some_new_kind", "Some new kind"), ("", "Event")])
def test_an_event_kind_reads_as_words(kind, label):
    assert dv.event_label(kind) == label


def test_repeated_rows_become_one_row_with_a_count():
    ev = lambda k, m, t: {"kind": k, "message": m, "created_at": t, "screenshot_path": None, "html_path": None}
    rows = dv.group_events([ev("note", "same", 3), ev("note", "same", 2), ev("note", "same", 1), ev("auto_submit", "x", 0)])
    assert [(r["label"], r["count"]) for r in rows] == [("Note", 3), ("Auto-submit check", 1)]
    assert rows[0]["created_at"] == 3 and rows[0]["since"] == 1


def test_rows_that_differ_or_are_apart_are_not_merged():
    ev = lambda m: {"kind": "note", "message": m, "created_at": 0}
    assert len(dv.group_events([ev("a"), ev("b"), ev("a")])) == 3


# -- answers and the decision ------------------------------------------------------------------------------------

@pytest.mark.parametrize("value, text", [("user", "You"), ("claude", "AI"), ("PROFILE", "From profile"),
                                         ("USER_OVERRIDE", "You"), ("AI_INFERENCE", "AI"), ("odd-source", "odd-source")])
def test_who_answered_is_an_icon_and_words(value, text):
    shown = str(dv.answered_by(value))
    assert f"<span>{text}</span>" in shown and "<svg" in shown and 'aria-hidden="true"' in shown


def test_an_unknown_source_cannot_inject_markup():
    assert "<script>" not in str(dv.answered_by("<script>alert(1)</script>"))


def test_not_eligible_on_something_you_submitted_says_so():
    view = dv.decision_view({"eligible": False, "decided_at": "2026-11-01T17:41:00+00:00", "reasons": []}, "done")
    assert "You submitted this yourself" in view["note"] and view["no_reasons"] and view["when"].year == 2026


def test_reasons_are_kept_when_stored():
    view = dv.decision_view({"eligible": False, "reasons": ["a CAPTCHA was showing"]}, "needs")
    assert view["reasons"] == ["a CAPTCHA was showing"] and not view["no_reasons"] and "waiting for you" in view["note"]


# -- the rendered detail page ------------------------------------------------------------------------------------

@pytest.fixture
def detail(monkeypatch):
    def render(status="submitted", events=None, answers=None, decision=None):
        rec = SimpleNamespace(id=1, company="Acme", title="Engineer", status=status, notes="", url="https://j.example.com/1",
                              last_page_url="", updated_at=None, created_at=None, dedup_key="k1", location="")
        events = list(events or [])
        if decision is not None:
            events.append({"kind": "auto_submit", "message": "m", "created_at": datetime.now(timezone.utc),
                           "payload": decision})
        monkeypatch.setattr(web_ui, "get_tracker", lambda: SimpleNamespace(
            list_all=lambda: [rec], documents_for=lambda _i: [], answers_for=lambda _i: answers or [],
            events=lambda *_a, **_k: events, read_checkpoint_by_dedup_key=lambda _k: None))
        monkeypatch.setattr(web_ui, "_current_runs", lambda: {})
        monkeypatch.setattr(web_ui, "latest_validation", lambda _r: {})
        monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
        web_ui.app.config["TESTING"] = True
        return web_ui.app.test_client().get("/application/1").get_data(as_text=True)
    return render


def _event(kind, message="m"):
    return {"kind": kind, "message": message, "created_at": datetime.now(timezone.utc),
            "screenshot_path": None, "html_path": None}


def test_history_shows_plain_labels_and_one_row_for_repeats(detail):
    html = detail(events=[_event("note", "same")] * 3 + [_event("handoff_started", "x")])
    assert "Stopped for you" in html and ">handoff_started<" not in html and ">note<" not in html
    assert len(re.findall(r"<td>same\b", html)) == 1 and "&times;3" in html


def test_the_answers_table_shows_who_gave_each_answer_in_words(detail):
    html = detail(answers=[{"question": "Q", "answer": "A", "answered_by": "claude"}])
    assert "<span>AI</span>" in html


def test_change_status_lives_in_the_more_menu_not_loose_on_the_page(detail):
    html = detail()
    form = re.search(r'<form[^>]*action="/application/1/status".*?</form>', html, re.S).group(0)
    before = html[:html.index(form)]
    assert before.rfind('<details class="menu"') > before.rfind("</details>")


def test_a_not_eligible_decision_is_explained(detail):
    html = detail(status="submitted",
                  decision={"eligible": False, "decided_at": "2026-11-01T17:41:00+00:00", "reasons": []})
    assert "You submitted this yourself" in html and "No reasons were stored" in html and "1 Nov 2026" in html


# -- progress ----------------------------------------------------------------------------------------------------

@pytest.fixture
def progress_page(monkeypatch):
    def render(statuses):
        rows = [SimpleNamespace(status=s, url=f"https://boards.greenhouse.io/x/{i}", notes="", dedup_key=f"k{i}",
                                company="C", title="T") for i, s in enumerate(statuses)]
        monkeypatch.setattr(web_ui, "get_tracker", lambda: SimpleNamespace(list_all=lambda: rows))
        monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
        web_ui.app.config["TESTING"] = True
        return web_ui.app.test_client().get("/progress").get_data(as_text=True)
    return render


def test_the_funnel_says_what_it_counts(progress_page):
    assert "failed starts aren" in progress_page(["submitted", "ready_to_submit"])


def test_a_percentage_that_is_100_at_every_step_is_not_shown(progress_page):
    html = re.sub(r"<style>.*?</style>", "", progress_page(["submitted", "submitted"]), flags=re.S)
    assert "of started" not in html and "100%" not in html


def test_percentages_show_when_they_say_something(progress_page):
    assert "of started" in progress_page(["submitted", "form_filled", "form_filled"])


def test_empty_outcome_tiles_say_how_to_fill_them(progress_page):
    assert progress_page(["submitted"]).count("Mark outcomes on each application") == 2


# -- system health -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("worker, label", [
    ({"status": "IDLE", "metrics": {"cpu_percent": 0, "memory_percent": 0}, "git_sha": "unknown"}, "Stale"),
    ({"status": "IDLE", "metrics": {"cpu_percent": 0.0, "memory_percent": 0.0}, "git_sha": ""}, "Stale"),
    ({"status": "IDLE", "metrics": {"cpu_percent": 0, "memory_percent": 0}, "git_sha": "ab12cd34ef"}, "Idle"),
    ({"status": "BUSY", "metrics": {"cpu_percent": 40, "memory_percent": 30}, "git_sha": "unknown"}, "Busy"),
    ({"status": "PRESSURE", "metrics": {"cpu_percent": 90, "memory_percent": 80}, "git_sha": "ab12cd34"}, "Pressure"),
    ({"status": "OFFLINE", "metrics": {"cpu_percent": 5, "memory_percent": 5}, "git_sha": "ab12cd34"}, "Offline"),
])
def test_a_worker_that_has_not_really_reported_is_stale_not_healthy(worker, label):
    assert hv.worker_state(worker)[0] == label


def test_the_health_page_does_not_wait_for_the_browser_check(monkeypatch):
    def slow():
        raise AssertionError("the page waited for the browser check")
    monkeypatch.setattr(web_ui, "_browser_check", slow)
    web_ui.app.config["TESTING"] = True
    html = web_ui.app.test_client().get("/health", headers={"Accept": "text/html"}).get_data(as_text=True)
    assert "Playwright Chromium" in html and "Checking" in html


def test_the_browser_check_is_a_route_of_its_own(monkeypatch):
    monkeypatch.setattr(web_ui, "_browser_check",
                        lambda: {"title": "Playwright Chromium", "ok": True, "message": "Browser located: chrome.exe"})
    web_ui.app.config["TESTING"] = True
    data = web_ui.app.test_client().get("/health/browser").get_json()
    assert data["ok"] is True and "chrome.exe" in data["message"]
