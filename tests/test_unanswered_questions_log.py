"""The questions the agent cannot answer are kept, once each, in a file the owner can read.

Each required question the agent leaves for the owner -- "Have you ever been fired or asked to
resign from a job?" had no answer in the profile -- is written to data/unanswered_questions.json
with why it was left, the choices the form offered, and where it was met. The owner's answer goes
into data/profile_answers.json (the answer library the agent already reads); once it is there
the entry shows it. Both are plain JSON, so the whole set travels to the next project.
"""
import json
import re
from types import SimpleNamespace

import pytest

import config
import page_agent
import safety
from browser_automation import JobApplicationAssistant

QUESTION = "Have you ever been fired or asked to resign from a job?"


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    pg = context.new_page()
    yield pg
    context.close()


def make_agent(tmp_path, company="Willdan", library=None):
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    cfg = SimpleNamespace(auto_submit=False, ats_email="")
    job = SimpleNamespace(title="Cloud Engineer", company=company, url="https://workforcenow.example.com/apply")
    agent = page_agent.PageAgent(assistant, SimpleNamespace(), cfg, config.UserProfile(email="o@example.com"),
                                 SimpleNamespace(raw_text="x"), job, resume_file=None)
    agent.unanswered_path = tmp_path / "unanswered_questions.json"
    agent._current_host = "workforcenow.example.com"
    agent._profile_answer_library = library if library is not None else {}
    return agent


def form(page):
    page.set_content(f'<label for="q">{QUESTION}*</label><select id="q"><option value="">Choose</option>'
                     "<option>Yes</option><option>No</option></select>")


def leave(agent, page, question=QUESTION + "*", reason="No fact in the profile or resume addresses this.",
          required=True):
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    plan = page_agent.PagePlan(for_owner=[{"question": question, "reason": reason, "required": required}])
    return agent.blockers(page, plan, controls, about_to_send=False)


def logged(agent):
    return json.loads(agent.unanswered_path.read_text(encoding="utf-8"))


def test_a_required_question_left_for_the_owner_is_kept_with_why_and_where(page, tmp_path):
    form(page)
    agent = make_agent(tmp_path)
    leave(agent, page)
    (entry,) = logged(agent).values()
    assert entry["question"] == QUESTION                      # no asterisk, as the form words it
    assert entry["required"] is True
    assert "No fact in the profile" in entry["reason"]
    assert entry["options"] == ["Yes", "No"]                  # what the form offered, so the answer can be checked
    assert entry["sites"] == ["workforcenow.example.com"] and entry["companies"] == ["Willdan"]
    assert entry["times"] == 1 and entry["first_seen"] and entry["last_seen"]
    assert entry["answer"] is None                            # nobody has answered it yet
    # ... and how to answer it: the line to add to the answer library
    assert re.search(entry["answer_key"][3:], QUESTION, re.IGNORECASE) and entry["answer_key"].startswith("re:")


def test_the_same_question_is_one_entry_that_counts_and_remembers_every_site(page, tmp_path):
    form(page)
    agent = make_agent(tmp_path)
    leave(agent, page)
    other = make_agent(tmp_path, company="Alloy")
    other._current_host = "job-boards.greenhouse.io"
    leave(other, page, question=QUESTION.replace("?", " ") + "*")     # another form's wording of it
    (entry,) = logged(agent).values()
    assert entry["times"] == 2
    assert entry["sites"] == ["workforcenow.example.com", "job-boards.greenhouse.io"]
    assert entry["companies"] == ["Willdan", "Alloy"]


def test_a_question_that_is_not_required_is_not_kept(page, tmp_path):
    form(page)
    agent = make_agent(tmp_path)
    leave(agent, page, question=QUESTION, required=False)          # no asterisk, not required
    assert not agent.unanswered_path.exists()


def test_an_answered_question_shows_its_answer_and_is_not_asked_again(page, tmp_path):
    form(page)
    agent = make_agent(tmp_path)
    leave(agent, page)
    (entry,) = logged(agent).values()
    # the owner adds the answer to the library, as the entry says
    agent._profile_answer_library = {entry["answer_key"]: "No"}
    leave(agent, page)
    (entry,) = logged(agent).values()
    assert entry["answer"] == "No"


def test_nothing_personal_is_written_into_the_reason(page, tmp_path):
    form(page)
    agent = make_agent(tmp_path)
    leave(agent, page, reason="profile says jane.doe@example.com and (571) 354-5212")
    text = agent.unanswered_path.read_text(encoding="utf-8")
    assert "jane.doe@example.com" not in text and "354-5212" not in text


@pytest.mark.parametrize("content", ["", "not json", "[1, 2]", '{"broken": '])
def test_a_damaged_file_never_stops_the_run_and_is_replaced(page, tmp_path, content):
    form(page)
    agent = make_agent(tmp_path)
    agent.unanswered_path.write_text(content, encoding="utf-8")
    leave(agent, page)
    (entry,) = logged(agent).values()
    assert entry["question"] == QUESTION


def test_a_file_that_cannot_be_written_never_stops_the_run(page, tmp_path):
    form(page)
    agent = make_agent(tmp_path)
    agent.unanswered_path = tmp_path / "missing_folder" / "deeper" / "x.json"
    agent.unanswered_path.parent.parent.write_text("a file where a folder should be", encoding="utf-8")
    leave(agent, page)                                            # must not raise
