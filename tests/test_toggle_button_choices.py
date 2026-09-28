"""Choices drawn as a row of toggle buttons.

Writer (Ashby), 28 September: "Are you at least 18 years of age?", "Are you
legally authorized to work...?", "Will you ... require employment visa
sponsorship?" and the office-days question were left blank on six runs in a
row, although the profile answers every one. Ashby draws each Yes/No question
as two <button aria-pressed> elements under a <label>. The reader took them for
two ordinary buttons called "Yes" and "No", tied to no question, so nothing
answered them. And a toggle clicked twice is cleared, so the click that
answers one must be a single click that is then read back.

The class: a question whose choices are pressable buttons, not radios. These
tests cover the row wherever it appears -- any question, any number of
choices, pressed or not -- and the rows that must NOT be read as choices.
"""

import dataclasses
from types import SimpleNamespace

import pytest

import config
import page_agent
import safety
from browser_automation import JobApplicationAssistant


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


# Each button toggles itself, as Ashby's do: a second click clears the answer.
TOGGLE_SCRIPT = """<script>
document.addEventListener('click', e => {
  const b = e.target.closest('button[aria-pressed]');
  if (!b) return;
  const on = b.getAttribute('aria-pressed') !== 'true';
  b.parentElement.querySelectorAll('button[aria-pressed]').forEach(x => x.setAttribute('aria-pressed', 'false'));
  b.setAttribute('aria-pressed', on ? 'true' : 'false');
});
</script>"""


def question(text: str, choices=("Yes", "No"), pressed: str = "") -> str:
    buttons = "".join(
        f'<button type="button" class="option" aria-pressed="{"true" if c == pressed else "false"}">{c}</button>'
        for c in choices)
    return f'<div class="field"><label>{text}</label><div class="yesno">{buttons}</div></div>'


def serve(page, body: str) -> None:
    page.set_content(f"<html><body><form>{body}</form>{TOGGLE_SCRIPT}</body></html>")


def controls_of(page):
    return page_agent.parse_snapshot(page.locator("body").aria_snapshot(mode="ai"))


def row(controls, text):
    return [c for c in controls if c.group == text]


# --- reading: any question, any choices, pressed or not ------------------------

@pytest.mark.parametrize("text", [
    "Are you at least 18 years of age?",
    "Are you legally authorized to work in the country in which the job you are applying for is located?",
    "Will you now or in the future require employment visa sponsorship to work in the country?",
    "Are you able and excited to join us for in person, collaborative working sessions in office 3 days / week?",
])
@pytest.mark.parametrize("choices", [("Yes", "No"), ("Yes", "No", "Prefer not to say"), ("True", "False")])
@pytest.mark.parametrize("pressed", ["", "first", "last"])
def test_a_row_of_toggle_buttons_is_read_as_that_questions_choices(page, text, choices, pressed):
    chosen = {"": "", "first": choices[0], "last": choices[-1]}[pressed]
    serve(page, question(text, choices, chosen))
    found = row(controls_of(page), text)
    assert [c.name for c in found] == list(choices)
    assert all(c.role == "radio" and c.toggle for c in found)
    assert [c.name for c in found if c.checked] == ([chosen] if chosen else [])


def test_each_row_keeps_its_own_question(page):
    serve(page, question("Are you at least 18 years of age?", pressed="Yes")
          + question("Will you now or in the future require visa sponsorship?"))
    controls = controls_of(page)
    assert [c.checked for c in row(controls, "Are you at least 18 years of age?")] == [True, False]
    assert [c.checked for c in row(controls, "Will you now or in the future require visa sponsorship?")] == [False, False]


@pytest.mark.parametrize("buttons", [
    ("Back", "Next"),
    ("Cancel", "Continue"),
    ("Edit", "Remove"),
    ("Save and Continue", "Finish Later"),
    ("Attach", "Enter manually"),
])
def test_a_row_of_action_buttons_is_not_a_question(page, buttons):
    html = "".join(f'<button type="button">{b}</button>' for b in buttons)
    serve(page, f"<label>Are you ready to continue?</label><div>{html}</div>")
    controls = controls_of(page)
    assert all(c.role == "button" and not c.toggle for c in controls if c.name in buttons)


@pytest.mark.parametrize("asks, concept", [
    ("Are you legally authorized to work", "WORK_AUTHORIZATION"),
    ("Are you authorized to work", "WORK_AUTHORIZATION"),
    ("Will you now or in the future require visa sponsorship to work", "VISA_SPONSORSHIP"),
    ("Do you need sponsorship to work", "VISA_SPONSORSHIP"),
])
@pytest.mark.parametrize("where", [
    " in the country in which the job you are applying for is located?",
    " in the country where this role is based?",
    " in this country?",
    " in the state or province of this role?",
    "?",
])
def test_a_place_named_inside_a_status_question_does_not_make_it_a_place_question(asks, concept, where):
    """Writer, 28 September: 'Are you legally authorized to work in the country ...?' was taken
    for a COUNTRY question and answered 'United States' -- a place word as a qualifier outscored
    the phrase that says what is asked."""
    import concept_matcher
    assert concept_matcher.match_concept(asks + where) == concept


def test_a_lone_button_is_not_a_question(page):
    serve(page, '<label>Are you at least 18 years of age?</label><div><button type="button">Yes</button></div>')
    (only,) = [c for c in controls_of(page) if c.name == "Yes"]
    assert only.role == "button" and not only.toggle


def test_unpressed_buttons_after_a_heading_that_asks_nothing_are_left_alone(page):
    serve(page, '<h2>Your documents</h2><div><button type="button">Upload</button>'
                '<button type="button">Preview</button></div>')
    assert all(c.role == "button" for c in controls_of(page) if c.name in ("Upload", "Preview"))


# --- answering: from the profile, one click, read back -------------------------

@pytest.fixture
def make_agent(tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test resume")

    def build(profile):
        assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
        assistant.values = safety.AgentValues()
        cfg = SimpleNamespace(auto_submit=False, ats_email="")
        job = SimpleNamespace(title="Infrastructure Engineer", company="Example",
                              url="https://jobs.example.com/apply")
        return page_agent.PageAgent(assistant, SimpleNamespace(), cfg, profile,
                                    SimpleNamespace(raw_text="Infrastructure engineer"), job,
                                    resume_file=resume)
    return build


WRITER_QUESTIONS = (
    "Are you at least 18 years of age?",
    "Are you legally authorized to work in the country in which the job you are applying for is located?",
    "Will you now or in the future require employment visa sponsorship to work in the country in which the job you're applying for is located?",
)


def pressed(page, text):
    return page.evaluate("""t => { const l = [...document.querySelectorAll('label')].find(x => x.textContent === t);
        const b = l.parentElement.querySelector('button[aria-pressed="true"]'); return b ? b.textContent : ''; }""", text)


@pytest.mark.parametrize("needs_sponsorship", [True, False])
def test_the_profile_answers_toggle_questions_with_one_click(page, make_agent, needs_sponsorship):
    profile = dataclasses.replace(config.get_user_profile(), at_least_18="Yes",
                                  legally_eligible_to_work="Yes", requires_visa_sponsorship=needs_sponsorship)
    serve(page, "".join(question(q) for q in WRITER_QUESTIONS))
    agent = make_agent(profile)
    agent.answer_what_is_known(page, controls_of(page), set())
    assert pressed(page, WRITER_QUESTIONS[0]) == "Yes"
    assert pressed(page, WRITER_QUESTIONS[2]) == ("Yes" if needs_sponsorship else "No")


def test_an_answer_already_pressed_is_not_clicked_off(page, make_agent):
    """A second click clears a toggle: the reader must see the pressed answer and leave it."""
    profile = dataclasses.replace(config.get_user_profile(), at_least_18="Yes")
    serve(page, question(WRITER_QUESTIONS[0], pressed="Yes"))
    agent = make_agent(profile)
    for _ in range(2):                       # a refill pass reads the page again
        agent.answer_what_is_known(page, controls_of(page), set())
    assert pressed(page, WRITER_QUESTIONS[0]) == "Yes"


def test_a_toggle_that_does_not_stay_pressed_is_reported_not_counted(page, make_agent):
    """A site whose button ignores the click: the answer is not claimed as given."""
    profile = dataclasses.replace(config.get_user_profile(), at_least_18="Yes")
    page.set_content("<html><body><form>" + question(WRITER_QUESTIONS[0]) + "</form></body></html>")  # no script
    agent = make_agent(profile)
    filled, still_open = agent.answer_what_is_known(page, controls_of(page), set())
    assert filled == 0
    assert any(q.startswith(WRITER_QUESTIONS[0]) for q in still_open)
