"""The Applications page puts the owner's own work first and says each thing once (PR D of the 1 November 2026
UI review). Classes: order follows what needs doing; a count appears in one place; an idle card is one line; a
display value that cannot be a name is not shown as one."""
import re
from types import SimpleNamespace

import pytest

import profile_setup
import ui_shell
import web_ui


def _rows(statuses, company="Company"):
    return [SimpleNamespace(id=i + 1, company=company if isinstance(company, str) else company[i], location="",
                            title="Engineer", status=s, notes="", url=f"https://jobs.example.com/{i}",
                            last_page_url="", updated_at=None, created_at=None, dedup_key=f"k{i}")
            for i, s in enumerate(statuses)]


@pytest.fixture
def page(monkeypatch):
    import job_discovery

    def render(statuses=("needs_user_review", "submitted", "skipped"), company="Company", runs=None):
        rows = _rows(statuses, company)
        monkeypatch.setattr(web_ui, "get_tracker", lambda: SimpleNamespace(
            list_all=lambda: rows, documents_for=lambda _i: [], answers_for=lambda _i: [], events=lambda *_a, **_k: []))
        monkeypatch.setattr(web_ui, "_current_runs", lambda: runs or {})
        monkeypatch.setattr(web_ui, "waiting_runs", lambda _d: [])
        monkeypatch.setattr(web_ui, "last_scan", lambda: {})
        monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
        monkeypatch.setattr(job_discovery.JobDiscoveryEngine, "__init__", lambda self, *a, **k: None)
        monkeypatch.setattr(job_discovery.JobDiscoveryEngine, "get_discovered_jobs", lambda self, **k: [
            dict(id=1, company_name="Acme", job_title="Engineer", url="https://x.example/1", location="Austin",
                 is_remote=False, ats_type="greenhouse", match_score=65, matched_skills=[], discovered_at="2026-11-01T09:00:00")])
        web_ui.app.config["TESTING"] = True
        return web_ui.app.test_client().get("/").get_data(as_text=True)
    return render


def test_the_owners_work_comes_before_the_suggestions(page):
    html = page()
    needs, current, history, suggested = (html.index(m) for m in
                                          ('id="needs-h"', 'id="live-cockpit-card"', 'id="hist-h"', 'id="jobs-h"'))
    assert needs < current < history < suggested


def test_a_count_is_shown_once_not_in_a_summary_a_legend_and_chips(page):
    html = page()
    assert 'id="dash-summary"' not in html and "dash-legend" not in html
    assert re.search(r'data-filter="needs"[^>]*>Needs you<span class="n">1</span>', html)      # the chips keep theirs


def test_the_progress_bar_says_what_it_measures(page):
    html = page(("submitted", "submitted", "skipped", "needs_user_review"))
    assert "Submitted of tracked" in html and "2 of 4" in html


def test_the_idle_card_is_one_line_with_the_last_result(page):
    runs = {"https://jobs.example.com/1": {"state": "finished -- submitted", "title": "Engineer", "company": "Acme"}}
    html = page(runs=runs)
    rest = re.search(r'<div class="cockpit-rest">(.*?)</div>\s*\n\s*<div class="cockpit-live">', html, re.S).group(1)
    assert "No application is running" in rest and "finished -- submitted" in rest
    assert "<h2" not in rest and rest.count("<p") <= 1


def test_the_idle_card_with_no_history_still_says_what_is_true(page):
    html = page()
    assert "No application is running" in html and "Nothing has run yet" in html


@pytest.mark.parametrize("company, shown", [("P", False), ("", False), ("-", False), ("Bitdeer", True),
                                            ("3M", True), ("IBM", True), ("X Corp", True)])
def test_a_company_that_cannot_be_a_name_is_flagged(company, shown):
    label = ui_shell.company_label(company)
    assert (label == company) is shown
    assert shown or label == "Company unclear"


def test_the_table_shows_company_unclear_for_a_one_letter_company(page):
    html = page(("submitted",), company="P")
    assert "Company unclear" in html and "<strong>P</strong>" not in html
