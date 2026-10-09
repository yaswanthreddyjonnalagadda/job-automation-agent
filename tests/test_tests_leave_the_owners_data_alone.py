"""No test reads or writes the owner's own records in data/ -- by whatever path a module names them.

29 September: a test typed 'First Name = Yash R.' into a form, the learning step saved it, and it landed in the
owner's real data/profile_answers.json, because that file was named by its full path (profile_setup.ANSWERS_PATH)
and the test setup redirected only paths written 'data/...'. The agent then read it back through a path of its
own, and every application would have used it.
"""
import hashlib
from pathlib import Path

import config
import profile_setup

REAL_DATA = (Path(__file__).resolve().parents[1] / "data").resolve()


def test_the_saved_answers_and_the_tracker_database_are_the_tests_own():
    for path in (profile_setup.ANSWERS_PATH, profile_setup.UNANSWERED_PATH, config.DB_PATH):
        assert Path(path).parent != REAL_DATA, path


def test_the_owners_profile_stays_readable_for_the_tests_that_need_it():
    assert Path(config.PROFILE_PATH).parent == REAL_DATA


def test_learning_an_answer_in_a_test_leaves_the_real_file_as_it_was():
    real = REAL_DATA / "profile_answers.json"
    before = hashlib.sha256(real.read_bytes()).hexdigest() if real.exists() else None
    profile_setup.remember_answer("Are you willing to work night shifts?", "Yes")
    after = hashlib.sha256(real.read_bytes()).hexdigest() if real.exists() else None
    assert before == after


def test_the_agent_reads_the_saved_answers_where_setup_writes_them():
    import page_agent
    from types import SimpleNamespace
    agent = page_agent.PageAgent(SimpleNamespace(values=None), SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email=""),
                                 config.UserProfile(email="jane@example.com"), SimpleNamespace(raw_text="x"),
                                 SimpleNamespace(title="t", company="c", url="u"), resume_file=None)
    profile_setup.save_answers({"Preferred shift?": "Day"})
    assert agent._answer_library() == {"Preferred shift?": "Day"}
