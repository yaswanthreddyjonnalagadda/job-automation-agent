"""Reliability is measured run by run, by job site and by code version (review of 1 October, item 7)."""
from types import SimpleNamespace

import pytest

import run_metrics


@pytest.fixture(autouse=True)
def metrics_file(tmp_path, monkeypatch):
    monkeypatch.setattr(run_metrics, "FILE", tmp_path / "run_metrics.jsonl")
    monkeypatch.setattr(run_metrics, "_run", None)
    yield
    run_metrics._run = None


def a_run(url, code, stops=(), review=True, ai=(True, True), resumed=False, verdict=""):
    run_metrics.start("k", url, code, resumed=resumed)
    for ok in ai:
        run_metrics.ai_call("Gemini", 1.5, ok)
    for reason in stops:
        run_metrics.owner_stop(reason)
    if verdict:
        run_metrics.resume(verdict)
    run_metrics.outcome("review" if review else "needs_you")
    run_metrics.finish()


def test_a_run_is_written_once_with_its_numbers():
    a_run("https://acme.wd1.myworkdayjobs.com/x", "abc123", stops=["needs your answer: Degree | more"])
    (row,) = run_metrics.load()
    assert row["site"] == "Workday" and row["code"] == "abc123" and row["reached_review"] is True
    assert row["owner_stops"] == 1 and row["stop_reasons"] == ["needs your answer: Degree"]
    assert row["ai_calls"] == 2 and row["ai_failures"] == 0


def test_runs_are_grouped_by_site_and_by_code_version():
    a_run("https://acme.wd1.myworkdayjobs.com/x", "v1")
    a_run("https://acme.wd1.myworkdayjobs.com/y", "v2", stops=["a CAPTCHA is showing"])
    a_run("https://boards.greenhouse.io/acme/1", "v2", review=False, stops=["could not read this page"],
          ai=(False,))
    summary = run_metrics.summary()
    assert summary["all"]["runs"] == 3 and summary["all"]["without_you"] == 1
    assert summary["by_site"]["Workday"]["rate_review"] == "100%"
    assert summary["by_site"]["Greenhouse"]["rate_review"] == "0%"
    assert summary["by_code"]["v2"]["runs"] == 2 and summary["by_code"]["v1"]["rate_without_you"] == "100%"
    assert summary["all"]["ai_failure_rate"] == "20%"


def test_resumes_are_counted_by_whether_they_found_the_application():
    a_run("https://acme.wd1.myworkdayjobs.com/x", "v1", resumed=True, verdict="same_application")
    a_run("https://acme.wd1.myworkdayjobs.com/x", "v1", resumed=True, verdict="different")
    s = run_metrics.summary()["all"]
    assert s["resumes"] == 2 and s["resumes_on_the_application"] == 1


def test_a_stop_is_saved_at_once_so_a_killed_run_still_counts():
    run_metrics.start("k", "https://jobs.lever.co/acme/1", "v1")
    run_metrics.owner_stop("needs your answer: Salary")
    assert run_metrics.load()[0]["owner_stops"] == 1          # written before the run ends


def test_nothing_is_recorded_outside_a_run():
    run_metrics.ai_call("Gemini", 1, True)
    run_metrics.owner_stop("x")
    assert run_metrics.load() == []


def test_the_progress_page_shows_the_numbers(monkeypatch):
    import profile_setup
    import web_ui
    a_run("https://acme.wd1.myworkdayjobs.com/x", "v9")
    monkeypatch.setattr(profile_setup, "needs_setup", lambda: False)
    monkeypatch.setattr(web_ui, "get_tracker", lambda: SimpleNamespace(list_all=lambda: []))
    web_ui.app.config["TESTING"] = True
    page = web_ui.app.test_client().get("/progress").get_data(as_text=True)
    assert "Reached Review without you" in page and "v9" in page and "Workday" in page
