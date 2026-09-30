"""Praxis (Greenhouse), 25 September: the Discipline list offered "Computer Science" but not the owner's
"Computer Technology". The agent rightly does not swap a field of study on its own -- the application ends with
the owner certifying it as true and correct -- and left the box empty. The owner then decided that, for this
one value, Computer Science is an acceptable stand-in.

The class: a decision that belongs to the owner has to be the owner's data, not a loosened rule and not a
literal in code. profile.answer_alternatives lists the substitutes the owner has approved for a value; they are
tried, in order, only after the exact value is not offered, and the hand-over says when one was used.
"""
import json
from types import SimpleNamespace

import pytest

import config
import page_agent
import safety
from browser_automation import JobApplicationAssistant

LIST = ["Computer Science", "Electrical Engineering"]


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


def discipline_widget(options=LIST):
    """A Greenhouse-style list: it searches as you type by the start of a name, and nothing is chosen until a row is."""
    return f"""<html><body>
<label id="lbl" for="discipline">Discipline</label>
<div class="select-shell">
  <div id="chosen" class="select__single-value"></div>
  <input id="discipline" role="combobox" aria-autocomplete="list" aria-labelledby="lbl" type="text" autocomplete="off">
  <div id="menu" hidden><div id="list" role="listbox"></div></div>
</div>
<script>
  const DATA = {list(options)!r};
  const input = document.getElementById('discipline'), list = document.getElementById('list'),
        menu = document.getElementById('menu'), chosen = document.getElementById('chosen');
  function pick(x) {{ chosen.textContent = x; input.value = ''; menu.hidden = true; }}
  function render(items) {{
    list.innerHTML = ''; menu.hidden = !items.length;
    items.forEach(x => {{ const o = document.createElement('div'); o.setAttribute('role', 'option'); o.textContent = x;
      o.addEventListener('mousedown', e => e.preventDefault()); o.addEventListener('click', () => pick(x)); list.appendChild(o); }});
  }}
  input.addEventListener('input', () => {{ const t = input.value.trim().toLowerCase();
    render(t ? DATA.filter(d => d.toLowerCase().startsWith(t)) : []); }});
  input.addEventListener('keydown', e => {{
    if (e.key === 'Enter' && !menu.hidden) pick(list.firstChild.textContent);
    if (e.key === 'Backspace' && input.value === '' && chosen.textContent) chosen.textContent = '';
  }});
  input.addEventListener('blur', () => {{ if (!chosen.textContent) input.value = ''; menu.hidden = true; }});
</script></body></html>"""


def make_agent(alternatives=None):
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    cfg = SimpleNamespace(auto_submit=False, ats_email="")
    job = SimpleNamespace(title="Engineer", company="Example", url="https://jobs.example.com/apply")
    profile = config.UserProfile(email="o@example.com", answer_alternatives=alternatives or {})
    return page_agent.PageAgent(assistant, SimpleNamespace(), cfg, profile, SimpleNamespace(raw_text="x"), job,
                                resume_file=None)


def box_of(agent, page):
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    return controls, next(c for c in controls if c.role == "combobox")


def shown(page):
    return page.locator("#chosen").inner_text()


def test_without_an_approved_alternative_a_different_subject_is_never_chosen(page):
    page.set_content(discipline_widget())
    agent = make_agent()
    controls, box = box_of(agent, page)
    assert agent.choose(page, box, "Computer Technology", controls) is False
    assert shown(page) == ""                                           # left empty, as before


def test_an_approved_alternative_is_used_when_the_exact_value_is_not_offered(page):
    page.set_content(discipline_widget())
    agent = make_agent({"Computer Technology": ["Computer Science"]})
    controls, box = box_of(agent, page)
    assert agent.choose(page, box, "Computer Technology", controls) is True
    assert shown(page) == "Computer Science"
    assert any("Computer Technology" in n and "Computer Science" in n for n in agent.notes)   # the owner is told


def test_the_exact_value_wins_when_it_is_offered(page):
    page.set_content(discipline_widget(options=["Computer Technology", "Computer Science"]))
    agent = make_agent({"Computer Technology": ["Computer Science"]})
    controls, box = box_of(agent, page)
    assert agent.choose(page, box, "Computer Technology", controls) is True
    assert shown(page) == "Computer Technology" and agent.notes == []


def test_only_the_values_the_owner_listed_have_alternatives(page):
    page.set_content(discipline_widget())
    agent = make_agent({"Computer Technology": ["Computer Science"]})
    controls, box = box_of(agent, page)
    assert agent.choose(page, box, "Mechanical Engineering", controls) is False
    assert shown(page) == ""


def test_alternatives_are_tried_in_the_owners_order_and_only_those(page):
    page.set_content(discipline_widget(options=["Information Systems", "Electrical Engineering"]))
    agent = make_agent({"computer technology": ["Computer Science", "Information Systems"]})   # spelling is no difference
    controls, box = box_of(agent, page)
    assert agent.choose(page, box, "Computer Technology", controls) is True
    assert shown(page) == "Information Systems"                          # not "Electrical Engineering"


def test_an_alternative_that_is_not_offered_either_leaves_the_box_empty(page):
    page.set_content(discipline_widget(options=["Electrical Engineering"]))
    agent = make_agent({"Computer Technology": ["Computer Science"]})
    controls, box = box_of(agent, page)
    assert agent.choose(page, box, "Computer Technology", controls) is False
    assert shown(page) == ""


def test_the_answer_reads_as_what_was_chosen_so_the_page_is_not_read_again_and_again(page):
    """apply_answers records the substitute, or the check that an answer stayed would call it a different answer
    and the page would be re-read until the run gave up."""
    page.set_content(discipline_widget())
    agent = make_agent({"Computer Technology": ["Computer Science"]})
    controls, box = box_of(agent, page)
    plan = page_agent.PagePlan(page_kind="application_form", answers=[
        page_agent.Answer(ref=box.ref, question="Discipline", action="choose", value="Computer Technology",
                          source="profile.education.discipline")])
    given = agent.apply_answers(page, plan, controls)
    assert [a.value for a, _c in given] == ["Computer Science"]
    after = page_agent.parse_snapshot(agent.snapshot(page))
    assert agent.not_stuck(given, after) == []


def test_the_profile_file_carries_the_owners_alternatives(tmp_path, monkeypatch):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"full_name": "Jane Doe",
                                "answer_alternatives": {"Computer Technology": ["Computer Science"]}}),
                    encoding="utf-8")
    monkeypatch.setattr(config, "PROFILE_PATH", path)
    assert config.get_user_profile().answer_alternatives == {"Computer Technology": ["Computer Science"]}
    assert config.UserProfile().answer_alternatives == {}                # nothing is substituted by default
