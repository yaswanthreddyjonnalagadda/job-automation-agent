from types import SimpleNamespace

import pytest

import page_agent
import safety
from job_tracker import JobTracker


def make_tracker(tmp_path):
    tracker = JobTracker(tmp_path / "applications.db")
    tracker.create(
        dedup_key="learned-task-test",
        title="Test role",
        company="Test company",
        location="",
        source_site="",
        url="https://jobs.example.test/apply",
    )
    return tracker


def test_sqlite_recipe_is_recalled_only_for_the_same_control_signature(tmp_path):
    tracker = make_tracker(tmp_path)
    tracker.record_form_recipe(
        "jobs.example.test", "state-question", "combobox", "options-v1",
        "choose", "type_and_commit",
    )

    recipe = tracker.recall_form_recipe(
        "jobs.example.test", "state-question", "combobox", "options-v1", "choose"
    )
    assert recipe and recipe["method"] == "type_and_commit"
    assert tracker.recall_form_recipe(
        "jobs.example.test", "state-question", "combobox", "options-v2", "choose"
    ) is None


def test_failed_recipe_is_disabled_until_a_new_verified_success(tmp_path):
    tracker = make_tracker(tmp_path)
    args = ("jobs.example.test", "address-question", "combobox", "options-v1", "choose")
    tracker.record_form_recipe(*args, "type_and_commit")
    assert tracker.recall_form_recipe(*args)

    tracker.mark_form_recipe_failed(*args)
    assert tracker.recall_form_recipe(*args) is None

    tracker.record_form_recipe(*args, "type_and_commit")
    assert tracker.recall_form_recipe(*args)


class RecipeTracker:
    def __init__(self, recipe=None, prior_answers=()):
        self.recipe = recipe
        self.prior_answers = list(prior_answers)
        self.recorded = []
        self.failed = []

    def recall_form_recipe(self, *identity):
        return self.recipe

    def record_form_recipe(self, *recipe):
        self.recorded.append(recipe)

    def mark_form_recipe_failed(self, *identity):
        self.failed.append(identity)

    def recall_answer(self, question, limit=3):
        return list(self.prior_answers)


def make_page_agent(tracker, profile=None):
    def best_option(options, wanted):
        wanted_value = str(wanted[0]).casefold()
        return next((index for index, option in enumerate(options)
                     if str(option).casefold() == wanted_value), None)

    assistant = SimpleNamespace(values=safety.AgentValues(), _best_option=best_option)
    job = SimpleNamespace(url="https://jobs.example.test/apply", title="Test role", company="Test company")
    return page_agent.PageAgent(
        assistant, None, SimpleNamespace(), profile or SimpleNamespace(state="Virginia"),
        SimpleNamespace(raw_text=""), job, tracker=tracker, key="learned-task-test",
    )


def test_page_agent_reuses_a_verified_keyboard_recipe_only_after_readback():
    tracker = RecipeTracker(recipe={"method": "type_and_commit"})
    agent = make_page_agent(tracker)
    control = page_agent.Control(
        ref="state", role="combobox", name="State", options=["Virginia", "Texas"],
    )
    answer = page_agent.Answer("state", "State", "choose", "Virginia", "profile.state")
    calls = []
    agent.type_and_commit = lambda page, found_control, value: calls.append((found_control, value)) or True
    agent.choose = lambda *args: pytest.fail("the stored keyboard recipe should run before the generic chooser")

    assert agent.do(SimpleNamespace(url="https://jobs.example.test/apply"), answer, control)
    assert calls == [(control, "Virginia")]
    agent._remember(control, answer)
    assert tracker.recorded == []

    after = [page_agent.Control(ref="state", role="combobox", name="State", value="Virginia",
                                options=["Virginia", "Texas"])]
    agent._commit_learned_memory(after)
    assert tracker.recorded and tracker.recorded[0][-1] == "type_and_commit"


def test_profile_answer_overrides_a_learned_user_answer():
    tracker = RecipeTracker(prior_answers=[{
        "host": "jobs.example.test", "question": "State", "answer": "Texas", "answered_by": "user",
    }])
    agent = make_page_agent(tracker, profile=SimpleNamespace(state="Virginia"))
    control = page_agent.Control(ref="state", role="combobox", name="State", options=["Virginia", "Texas"])

    assert agent.known_answer(control) == ("Virginia", "profile.state")


def test_sensitive_questions_never_produce_a_reusable_recipe():
    agent = make_page_agent(RecipeTracker())
    control = page_agent.Control(ref="visa", role="combobox", name="What is your visa status?")
    answer = page_agent.Answer("visa", control.question, "choose", "H-1B", "profile.answer_library")

    assert agent._recipe_identity(control, answer) is None