"""The dashboard says only what is true, and offers only links that work (UI review of 1 November 2026).

Classes covered, not the single pages where they were seen:
  * a promise in the copy that depends on a setting (auto-submit) reads that setting;
  * a link that cannot be served is not drawn (View log 404'd for every run log);
  * a link never carries the owner's absolute file path;
  * an instruction for the owner is not shown once the application is finished;
  * a score is labelled with what it measures;
  * "Needs you" is visible when it is above zero and quiet when it is zero.
"""
import re
from types import SimpleNamespace

import pytest

import profile_setup
import ui_shell
import web_ui


def _tracker(statuses):
    rows = [SimpleNamespace(id=i + 1, company=f"Company {i}", location="Austin, Texas", title="Engineer", status=s,
                            notes="", url=f"https://jobs.example.com/{i}", last_page_url="", updated_at=None,
                            created_at=None, dedup_key=f"k{i}") for i, s in enumerate(statuses)]
    return SimpleNamespace(list_all=lambda: rows, documents_for=lambda _i: [], answers_for=lambda _i: [],
                           events=lambda *_a, **_k: [],
                           read_checkpoint_by_dedup_key=lambda _k: {"handoff_category": "captcha",
                                                                    "handoff_required_action": "Solve it"})


@pytest.fixture
def make_client(monkeypatch):
    def build(statuses=("submitted", "skipped"), runs=None, auto_submit=False):
        monkeypatch.setattr(web_ui, "get_tracker", lambda: _tracker(statuses))
        monkeypatch.setattr(web_ui, "_current_runs", lambda: runs or {})
        monkeypatch.setattr(web_ui, "waiting_runs", lambda _d: [])
        monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
        monkeypatch.setattr(web_ui, "latest_validation", lambda _r: {})
        monkeypatch.setenv("AUTO_SUBMIT_VERIFIED_ONLY", "true" if auto_submit else "")
        web_ui.app.config["TESTING"] = True
        return web_ui.app.test_client()
    return build


# -- the promise depends on the setting ---------------------------------------------------------------------------

def test_the_header_promise_follows_the_auto_submit_setting(make_client):
    off = make_client(auto_submit=False).get("/").get_data(as_text=True)
    on = make_client(auto_submit=True).get("/").get_data(as_text=True)
    assert "applies for you, you submit" in off and "you submit it yourself" in off
    assert "applies for you, you submit" not in on and "you submit it yourself" not in on
    assert "every answer is verified" in on


def test_the_resting_card_does_not_say_you_review_everything_when_auto_submit_is_on(make_client):
    on = make_client(auto_submit=True).get("/").get_data(as_text=True)
    assert "You review everything" not in on
    assert "every answer is verified" in on


def test_the_promise_is_one_function(monkeypatch):
    monkeypatch.setenv("AUTO_SUBMIT_VERIFIED_ONLY", "yes")
    assert "verified" in ui_shell.submit_promise()
    monkeypatch.setenv("AUTO_SUBMIT_VERIFIED_ONLY", "")
    assert "you submit" in ui_shell.submit_promise().lower()


# -- links -------------------------------------------------------------------------------------------------------

def test_an_unservable_log_or_screenshot_is_not_linked(make_client, tmp_path):
    log = tmp_path / "logs" / "ui_run.log"
    log.parent.mkdir()
    log.write_text("x", encoding="utf-8")            # no manifest.json beside it: the viewer refuses it
    run = {"https://jobs.example.com/0": {"state": "finished -- submitted", "title": "Engineer", "company": "C",
                                          "log": str(log), "screenshot": str(tmp_path / "gone.png"), "app_id": 1}}
    html = make_client(runs=run).get("/").get_data(as_text=True)
    assert "View log" not in html
    assert "Last screenshot" not in html


def test_artifact_links_never_carry_the_owners_absolute_path(make_client, monkeypatch):
    root = web_ui.BASE_DIR
    shown = root / "output" / "Job" / "shot.png"
    monkeypatch.setattr(web_ui, "_artifact_servable", lambda _p: True)
    run = {"https://jobs.example.com/0": {"state": "finished -- submitted", "title": "Engineer", "company": "C",
                                          "log": str(root / "logs" / "a.log"), "screenshot": str(shown), "app_id": 1}}
    html = make_client(runs=run).get("/").get_data(as_text=True)
    assert "View log" in html and "Last screenshot" in html
    for href in re.findall(r'href="(/(?:log|evidence)\?[^"]*)"', html):
        assert ":" not in href.split("=", 1)[1].replace("%3A", ":").replace("%5C", "\\")[:3], href
        assert "Users" not in href and "%5C" not in href and "\\" not in href, href


def test_a_relative_artifact_reference_resolves_inside_the_project_only():
    assert web_ui._artifact_path("logs/a.log") == (web_ui.BASE_DIR / "logs" / "a.log").resolve()
    assert web_ui._artifact_ref(web_ui.BASE_DIR / "logs" / "a.log") == "logs/a.log"
    assert web_ui._artifact_ref("C:/elsewhere/secret.log") == ""         # outside the project: no reference at all


# -- what to do / screenshot -------------------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["submitted", "skipped", "DISQUALIFIED_POLICY_MISMATCH", "rejected", "offer"])
def test_finished_applications_are_not_told_what_to_do(make_client, status):
    html = make_client(statuses=(status,)).get("/application/1").get_data(as_text=True)
    assert "What to do" not in html


def test_an_application_waiting_for_the_owner_is_still_told_what_to_do(make_client):
    html = make_client(statuses=("needs_user_review",)).get("/application/1").get_data(as_text=True)
    assert "What to do" in html


def test_the_screenshot_is_a_fixed_height_thumbnail():
    css = ui_shell.BASE_CSS
    block = re.search(r"\.shot\s*\{[^}]*\}", css).group(0) + re.search(r"\.shot img\s*\{[^}]*\}", css).group(0)
    assert re.search(r"max-height:\s*4\d\dpx|height:\s*4\d\dpx", block)
    assert "object-fit:cover" in block.replace(" ", "") and "object-position:top" in block.replace(" ", "")


# -- scores and "Needs you" --------------------------------------------------------------------------------------

def test_the_match_percentage_says_what_it_measures(make_client, monkeypatch):
    job = dict(id=1, company_name="Acme", job_title="Engineer", url="https://x.example/1", location="Austin",
               is_remote=False, ats_type="greenhouse", match_score=65, matched_skills=["Title"],
               discovered_at="2026-11-01T09:00:00")
    import job_discovery
    monkeypatch.setattr(job_discovery.JobDiscoveryEngine, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(job_discovery.JobDiscoveryEngine, "get_discovered_jobs", lambda self, **k: [job])
    html = make_client().get("/").get_data(as_text=True)
    assert "Title match" in html


def test_needs_you_is_quiet_at_zero_and_loud_above_zero(make_client):
    quiet = make_client(statuses=("submitted",)).get("/").get_data(as_text=True)
    loud = make_client(statuses=("submitted", "needs_user_review")).get("/").get_data(as_text=True)
    chip = lambda h: re.search(r'<button[^>]*data-filter="needs"[^>]*>', h).group(0)
    assert "is-zero" in chip(quiet) and "is-zero" not in chip(loud)
    assert "has-items" in chip(loud)
    tab = lambda h: re.search(r'<a href="/"[^>]*>Applications</a>', h).group(0)
    assert "data-count" not in tab(quiet)
    assert 'data-count="1"' in tab(loud) and "1 need you" in tab(loud)
