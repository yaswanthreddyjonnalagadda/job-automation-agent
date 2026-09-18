"""The session, rather than the paid API, works out what to answer.

The agent writes the page into data/_ask_page.json and waits; whatever is
written back into data/_plan_page.json is the plan it carries out.
"""
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import page_agent
import session_planner


def make_planner(tmp_path, timeout=10, fallback=None):
    return session_planner.SessionPlanner(SimpleNamespace(), folder=tmp_path,
                                          fallback=fallback, timeout_seconds=timeout)


def answer_when_asked(planner, plan: dict, delay: float = 0.1) -> threading.Thread:
    """Stands in for the session: waits for the question, writes the plan."""
    folder = planner._folder

    def reply():
        ask = folder / planner.ASK_JSON
        for _ in range(100):
            if ask.exists():
                break
            time.sleep(0.05)
        time.sleep(delay)
        (folder / planner.PLAN_JSON).write_text(
            json.dumps(plan), encoding="utf-8")
    thread = threading.Thread(target=reply, daemon=True)
    thread.start()
    return thread


def test_the_page_is_handed_over_and_the_plan_comes_back(tmp_path):
    planner = make_planner(tmp_path)
    planner.now_applying = "R+L Carriers -- Network Engineer"
    plan = {"page_kind": "application_form",
            "answers": [{"ref": "e5321", "question": "Current Job", "action": "check",
                         "value": "Yes", "source": "resume"}],
            "next": {"ref": "e5403", "label": "Add Experience", "kind": "next_step"}}
    answer_when_asked(planner, plan)

    got = planner.plan_page("- checkbox \"Current Job\"", {"profile": {"first_name": "Yaswanth Reddy"}},
                            feedback="the end date is required")

    assert got == plan
    as_used = page_agent.PagePlan.from_json(got)       # the agent can act on it unchanged
    assert as_used.answers[0].ref == "e5321"
    assert as_used.next_label == "Add Experience"


def test_the_question_says_what_is_being_applied_for(tmp_path):
    planner = make_planner(tmp_path)
    planner.now_applying = "R+L Carriers -- Network Engineer"
    seen = {}

    def look_then_answer():
        ask = tmp_path / planner.ASK_JSON
        for _ in range(100):
            if ask.exists():
                seen.update(json.loads(ask.read_text(encoding="utf-8")))
                break
            time.sleep(0.05)
        (tmp_path / planner.PLAN_JSON).write_text("{}", encoding="utf-8")

    threading.Thread(target=look_then_answer, daemon=True).start()
    planner.plan_page("- textbox \"Employer Name\"", {"profile": {}}, feedback="")

    assert seen["job"] == "R+L Carriers -- Network Engineer"
    assert seen["feedback"] == ""
    assert Path(seen["page_file"]).name == planner.ASK_PAGE
    assert seen["facts"] == {"profile": {}}


def test_the_page_itself_is_written_where_the_session_can_read_it(tmp_path):
    planner = make_planner(tmp_path)
    answer_when_asked(planner, {"page_kind": "other"}, delay=0.3)
    written = {}

    def watch():
        page = tmp_path / planner.ASK_PAGE
        for _ in range(100):
            if page.exists():
                written["text"] = page.read_text(encoding="utf-8")
                return
            time.sleep(0.02)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    planner.plan_page("- heading \"Work history\"", {})
    watcher.join(timeout=2)
    assert written.get("text") == '- heading "Work history"'


def test_nothing_is_left_behind_once_it_is_answered(tmp_path):
    planner = make_planner(tmp_path)
    answer_when_asked(planner, {"page_kind": "other"})
    planner.plan_page("- heading \"Anything\"", {})
    assert not (tmp_path / planner.ASK_JSON).exists()
    assert not (tmp_path / planner.PLAN_JSON).exists()


def test_a_half_written_plan_is_not_read(tmp_path):
    """A plan caught mid-write is waited on rather than read as broken."""
    planner = make_planner(tmp_path, timeout=6)
    plan_file = tmp_path / planner.PLAN_JSON

    def write_in_two_halves():
        ask = tmp_path / planner.ASK_JSON
        for _ in range(100):
            if ask.exists():
                break
            time.sleep(0.05)
        plan_file.write_text('{"page_kind": "applicat', encoding="utf-8")   # cut off
        time.sleep(2.5)
        plan_file.write_text('{"page_kind": "application_form"}', encoding="utf-8")

    threading.Thread(target=write_in_two_halves, daemon=True).start()
    assert planner.plan_page("- heading \"Anything\"", {})["page_kind"] == "application_form"


def test_the_run_is_told_when_no_answer_comes(tmp_path):
    planner = make_planner(tmp_path, timeout=3)
    with pytest.raises(TimeoutError) as stopped:
        planner.plan_page("- heading \"Nobody home\"", {})
    assert "did not answer" in str(stopped.value)


def test_a_stale_plan_from_last_time_is_never_used(tmp_path):
    """The plan left over from the previous page must not be taken for this one."""
    planner = make_planner(tmp_path, timeout=4)
    (tmp_path / planner.PLAN_JSON).write_text(
        json.dumps({"page_kind": "yesterday"}), encoding="utf-8")
    answer_when_asked(planner, {"page_kind": "today"}, delay=0.4)
    assert planner.plan_page("- heading \"Today\"", {})["page_kind"] == "today"


def test_what_the_session_does_not_do_still_goes_to_the_api(tmp_path):
    """Cover letters and resumes are not part of this; they carry on as before."""
    fallback = SimpleNamespace(generate_cover_letter=lambda *a, **k: "Dear hiring manager",
                               tailor_resume=lambda *a, **k: "resume")
    planner = make_planner(tmp_path, fallback=fallback)
    assert planner.generate_cover_letter() == "Dear hiring manager"
    assert planner.tailor_resume() == "resume"


def test_a_run_given_its_own_planner_keeps_it(monkeypatch, tmp_path):
    """Switching AGENT_BRAIN must not seize a planner handed in on purpose --
    a test's stub, or anything else a caller chose."""
    monkeypatch.setenv("AGENT_BRAIN", "session")
    stub = SimpleNamespace(plan_page=lambda *a, **k: {"page_kind": "other"})
    agent = page_agent.PageAgent.__new__(page_agent.PageAgent)
    agent.claude, agent.config, agent.job = stub, SimpleNamespace(), SimpleNamespace()
    agent.follow_the_chosen_brain()
    assert agent.claude is stub


def test_a_real_run_moves_onto_the_session_and_back(monkeypatch, tmp_path):
    """The owner changes AGENT_BRAIN and the running application follows, with
    the API client kept underneath for what the session does not do."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    class ClaudeClient:                      # only its name matters here
        def plan_page(self, *a, **k):
            return {}

    api = ClaudeClient()
    agent = page_agent.PageAgent.__new__(page_agent.PageAgent)
    agent.claude, agent.config = api, SimpleNamespace()
    agent.job = SimpleNamespace(company="R+L Carriers", title="Network Engineer")

    monkeypatch.setenv("AGENT_BRAIN", "session")
    agent.follow_the_chosen_brain()
    assert type(agent.claude).__name__ == "SessionPlanner"
    assert agent.claude.now_applying == "R+L Carriers -- Network Engineer"

    monkeypatch.setenv("AGENT_BRAIN", "api")
    agent.follow_the_chosen_brain()
    assert agent.claude is api


def test_two_applications_at_once_do_not_take_each_other_s_answers(tmp_path):
    """Both runs asked in the same file and the first to look took the other's
    plan: one application sat waiting for an answer that had already gone."""
    first = make_planner(tmp_path)
    first.now_applying = "R+L Carriers -- Network Engineer"
    second = make_planner(tmp_path)
    second.now_applying = "The Options Clearing Corporation -- Cloud Engineering"

    assert first.ASK_JSON != second.ASK_JSON
    assert first.PLAN_JSON != second.PLAN_JSON

    answer_when_asked(second, {"page_kind": "for the second"})
    assert second.plan_page("- heading \"Second\"", {})["page_kind"] == "for the second"
    assert not (tmp_path / first.ASK_JSON).exists()      # the first was never touched
