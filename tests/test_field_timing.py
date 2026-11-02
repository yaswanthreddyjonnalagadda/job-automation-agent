"""Where an application's time goes is recorded for every field, list search, model call and page read (f303).

The owner, 2 November 2026: track the time taken to fill each field so it is known where an application is slow. Every
step is one line in data/field_timings.jsonl (run, kind, field label, seconds, result) -- never an answer or a value.
"""
import json
import sys
from pathlib import Path

import pytest

import field_timing
from claude_integration import ClaudeClient
from page_agent import PageAgent
from sites.workday import WorkdayAdapter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import field_timings  # noqa: E402


@pytest.fixture()
def lines(tmp_path, monkeypatch):
    target = tmp_path / "timings.jsonl"
    monkeypatch.setenv("JAA_FIELD_TIMINGS", str(target))
    field_timing.begin("Verizon", "Network Cyber Security Engineer")
    return lambda: [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]


def test_a_timed_step_records_run_kind_label_seconds_and_result(lines):
    @field_timing.timed("field", lambda name: f"fill: {name}")
    def work(name):
        return True
    work("Email Address")
    (row,) = lines()
    assert row["run"] == "Verizon | Network Cyber Security Engineer"
    assert (row["kind"], row["label"], row["result"]) == ("field", "fill: Email Address", "ok")
    assert row["seconds"] >= 0 and row["at"]


def test_a_step_that_returns_false_is_failed_and_one_that_raises_is_error_and_still_raises(lines):
    @field_timing.timed("search", lambda: "--fieldOfStudy[0]")
    def not_found():
        return False

    @field_timing.timed("search", lambda: "--school[0]")
    def breaks():
        raise RuntimeError("page closed")
    assert not_found() is False
    with pytest.raises(RuntimeError):
        breaks()
    assert [row["result"] for row in lines()] == ["failed", "error"]


def test_a_value_is_never_written_only_the_label(lines):
    @field_timing.timed("field", lambda question, value: f"choose: {question}")
    def answer(question, value):
        return True
    answer("Gender", "a secret value")
    assert "secret" not in Path(field_timing.path()).read_text(encoding="utf-8")


def test_timing_can_never_stop_an_application(tmp_path, monkeypatch):
    monkeypatch.setenv("JAA_FIELD_TIMINGS", str(tmp_path))          # a folder: opening it as a file fails
    field_timing.record("field", "anything", 1.0)

    @field_timing.timed("field", lambda: 1 / 0)                       # a label that fails to build
    def work():
        return "done"
    assert work() == "done"


@pytest.mark.parametrize("method", [
    PageAgent.do, PageAgent.read_when_loaded, ClaudeClient._call,
    WorkdayAdapter.fill_by_id_suffix, WorkdayAdapter.fill_date_spinner,
    WorkdayAdapter.select_from_searchable_input, WorkdayAdapter.select_from_button_dropdown,
], ids=lambda m: m.__qualname__)
def test_every_fill_search_model_and_read_path_is_timed(method):
    assert hasattr(method, "__wrapped__"), f"{method.__qualname__} is not timed"


def test_the_report_shows_the_slowest_steps_of_the_last_run(lines):
    field_timing.record("search", "--fieldOfStudy[0]", 41.2, "failed")
    field_timing.record("field", "fill: First Name", 0.8)
    field_timing.record("model", "Gemini", 66.0)
    field_timing.begin("Next", "Other job")
    field_timing.record("field", "fill: Phone", 0.5)
    rows = field_timings.load(field_timing.path())
    assert [r["label"] for r in field_timings.last_run(rows)] == ["fill: Phone"]
    text = field_timings.report(field_timings.last_run(rows, "verizon"))
    assert text.index("Gemini") < text.index("--fieldOfStudy[0]") < text.index("First Name")
    assert "model" in text and "failed" in text
    assert "No timings" in field_timings.report([])
