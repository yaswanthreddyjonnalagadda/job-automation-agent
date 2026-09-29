"""An answer the owner gives while the run waits for them is kept at once, not only for that run.

Answers were learned at the Review page, from what that page shows; a question answered on step 3 during a pause
is not on it, so it was kept only in the run's memory and came back on the next form (29 September).
"""
import json
from types import SimpleNamespace

import pytest

import config
import page_agent
import profile_setup


class Tracker:
    def __init__(self):
        self.recorded = []

    def record_answer(self, key, host, question, answer, answered_by):
        self.recorded.append((question, answer, answered_by))


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_setup, "ANSWERS_PATH", tmp_path / "profile_answers.json")
    job = SimpleNamespace(title="Engineer", company="Acme Corp", url="https://acme.wd1.myworkdayjobs.com/apply")
    a = page_agent.PageAgent(SimpleNamespace(values=None), SimpleNamespace(),
                             SimpleNamespace(auto_submit=False, ats_email=""),
                             config.UserProfile(email="jane@example.com"), SimpleNamespace(raw_text="x"), job,
                             resume_file=None, tracker=Tracker(), key="k")
    a.tab = lambda page: SimpleNamespace(url=job.url)
    return a


def pause_then_continue(agent, before: dict, after: dict):
    """The page as the agent left it at the pause, then as the owner left it when they pressed Continue."""
    agent._paused_state = dict(before)
    snapshot = "\n".join(f'- textbox "{q}" [ref=e{i}]: {v}' for i, (q, v) in enumerate(after.items()))
    agent.snapshot = lambda page: snapshot
    agent.note_owner_changes(page=None)


def saved(tmp_path):
    path = tmp_path / "profile_answers.json"
    return json.loads(path.read_text()) if path.exists() else {}


def test_a_general_answer_given_in_a_pause_is_saved_for_every_form(agent, tmp_path):
    pause_then_continue(agent, {"Are you willing to work night shifts?": ""},
                        {"Are you willing to work night shifts?": "Yes"})
    assert saved(tmp_path) == {"Are you willing to work night shifts?": "Yes"}
    assert agent.tracker.recorded == [("Are you willing to work night shifts?", "Yes", "user")]
    assert agent.owner_answers == {"Are you willing to work night shifts?": "Yes"}   # and not overwritten this run


def test_an_answer_about_this_employer_is_kept_for_this_employer_only(agent, tmp_path):
    pause_then_continue(agent, {"Were you referred by an Acme employee?": ""},
                        {"Were you referred by an Acme employee?": "No"})
    assert saved(tmp_path) == {}
    assert agent.tracker.recorded == [("Were you referred by an Acme employee?", "No", "user")]


@pytest.mark.parametrize("question", ["Do you require H-1B visa sponsorship?",
                                      "I certify that the information above is true and complete"])
def test_a_legal_or_signed_answer_is_never_kept(agent, tmp_path, question):
    pause_then_continue(agent, {question: ""}, {question: "Yes"})
    assert saved(tmp_path) == {} and agent.tracker.recorded == []


def test_what_the_owner_did_not_change_is_not_taken_for_their_answer(agent, tmp_path):
    pause_then_continue(agent, {"Notice period": "2 weeks"}, {"Notice period": "2 weeks"})
    assert saved(tmp_path) == {} and agent.tracker.recorded == []


def test_a_question_answered_while_applying_leaves_the_waiting_list(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_setup, "ANSWERS_PATH", tmp_path / "profile_answers.json")
    monkeypatch.setattr(profile_setup, "UNANSWERED_PATH", tmp_path / "unanswered.json")
    (tmp_path / "unanswered.json").write_text(json.dumps({"a": {
        "question": "Are you willing to work night shifts? *", "answer_key": "re:willing\\s+to\\s+work\\s+night",
        "times": 2}}))
    assert len(profile_setup.waiting_questions()) == 1
    profile_setup.remember_answer("Are you willing to work night shifts?", "Yes")
    assert profile_setup.waiting_questions() == []
