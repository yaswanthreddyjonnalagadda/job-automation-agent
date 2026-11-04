"""The suggestions section is calm and explains itself (f326).

Slices 1 and 2 stacked six control rows above the first card and left two
regions sharing one label. Now the instant chips and the bulk bar share one
tools row, the listing rules fold into a "How this list works" disclosure, a
first-look hint points at the new filters once, every card's age carries when
it was found and posted, and each labelled region is named once.
"""
from types import SimpleNamespace

import pytest

import discovered_view as dv
import job_discovery as jd


@pytest.fixture
def feed(monkeypatch):
    import profile_setup
    import web_ui

    def render(jobs):
        monkeypatch.setattr(web_ui, "get_tracker", lambda: SimpleNamespace(
            list_all=lambda: [], documents_for=lambda _i: [], answers_for=lambda _i: [],
            events=lambda *_a, **_k: []))
        monkeypatch.setattr(web_ui, "_current_runs", lambda: {})
        monkeypatch.setattr(web_ui, "waiting_runs", lambda _d: [])
        monkeypatch.setattr(web_ui, "last_scan", lambda: {})
        monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
        monkeypatch.setattr(jd.JobDiscoveryEngine, "__init__", lambda self, *a, **k: None)
        monkeypatch.setattr(jd.JobDiscoveryEngine, "get_discovered_jobs", lambda self, **k: jobs)
        monkeypatch.setattr(jd, "get_discovered_countries", lambda: ["CA"])
        web_ui.app.config["TESTING"] = True
        return web_ui.app.test_client().get("/").get_data(as_text=True)
    return render


def job(**kw):
    base = dict(id=1, company_name="Acme", job_title="Network Engineer",
                url="https://boards.example.com/acme/1", location="Austin, TX", is_remote=False,
                match_score=65, discovered_at="2026-11-03T09:00:00",
                posted_at="2026-11-01T09:00:00", posted_precision="day",
                ats_type="greenhouse", matched_skills=[])
    base.update(kw)
    return base


def section(html):
    return html.split('id="jobs-h"')[1].split("</section>")[0]


def test_chips_and_bulk_share_one_tools_row(feed):
    tools = section(feed([job()]))
    assert tools.count('class="feed-tools"') == 1
    row = tools.split('class="feed-tools"')[1]
    assert "data-job-filter" in row and 'id="bulk-form"' in row
    assert row.index("data-job-filter") < row.index('id="bulk-form"')


def test_the_listing_rules_fold_into_a_disclosure(feed):
    tools = section(feed([job()]))
    assert "<summary>How this list works</summary>" in tools
    assert tools.index("<details") < tools.index("Newest first") < tools.index("</details>")


def test_each_labelled_region_is_named_once(feed):
    tools = section(feed([job()]))
    assert tools.count("Filter suggested jobs") == 1


def test_the_first_look_hint_is_present_but_hidden_until_seen(feed):
    from pathlib import Path
    assert 'id="feed-hint" hidden' in feed([job()])
    js = (Path(__file__).resolve().parent.parent / "static" / "cockpit.js").read_text(encoding="utf-8")
    assert "jaaFeedHintSeen" in js and "feed-hint-ok" in js


def test_age_tooltip_names_found_and_posted_days():
    shown = dv.prepare([job()], [], None)
    assert shown[0]["age_title"] == "Found 3 Nov 2026 · Posted 1 Nov 2026"
    unposted = dv.prepare([job(posted_at="", posted_precision="")], [], None)
    assert unposted[0]["age_title"] == "Found 3 Nov 2026"
    unknown = dv.prepare([job(discovered_at="", posted_at="", posted_precision="")], [], None)
    assert unknown[0]["age_title"] == ""


def test_the_card_shows_the_tooltip_on_its_age(feed):
    assert 'title="Found 3 Nov 2026 · Posted 1 Nov 2026"' in section(feed([job()]))
