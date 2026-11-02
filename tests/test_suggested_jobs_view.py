"""Suggested jobs are shown honestly (UI review of 1 November 2026, PR B).

Classes: a job the owner already applied to is marked, not hidden; age comes from when it was found; a long list of
places is cut to the first; the tracker is only read; the cockpit reports what it knows (stop, check and resume).
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import discovered_view as dv

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def job(**kw):
    base = dict(id=1, company_name="Acme", job_title="Network Engineer", url="https://boards.example.com/acme/1",
                location="Austin, TX", is_remote=False, match_score=65, discovered_at="2026-11-01T09:00:00")
    base.update(kw)
    return base


def app(status="submitted", **kw):
    base = dict(id=7, status=status, company="Acme", title="Network Engineer", url="https://elsewhere.example.com/x",
                location="", notes="", last_page_url="", updated_at=None, created_at=None, dedup_key="k7")
    base.update(kw)
    return SimpleNamespace(**base)


# -- already applied ---------------------------------------------------------------------------------------------

def test_a_job_with_a_sent_application_for_the_same_company_and_title_is_marked_not_dropped():
    shown = dv.prepare([job()], [app("submitted")], NOW)
    assert len(shown) == 1 and shown[0]["already_applied"] is True


def test_the_same_posting_address_is_matched_whatever_the_tracking_noise():
    sent = app("submitted", company="Other", title="Other",
               url="https://www.boards.example.com/acme/1/?gh_src=abc#apply")
    assert dv.prepare([job()], [sent], NOW)[0]["already_applied"] is True


@pytest.mark.parametrize("status", ["skipped", "needs_user_review", "form_filled", "failed", "prepared"])
def test_an_application_that_was_not_sent_does_not_mark_the_job(status):
    assert dv.prepare([job()], [app(status)], NOW)[0]["already_applied"] is False


def test_company_and_title_match_ignores_case_and_punctuation():
    sent = app("submitted", company="ACME,  Inc", title="network engineer")
    assert dv.prepare([job(company_name="Acme Inc")], [sent], NOW)[0]["already_applied"] is True


def test_marking_reads_the_tracker_and_changes_nothing_in_it():
    records = [app("submitted")]
    before = [vars(r).copy() for r in records]
    dv.prepare([job()], records, NOW)
    assert [vars(r) for r in records] == before


def test_applied_jobs_come_after_the_rest_without_being_removed():
    jobs = [job(id=1, match_score=90), job(id=2, company_name="Other", match_score=60)]
    shown = dv.prepare(jobs, [app("submitted")], NOW)
    assert [j["id"] for j in shown] == [2, 1] and shown[1]["already_applied"]


# -- age and order -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("found, label, new", [
    ("2026-11-01T09:00:00", "found today", True),
    ("2026-10-31T09:00:00", "found yesterday", True),
    ("2026-10-28T09:00:00", "found 4 days ago", False),
    ("2026-10-30T09:00:00", "found 2 days ago", True),
])
def test_posting_age_comes_from_when_it_was_found(found, label, new):
    assert dv.posting_age(found, NOW) == (label, new)


def test_an_unreadable_date_gives_no_age_and_no_new_badge():
    assert dv.posting_age("", NOW) == ("", False)
    assert dv.posting_age("not a date", NOW) == ("", False)


def test_newest_first_within_the_same_score():
    older = job(id=1, discovered_at=(NOW - timedelta(days=5)).isoformat())
    newer = job(id=2, company_name="B", discovered_at=(NOW - timedelta(hours=2)).isoformat())
    best = job(id=3, company_name="C", match_score=80, discovered_at=(NOW - timedelta(days=9)).isoformat())
    assert [j["id"] for j in dv.prepare([older, newer, best], [], NOW)] == [3, 2, 1]


# -- places ------------------------------------------------------------------------------------------------------

def test_a_long_list_of_places_is_cut_to_the_first_and_a_count():
    place, more = dv.short_location("Austin, TX; Denver, CO; Remote; Boston, MA", False)
    assert (place, more) == ("Austin, TX", 3)


def test_a_single_place_with_a_comma_is_not_split():
    assert dv.short_location("San Francisco, CA", False) == ("San Francisco, CA", 0)


def test_remote_is_not_listed_twice_when_the_remote_tag_shows():
    assert dv.short_location("Remote; Austin, TX", True) == ("Austin, TX", 0)
    assert dv.short_location("Remote", True) == ("", 0)
    assert dv.short_location("Remote", False) == ("Remote", 0)


# -- the cockpit -------------------------------------------------------------------------------------------------

COCKPIT_JS = (Path(__file__).resolve().parent.parent / "static" / "cockpit.js").read_text(encoding="utf-8")
COCKPIT_CARD = (Path(__file__).resolve().parent.parent / "templates" / "cockpit_card.html").read_text(encoding="utf-8")


def test_the_cockpit_offers_no_pause_because_the_runtime_cannot_pause():
    assert "pause" not in COCKPIT_JS.lower() and "pause" not in COCKPIT_CARD.lower()


def test_stopping_when_nothing_runs_does_not_say_stopped():
    assert "not_running" in COCKPIT_JS


def test_check_and_resume_does_not_claim_the_agent_is_carrying_on_before_the_next_status_says_so():
    assert "The agent is carrying on" not in COCKPIT_JS
    assert "Checking" in COCKPIT_JS


# -- the rendered page -------------------------------------------------------------------------------------------

@pytest.fixture
def page(monkeypatch):
    import job_discovery
    import profile_setup
    import web_ui

    def render(jobs, applications=(), scan=None):
        monkeypatch.setattr(web_ui, "get_tracker", lambda: SimpleNamespace(
            list_all=lambda: list(applications), documents_for=lambda _i: [], answers_for=lambda _i: [],
            events=lambda *_a, **_k: []))
        monkeypatch.setattr(web_ui, "_current_runs", lambda: {})
        monkeypatch.setattr(web_ui, "waiting_runs", lambda _d: [])
        monkeypatch.setattr(web_ui, "last_scan", lambda: scan or {})
        monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
        monkeypatch.setattr(job_discovery.JobDiscoveryEngine, "__init__", lambda self, *a, **k: None)
        monkeypatch.setattr(job_discovery.JobDiscoveryEngine, "get_discovered_jobs", lambda self, **k: jobs)
        web_ui.app.config["TESTING"] = True
        return web_ui.app.test_client().get("/").get_data(as_text=True)
    return render


def _recent(**kw):
    kw.setdefault("discovered_at", datetime.now(timezone.utc).isoformat())
    kw.setdefault("ats_type", "greenhouse")
    kw.setdefault("matched_skills", [])
    return job(**kw)


def test_an_applied_job_is_shown_marked_and_not_hidden(page):
    html = page([_recent()], [app("submitted")])
    assert "Already applied" in html and "Network Engineer" in html


def test_a_new_job_is_badged_and_an_old_one_is_not(page):
    html = page([_recent(), _recent(id=2, company_name="B", discovered_at="2026-01-01T00:00:00")])
    assert html.count('class="tag new"') == 1 and "found today" in html


def test_remote_appears_once_and_a_long_list_is_cut(page):
    html = page([_recent(is_remote=True, location="Remote; Austin, TX; Denver, CO; Boston, MA")])
    assert html.count('class="tag remote"') == 1
    assert "+2 more" in html and "Boston, MA</span>" not in html


def test_not_interested_is_a_plain_link_not_a_second_button(page):
    html = page([_recent()])
    assert 'class="linklike" aria-label="Not interested' in html


def test_the_scan_line_says_when_and_how_many_boards(page):
    at = datetime(2026, 11, 1, 17, 41, tzinfo=timezone.utc)
    shown = page([_recent()], scan={"at": at, "boards": 12, "saved": 3})
    assert "12 boards checked" in shown and "Updated" in shown
    assert "Not checked yet" in page([_recent()])


# -- one role open in several places is one card -----------------------------------------------------------------

def test_the_same_role_listed_for_several_places_is_one_card_with_a_count():
    jobs = [job(id=1, url="https://x/a", location="Austin, TX", match_score=65),
            job(id=2, url="https://x/b", location="Denver, CO", match_score=65),
            job(id=3, url="https://x/c", location="Boston, MA", match_score=65),
            job(id=4, company_name="Other", url="https://x/d")]
    shown = dv.prepare(jobs, [], NOW)
    assert len(shown) == 2
    merged = next(j for j in shown if j["company_name"] == "Acme")
    assert merged["other_openings"] == 2 and merged["id"] == 1


def test_the_best_scored_opening_is_the_one_kept():
    jobs = [job(id=1, url="https://x/a", resume_score=40), job(id=2, url="https://x/b", resume_score=80)]
    assert dv.prepare(jobs, [], NOW)[0]["id"] == 2


def test_roles_that_differ_in_title_or_company_are_not_merged():
    jobs = [job(id=1), job(id=2, job_title="Staff Network Engineer", url="https://x/2")]
    assert len(dv.prepare(jobs, [], NOW)) == 2
