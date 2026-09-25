"""Praxis (Greenhouse), 25 September: two required dropdowns -- "Are you a former Praxis employee ...?" and
"Applicants must read the description ... acknowledgement" -- were answered "No" and "Yes", and neither took: the
form's own choices are "Never Employed by Praxis" and "I have read, authorize and acknowledge". The choices were
found only by opening the lists by hand.

The agent already read the choices of a dropdown whose name was a prompt ("Choose an option"). These two are named
by their question, so they were never opened, and the planner guessed at a vocabulary it had not seen. The class:
a list the agent never looked at, once the control has a name. Any blank dropdown the agent could not answer itself
is now opened and read, so the planner is given the form's own words.
"""
import re
from types import SimpleNamespace

import pytest

import config
import page_agent
import safety
from browser_automation import JobApplicationAssistant

QUESTION = "Are you a former Praxis employee, or have you performed any work for us (in any capacity) in the past?"
CHOICES = ["Currently Employed by Praxis", "Never Employed by Praxis", "Former Employee",
           "Former Consultant or Contractor", "Former Intern or Fellow"]


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


def select_page(options=CHOICES, question=QUESTION, wrapper_class="select-shell", value_text="Select...",
                answered=False):
    """A react-select: the question is the box's own name, "Select..." sits beside it, and the list is drawn only
    once it is opened."""
    shown = options[0] if answered else value_text
    return f"""<html><body>
<div id="q"><label id="lbl" for="box">{question}*</label>
<div class="{wrapper_class}"><div id="value">{shown}</div>
<input id="box" role="combobox" aria-labelledby="lbl" aria-autocomplete="list" aria-expanded="false" type="text">
<div id="menu" hidden><div id="list" role="listbox"></div></div></div></div>
<label for="other">Comments</label><input id="other">
<script>
  window.opened = 0; const DATA = {list(options)!r};
  const box = document.getElementById('box'), list = document.getElementById('list'), menu = document.getElementById('menu');
  function close() {{ menu.hidden = true; }}
  box.addEventListener('mousedown', () => {{
    window.opened++; list.innerHTML = '';
    DATA.forEach(x => {{ const o = document.createElement('div'); o.setAttribute('role', 'option'); o.textContent = x;
      o.addEventListener('mousedown', e => e.preventDefault());
      o.addEventListener('click', () => {{ document.getElementById('value').textContent = x; window.chosen = x; close(); }});
      list.appendChild(o); }});
    menu.hidden = !DATA.length;
  }});
  box.addEventListener('click', () => {{ menu.hidden = !DATA.length; }});
  document.addEventListener('keydown', e => {{ if (e.key === 'Escape') close(); }});
</script></body></html>"""


def make_agent():
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    cfg = SimpleNamespace(auto_submit=False, ats_email="")
    job = SimpleNamespace(title="Engineer", company="Example", url="https://jobs.example.com/apply")
    return page_agent.PageAgent(assistant, SimpleNamespace(), cfg, config.UserProfile(email="o@example.com"),
                                SimpleNamespace(raw_text="x"), job, resume_file=None)


def read(agent, page, only):
    snap = agent.snapshot(page)
    controls = page_agent.parse_snapshot(snap)
    return agent.read_hidden_choices(page, controls, snap, only=only)


def state(page):
    return page.evaluate("({opened: window.opened || 0, chosen: window.chosen || ''})")


def test_a_blank_dropdown_named_by_its_question_is_opened_and_read_when_the_agent_could_not_answer_it(page):
    page.set_content(select_page())
    said = read(make_agent(), page, only=[QUESTION + "*"])
    assert all(f"'{choice}'" in said for choice in CHOICES)
    assert "former Praxis employee" in said
    # left as it was found: closed, nothing chosen
    assert page.locator("[role=listbox] [role=option]:visible").count() == 0
    assert state(page)["chosen"] == "" and page.locator("#value").inner_text() == "Select..."


def test_it_is_not_opened_when_the_agent_had_an_answer_for_it_itself(page):
    page.set_content(select_page())
    assert read(make_agent(), page, only=["Some other question*"]) == ""
    assert read(make_agent(), page, only=[]) == ""
    assert state(page)["opened"] == 0


def test_a_dropdown_that_already_shows_an_answer_is_not_opened(page):
    page.set_content(select_page(answered=True))
    assert read(make_agent(), page, only=[QUESTION + "*"]) == ""
    assert state(page)["opened"] == 0


def test_a_very_long_list_is_left_to_type_into_not_handed_over(page):
    """A School or Country list is hundreds long: the planner is given the choices only when it can read them."""
    page.set_content(select_page(options=[f"School number {i}" for i in range(200)]))
    assert read(make_agent(), page, only=[QUESTION + "*"]) == ""
    assert page.locator("[role=option]:visible").count() == 0            # and it was closed again


def test_an_ant_design_select_is_left_to_its_own_reader(page):
    page.set_content(select_page(wrapper_class="ant-select"))
    assert read(make_agent(), page, only=[QUESTION + "*"]) == ""
    assert state(page)["opened"] == 0


def test_a_box_that_never_offers_a_list_costs_nothing_and_is_left_alone(page):
    page.set_content(select_page(options=[]))
    assert read(make_agent(), page, only=[QUESTION + "*"]) == ""
    assert page.locator("[role=option]:visible").count() == 0


def test_a_prompt_named_dropdown_is_still_read_as_before(page):
    page.set_content(select_page().replace(f"aria-labelledby=\"lbl\"", "aria-label=\"Choose an option\""))
    assert "'Never Employed by Praxis'" in read(make_agent(), page, only=[])


def test_it_reads_no_more_than_a_few_dropdowns_in_one_look(page):
    boxes = "".join(select_page().split("<body>")[1].split("<script>")[0].replace('id="box"', f'id="b{i}"')
                    .replace('id="q"', f'id="q{i}"').replace('id="lbl"', f'id="l{i}"').replace('for="box"', f'for="b{i}"')
                    .replace('aria-labelledby="lbl"', f'aria-labelledby="l{i}"').replace(QUESTION, f"Question {i}")
                    for i in range(12))
    page.set_content(f"<html><body>{boxes}</body></html>")
    agent = make_agent()
    snap = agent.snapshot(page)
    controls = page_agent.parse_snapshot(snap)
    only = [f"Question {i}" for i in range(12)]
    agent.read_hidden_choices(page, controls, snap, only=only)
    opened = page.locator("[role=option]:visible").count()
    assert opened == 0                                                    # all closed again
    assert agent._peeked and sum(agent._peeked.values()) <= page_agent.PageAgent.MAX_PEEKS_PER_READ


class FormsOwnWords:
    """Answers the dropdown only when it has been told the form's own words -- as the planner could not before."""

    def __init__(self):
        self.feedbacks = []

    def plan_page(self, snapshot, facts, feedback=""):
        self.feedbacks.append(feedback)
        box = re.search(r'combobox "[^"]*" (?:\[[^\]]*\] )*\[ref=([\w-]+)\]', snapshot)
        plan = {"page_kind": "application_form", "step": "1 of 1", "answers": [], "leave_for_owner": [],
                "next": {"ref": "", "label": "", "kind": "none"}}
        if "'Never Employed by Praxis'" in feedback and box:
            plan["answers"].append({"ref": box.group(1), "question": QUESTION, "action": "choose",
                                    "value": "Never Employed by Praxis", "source": "profile.previously_employed_here"})
        else:
            plan["answers"].append({"ref": box.group(1) if box else "", "question": QUESTION, "action": "choose",
                                    "value": "No", "source": "profile.previously_employed_here"})
        return plan


def test_the_planner_is_given_the_forms_own_words_and_the_question_is_answered_in_them(page):
    page.set_content(select_page())
    planner = FormsOwnWords()
    agent = make_agent()
    agent.claude = planner
    agent.run(page)
    assert page.locator("#value").inner_text() == "Never Employed by Praxis"
    assert any("'Never Employed by Praxis'" in f for f in planner.feedbacks)
