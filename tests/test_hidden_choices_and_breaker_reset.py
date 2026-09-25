"""Two things a live ADP application (Willdan, 24 September) stopped on.

1. A required dropdown -- "If you are under 18 years of age, can you provide proof of
   your eligibility to work?" -- reaches the agent as `button "Choose an option"`: the
   page draws its choices only once it is opened. Claude was asked to answer it without
   seeing them, said their names were unknown, and the question was left for the owner,
   who was the one thing the agent is there to spare. A list that is drawn only when opened
   is read by opening it.

2. The loop guard (three presses of Next on an unchanged page) was never re-armed. After it
   tripped, the owner pressed Continue, the agent filled the missing answer and pressed Next
   once, and the guard tripped again at once -- "identical across 4 consecutive cycles" --
   because its count carried over. A resumed run gets the guard's three tries afresh.
"""
import re
from types import SimpleNamespace

import pytest

import config
import page_agent
import safety
from browser_automation import JobApplicationAssistant
from state_machine import StateFingerprintCircuitBreaker

QUESTION = "If you are under 18 years of age, can you provide proof of your eligibility to work?*"


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


def make_agent(planner=None):
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    cfg = SimpleNamespace(auto_submit=False, ats_email="")
    job = SimpleNamespace(title="Cloud Engineer", company="Example", url="https://jobs.example.com/apply")
    return page_agent.PageAgent(assistant, planner or SimpleNamespace(), cfg, config.UserProfile(email="o@example.com"),
                                SimpleNamespace(raw_text="Cloud engineer"), job, resume_file=None)


def snapshot_of(agent, page):
    snap = agent.snapshot(page)
    return snap, page_agent.parse_snapshot(snap)


def dropdown_page(label=QUESTION, shown="Choose an option", options=("Yes", "No", "N/A")):
    """ADP's shape: a label, then a button that draws its list in a portal only when opened."""
    return f"""<html><body>
<div id="q"><div>{label}</div>
  <div id="wrap"><button id="dd">{shown}</button></div></div>
<button id="next" onclick="location.href='/done'">Next</button>
<script>
  window.opened = 0; let list = null;
  const opts = {list(options)!r}.map(String);
  function close() {{ if (list) {{ list.remove(); list = null; }} }}
  document.getElementById('wrap').addEventListener('click', () => {{
    if (list) return close();
    window.opened++;
    list = document.createElement('ul'); list.setAttribute('role', 'listbox');
    for (const o of opts) {{
      const li = document.createElement('li'); li.setAttribute('role', 'option'); li.textContent = o;
      li.addEventListener('click', e => {{ e.stopPropagation(); document.getElementById('dd').textContent = o;
                                          window.choice = o; try {{ localStorage.choice = o; }} catch (x) {{}}
                                          close(); }});
      list.appendChild(li);
    }}
    document.body.appendChild(list);
  }});
  document.addEventListener('keydown', e => {{ if (e.key === 'Escape') close(); }});
</script></body></html>"""


def opened_count(page):
    return page.evaluate("window.opened")


# --- 1. a list drawn only when opened is read by opening it ---------------------------------

def test_the_label_above_a_control_is_the_question_it_answers():
    snap = f'''- generic [ref=f10e355]:
  - generic [ref=f10e356]: {QUESTION}
  - button "Choose an option" [ref=f10e363]
- generic [ref=f10e369]:
  - generic [ref=f10e370]: If hired, will you be able to provide documentation?*
  - radiogroup "x" [ref=f10e377]'''
    assert page_agent.label_above(snap, "f10e363") == QUESTION
    assert page_agent.label_above(snap, "nope") == ""


def test_a_dropdown_that_draws_its_choices_only_when_opened_is_read_by_opening_it(page):
    page.set_content(dropdown_page())
    agent = make_agent()
    snap, controls = snapshot_of(agent, page)
    said = agent.read_hidden_choices(page, controls, snap)
    assert "If you are under 18 years of age" in said
    assert all(f"'{choice}'" in said for choice in ("Yes", "No", "N/A"))
    # ... and it is left as it was found: closed, and nothing chosen.
    assert page.locator("[role=listbox]").count() == 0
    assert page.locator("#dd").inner_text() == "Choose an option"
    assert page.evaluate("window.choice || ''") == ""


@pytest.mark.parametrize("prompt", ["Choose an option", "Select one", "Select an option", "Please select",
                                    "-- Select --", "Select"])
def test_every_way_a_blank_dropdown_says_so_is_opened(page, prompt):
    page.set_content(dropdown_page(shown=prompt))
    agent = make_agent()
    snap, controls = snapshot_of(agent, page)
    assert "'N/A'" in agent.read_hidden_choices(page, controls, snap)


@pytest.mark.parametrize("shown", ["LinkedIn.com", "Yes", "United States"])
def test_a_dropdown_that_already_shows_an_answer_is_not_opened(page, shown):
    page.set_content(dropdown_page(shown=shown))
    agent = make_agent()
    snap, controls = snapshot_of(agent, page)
    assert agent.read_hidden_choices(page, controls, snap) == ""
    assert opened_count(page) == 0


def test_a_page_with_nothing_hidden_is_left_alone(page):
    page.set_content('<label for="a">Name</label><input id="a"><button id="n">Next</button>')
    agent = make_agent()
    snap, controls = snapshot_of(agent, page)
    assert agent.read_hidden_choices(page, controls, snap) == ""


def test_a_dropdown_is_not_opened_again_and_again(page):
    page.set_content(dropdown_page())
    agent = make_agent()
    snap, controls = snapshot_of(agent, page)
    for _ in range(6):
        agent.read_hidden_choices(page, controls, snap)
    assert 1 <= opened_count(page) <= 2


def test_a_list_that_stays_open_is_closed_again(page):
    """Some widgets ignore Escape: the button that opened the list closes it."""
    html = dropdown_page().replace("document.addEventListener('keydown'", "document.addEventListener('keydownX'")
    page.set_content(html)
    agent = make_agent()
    snap, controls = snapshot_of(agent, page)
    agent.read_hidden_choices(page, controls, snap)
    assert page.locator("[role=listbox]").count() == 0


class ChoicePlanner:
    """Answers the dropdown only when it has been told the choices -- as Claude did not, before."""

    def __init__(self):
        self.feedbacks = []

    def plan_page(self, snapshot, facts, feedback=""):
        self.feedbacks.append(feedback)
        if "Thank you" in snapshot:
            return {"page_kind": "confirmation", "next": {"kind": "none"}}
        dd = re.search(r'button "(?!Next)[^"]*"[^\n]*?\[ref=([\w-]+)\]', snapshot)
        nxt = re.search(r'button "Next"[^\n]*?\[ref=([\w-]+)\]', snapshot)
        plan = {"page_kind": "application_form", "step": "1 of 2", "answers": [], "for_owner": [],
                "next": {"ref": nxt.group(1) if nxt else "", "label": "Next", "kind": "next_step"}}
        if "'N/A'" in feedback:
            plan["answers"].append({"ref": dd.group(1), "question": QUESTION, "action": "choose",
                                    "value": "N/A", "source": "profile.at_least_18"})
        else:
            plan["for_owner"].append({"question": QUESTION, "required": True,
                                      "reason": "exact dropdown options for this conditional question are unknown"})
        return plan


def test_the_choices_reach_the_planner_and_the_question_is_answered_by_the_agent(page):
    pages = {"/apply": dropdown_page(), "/done": "<html><body><h1>Thank you for applying!</h1></body></html>"}
    page.route("https://jobs.example.com/**", lambda route: route.fulfill(
        status=200, content_type="text/html",
        body=pages.get("/" + route.request.url.split("/", 3)[3].split("?")[0], "not found")))
    page.goto("https://jobs.example.com/apply")
    planner = ChoicePlanner()
    agent = make_agent(planner)
    agent.run(page)
    assert page.evaluate("localStorage.choice || ''") == "N/A"
    assert any("If you are under 18" in f and "'N/A'" in f for f in planner.feedbacks)


# --- 2. a resumed run gets the loop guard's three tries afresh -------------------------------

def press_next_once(agent, page):
    snap, controls = snapshot_of(agent, page)
    plan = page_agent.PagePlan(page_kind="application_form", next_kind="next_step", next_label="Next",
                               next_ref=next(c.ref for c in controls if c.name == "Next"))
    return agent.press_next(page, plan, controls)


def test_a_tripped_loop_guard_is_re_armed_when_the_run_resumes(page):
    page.set_content('<label for="a">Answer *</label><input id="a"><button id="n">Next</button>')  # Next does nothing
    agent = make_agent()
    outcomes = [press_next_once(agent, page) for _ in range(3)]
    assert outcomes[-1][0] == "stop" and "three tries" in outcomes[-1][2]
    agent.forget_sign_in_attempts()                       # the owner pressed Continue
    kind, _page, why = press_next_once(agent, page)
    assert "three tries" not in why, why                  # it gets its tries again, not an instant stop


def test_without_a_resume_the_loop_guard_still_stops_a_page_that_never_moves(page):
    page.set_content('<label for="a">Answer *</label><input id="a"><button id="n">Next</button>')
    agent = make_agent()
    kinds = [press_next_once(agent, page) for _ in range(5)]
    assert kinds[2][0] == "stop" and "three tries" in kinds[2][2]
    assert kinds[3][0] == "stop"                          # and it stays stopped until someone resumes it


def test_the_guard_itself_starts_from_nothing_after_a_reset(page):
    page.set_content("<p>x</p>")
    guard = StateFingerprintCircuitBreaker(consecutive_threshold=3)
    assert [guard.check(page)[0] for _ in range(3)] == [False, False, True]
    guard.reset()
    assert [guard.check(page)[0] for _ in range(3)] == [False, False, True]
