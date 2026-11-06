"""A "which of these apply" group is put to the model once, with its boxes, as a pick-all-that-apply question (f296).

Fastly (Greenhouse), 1 November 2026: after the group was read as the question it really is, the agent asked the model
about it once per box -- eleven calls for one question -- with no list of the boxes, and the helper told the model to
"choose exactly one" of nothing. A tick-box group is several answers drawn from the boxes the page shows; the model has
to be given those boxes, asked for every one the resume supports, and its answer must name only boxes that exist.
"""
import json
import pathlib
import tempfile
from types import SimpleNamespace

import pytest

import config
import page_agent
import safety
from browser_automation import JobApplicationAssistant
from claude_integration import ClaudeClient

BOXES = ["Go", "Rust", "Perl", "Network infrastructure", "Terraform", "Kubernetes / Docker", "None of these"]
QUESTION = "With which of the following do you have professional experience? *"


# -- the model's side -------------------------------------------------------------------------------------------

def client_saying(reply: str):
    """A ClaudeClient whose _call always answers `reply`. Most of this file's fixtures predate the
    "grounded" field answer_single_question now requires before trusting an answer; when `reply`'s own
    JSON omits it, this adds grounded=true (as a real compliant model reply would already say), so this
    helper's job stays "which box names come back", not "did the model's self-report arrive"."""
    client = ClaudeClient.__new__(ClaudeClient)
    seen = []

    def call(system, user_message, max_tokens):
        seen.append((system, user_message))
        try:
            data = json.loads(reply)
        except ValueError:
            return reply
        if isinstance(data, dict) and "grounded" not in data:
            return json.dumps({**data, "grounded": True})
        return reply
    client._call = call
    client.seen = seen
    return client


def ask(client, **kw):
    return client.answer_single_question(QUESTION, options=BOXES, resume_text="Terraform and Kubernetes", several=True, **kw)


def test_the_model_is_told_to_pick_every_box_that_applies_from_the_boxes_shown():
    client = client_saying('{"answer": ["Terraform", "Kubernetes / Docker"]}')
    ask(client)
    system, user = client.seen[0]
    assert "every" in system.lower() and "exactly one" not in user.lower()
    assert all(box in user for box in BOXES), "the boxes were not given to the model"


@pytest.mark.parametrize("reply,expected", [
    ('{"answer": ["Terraform", "Kubernetes / Docker"]}', "Terraform; Kubernetes / Docker"),
    ('{"answer": "Terraform; Kubernetes / Docker"}', "Terraform; Kubernetes / Docker"),
    ('{"answer": "terraform, kubernetes / docker"}', "Terraform; Kubernetes / Docker"),
    ('{"answer": ["Terraform", "Cobol"]}', "Terraform"),                       # a box the page does not have is dropped
    ('{"answer": ["Cobol", "Fortran"]}', ""),                                  # nothing real: no answer, never a guess
    ('{"answer": ["Terraform", "None of these"]}', "Terraform"),               # "none" never goes with others
    ('{"answer": ["None of these"]}', "None of these"),
    ('{"answer": ""}', ""),
])
def test_the_answer_names_only_boxes_that_exist(reply, expected):
    assert ask(client_saying(reply)) == expected


def test_a_pick_one_question_is_still_asked_for_exactly_one():
    client = client_saying('{"answer": "Yes"}')
    assert client.answer_single_question("Are you over 18?", options=["Yes", "No"]) == "Yes"
    assert "exactly one" in client.seen[0][1].lower()


# -- the agent's side -------------------------------------------------------------------------------------------

PAGE = f"""<html><body>
<form>
  <div>Select...</div>
  <input role="combobox" aria-label="How much experience do you have?" aria-expanded="false">
  <div role="group" aria-label="{QUESTION}">
    {''.join(f'<label><input type="checkbox" name="b{n}"> {name}</label><br>' for n, name in enumerate(BOXES))}
  </div>
</form></body></html>"""


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


def run_agent(browser, answer):
    context = browser.new_context()
    page = context.new_page()
    page.set_content(PAGE)
    calls = []

    def answer_single_question(question, options=None, several=False, **kw):
        calls.append({"question": question, "options": list(options or []), "several": several})
        return answer if "which of the following" in question.lower() else ""
    claude = SimpleNamespace(
        plan_page=lambda snapshot, facts, feedback="": {"page_kind": "application_form", "answers": [], "next": {"kind": "none"}},
        answer_single_question=answer_single_question)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    resume = pathlib.Path(tempfile.mkdtemp()) / "Resume_Test.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    agent = page_agent.PageAgent(
        assistant, claude, SimpleNamespace(auto_submit=False, ats_email=""), config.get_user_profile(),
        SimpleNamespace(raw_text="Terraform, Kubernetes"),
        SimpleNamespace(title="Engineer", company="Example", url="https://jobs.example.com/apply/1"), resume_file=resume)
    agent.run(page)
    ticked = page.evaluate("[...document.querySelectorAll('input[type=checkbox]:checked')].map(b => b.parentElement.innerText.trim())")
    context.close()
    return calls, ticked


def test_the_group_is_asked_once_with_its_boxes_and_the_answer_ticks_them(browser):
    calls, ticked = run_agent(browser, "Terraform; Kubernetes / Docker")
    group_calls = [c for c in calls if "which of the following" in c["question"].lower()]
    assert len(group_calls) == 1, f"the group was put to the model {len(group_calls)} times"
    assert group_calls[0]["options"] == BOXES and group_calls[0]["several"] is True
    assert sorted(ticked) == ["Kubernetes / Docker", "Terraform"]
