"""The application's questions as the job site publishes them, known before the form is opened.

Greenhouse's public job board API lists every question of an application -- its words, whether it is required,
and every choice a list offers (boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}?questions=true). Its forms
draw many lists only when clicked, and a list the agent never saw left it nothing to match an answer to (f007,
f021). Now the published choices come with the job, and a list that shows none is answered from them.
"""
from types import SimpleNamespace

import pytest

import config
import job_sources
import page_agent
from page_agent import Control, published_choices

# The shape Greenhouse's boards API returns, trimmed to what matters.
GREENHOUSE = {
    "title": "Network Engineer", "company_name": "Acme", "location": {"name": "Remote"},
    "absolute_url": "https://boards.greenhouse.io/acme/jobs/123", "content": "&lt;p&gt;Build networks.&lt;/p&gt;",
    "questions": [
        {"label": "Resume/CV", "required": True, "fields": [{"name": "resume", "type": "input_file"},
                                                            {"name": "resume_text", "type": "textarea"}]},
        {"label": "How did you hear about this job?", "required": True,
         "fields": [{"name": "question_1", "type": "multi_value_single_select",
                     "values": [{"label": "LinkedIn", "value": 1}, {"label": "Referral", "value": 2},
                                {"label": "Company Website", "value": 3}]}]},
        {"label": "LinkedIn Profile", "required": False, "fields": [{"name": "question_2", "type": "input_text"}]},
        {"label": "", "required": False, "fields": [{"name": "empty", "type": "input_text"}]},
    ],
    "compliance": [{"type": "eeoc", "questions": [
        {"label": "Veteran Status", "required": False,
         "fields": [{"name": "veteran_status_id", "type": "multi_value_single_select",
                     "values": [{"label": "I am not a protected veteran", "value": 1},
                                {"label": "I identify as one or more of the classifications of protected veteran",
                                 "value": 2}, {"label": "I don't wish to answer", "value": 3}]}]}]}],
}


def test_the_questions_are_read_with_their_choices():
    questions = job_sources.greenhouse_questions(GREENHOUSE)
    assert [q["question"] for q in questions] == ["Resume/CV", "How did you hear about this job?",
                                                  "LinkedIn Profile", "Veteran Status"]
    heard = questions[1]
    assert heard["required"] and heard["kind"] == "multi_value_single_select"
    assert heard["options"] == ["LinkedIn", "Referral", "Company Website"]
    assert questions[0]["kind"] == "textarea" and questions[0]["options"] == []
    assert questions[3]["options"][0] == "I am not a protected veteran"


def test_a_greenhouse_job_comes_with_its_questions(monkeypatch):
    asked = []

    def get(url, timeout):
        asked.append(url)
        return SimpleNamespace(status_code=200, json=lambda: GREENHOUSE)
    monkeypatch.setattr(job_sources.requests, "get", get)
    job = job_sources.fetch_greenhouse_job("https://boards.greenhouse.io/acme/jobs/123")
    assert "questions=true" in asked[0]
    assert job["raw_text"] == "Build networks." and len(job["questions"]) == 4


@pytest.mark.parametrize("on_the_page", ["How did you hear about this job?", "How did you hear about this job? *",
                                         "how did you hear about this job"])
def test_the_published_choices_are_found_however_the_page_punctuates_the_question(on_the_page):
    published = job_sources.greenhouse_questions(GREENHOUSE)
    assert published_choices(on_the_page, published) == ["LinkedIn", "Referral", "Company Website"]


@pytest.mark.parametrize("question", ["LinkedIn Profile", "Something else entirely", ""])
def test_a_question_without_published_choices_gets_none(question):
    assert published_choices(question, job_sources.greenhouse_questions(GREENHOUSE)) == []


def test_a_list_that_shows_no_choices_is_answered_from_the_published_ones(tmp_path):
    job = SimpleNamespace(title="Network Engineer", company="Acme", url="https://boards.greenhouse.io/acme/jobs/123",
                          analysis={"published_questions": job_sources.greenhouse_questions(GREENHOUSE)})
    profile = config.UserProfile(email="jane@example.com", how_did_you_hear="LinkedIn")
    agent = page_agent.PageAgent(SimpleNamespace(values=None), SimpleNamespace(),
                                 SimpleNamespace(auto_submit=False, ats_email=""), profile,
                                 SimpleNamespace(raw_text="x"), job, resume_file=None)
    seen = []
    agent.do = lambda page, answer, control: seen.append((answer.value, list(control.options))) or True
    agent.settle = lambda *a, **k: None
    agent.tab = lambda page: SimpleNamespace(wait_for_load_state=lambda *a, **k: None)
    hidden_list = Control(ref="e1", role="combobox", name="How did you hear about this job?")
    agent.answer_what_is_known(page=None, controls=[hidden_list], required=set())
    assert seen and seen[0][1] == ["LinkedIn", "Referral", "Company Website"]
    assert seen[0][0] == "LinkedIn"


def test_the_ai_is_given_the_published_questions_too():
    job = SimpleNamespace(title="t", company="c", url="u",
                          analysis={"published_questions": job_sources.greenhouse_questions(GREENHOUSE)})
    agent = page_agent.PageAgent(SimpleNamespace(values=None), SimpleNamespace(),
                                 SimpleNamespace(auto_submit=False, ats_email=""),
                                 config.UserProfile(email="jane@example.com"), SimpleNamespace(raw_text="x"), job,
                                 resume_file=None)
    assert len(agent.facts([])["questions_the_site_publishes"]) == 4
