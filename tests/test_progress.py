"""The Progress page: how far applications get, by site, and why runs stop."""
from types import SimpleNamespace

import pytest

import progress


def rec(key, status, url="https://acme.wd1.myworkdayjobs.com/job/1", notes=""):
    return SimpleNamespace(dedup_key=key, status=status, url=url, notes=notes)


RECORDS = [
    rec("a", "offer", "https://boards.greenhouse.io/acme/jobs/1"),
    rec("b", "rejected", "https://jobs.ashbyhq.com/writer/1"),
    rec("c", "submitted", "https://boards.greenhouse.io/globex/jobs/2"),
    rec("d", "ready_to_submit"),
    rec("e", "needs_user_review", notes="the page did not move on after 3 tries | worth checking: x"),
    rec("f", "needs_user_review", notes="the page did not move on after 5 tries"),
    rec("g", "needs_user_review", notes="a CAPTCHA is showing"),
    rec("h", "needs_user_review"),                                     # reached the hand-over at Review
    rec("i", "skipped", notes="Skipped: no visa sponsorship"),
]


def test_the_funnel_from_started_to_interview():
    f = progress.funnel(RECORDS, reached_hand_over={"h"})
    assert (f.started, f.reached_review, f.submitted, f.replied, f.interviews) == (8, 5, 3, 2, 1)
    assert f.skipped == 1 and f.stuck == 3


def test_each_site_is_counted():
    f = progress.funnel(RECORDS, reached_hand_over={"h"})
    assert f.by_site["Workday"] == {"started": 5, "reached_review": 2, "submitted": 0}
    assert f.by_site["Greenhouse"] == {"started": 2, "reached_review": 2, "submitted": 2}
    assert list(f.by_site)[0] == "Workday"                              # the busiest first


def test_stop_reasons_are_grouped_whatever_their_numbers():
    f = progress.funnel(RECORDS)
    assert f.stop_reasons[0] == ("the page did not move on after N tries", 2)


@pytest.mark.parametrize("url, site", [
    ("https://kbi.wd1.myworkdayjobs.com/x", "Workday"), ("https://jobs.lever.co/acme/1", "Lever"),
    ("https://acme.icims.com/jobs/1", "iCIMS"), ("https://careers.initech.com/jobs/7", "initech.com"), ("", "unknown"),
])
def test_sites_are_told_apart_by_their_hosts(url, site):
    assert progress.site_of(url) == site


def test_rates_never_divide_by_zero():
    f = progress.funnel([])
    assert f.rate(f.submitted, f.started) == "-"


def test_the_page_and_the_new_statuses_on_the_dashboard(tmp_path, monkeypatch):
    import profile_setup
    import web_ui
    from job_tracker import JobTracker
    tracker = JobTracker(tmp_path / "a.db")
    tracker.create(dedup_key="k", title="Network Engineer", company="Acme", url="https://boards.greenhouse.io/acme/jobs/1")
    tracker.update_status("k", "submitted")
    monkeypatch.setattr(web_ui, "get_tracker", lambda: tracker)
    monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
    client = web_ui.app.test_client()
    client.post(f"/application/{tracker.get('k').id}/status", data={"status": "interviewing"})
    assert tracker.get("k").status == "interviewing"
    page = client.get("/progress").data.decode()
    assert "Greenhouse" in page and "interviews" in page and "Progress" in client.get("/").data.decode()
