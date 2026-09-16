"""The pickers on a real Greenhouse form, and what the agent reads back.

These cover three mistakes made against a live application: the phone
widget's dialling-code list being offered as the answer to a clearance
question, a chosen value reading back as blank because react-select clears
its search input, and a salary band being left for the user to pick.
"""

import re

import pytest

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
    pg = browser.new_page()
    yield pg
    pg.close()


@pytest.fixture
def agent():
    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a.values = safety.AgentValues()
    return a


GREENHOUSE_FORM = """
<html><body>
  <label for="phone_country">Country*</label>
  <div class="select__control"><div class="select__input-container">
    <input id="phone_country" class="select__input" role="combobox"></div></div>
  <div class="select__menu" id="phone_menu">
    <div class="select__option" role="option">Afghanistan+93</div>
    <div class="select__option" role="option">Åland Islands+358</div>
    <div class="select__option" role="option">United States+1</div>
  </div>

  <label for="q_clearance">What is your current clearance level?*</label>
  <div class="select__control"><div class="select__value-container">
    <div class="select__single-value">I do not have a clearance</div>
    <div class="select__input-container">
      <input id="q_clearance" class="select__input" role="combobox" value="" required></div>
  </div></div>
</body></html>
"""


@pytest.fixture
def form(page):
    page.set_content(GREENHOUSE_FORM)
    return page


def test_a_chosen_option_is_not_read_back_as_blank(form, agent):
    """react-select empties its search input once you choose; the choice is
    rendered beside it. Reading only the input reported answered questions as
    still blank, and the run stopped for nothing."""
    assistant = agent
    field = form.locator("[id='q_clearance']")
    assert field.input_value() == ""
    assert assistant.displayed_value(field) == "I do not have a clearance"


def test_the_form_read_back_shows_the_chosen_value(form, agent):
    assistant = agent
    clearance = next(f for f in assistant.read_back_fields(form)
                     if "clearance" in f["label"].lower())
    assert clearance["value"] == "I do not have a clearance"


def test_a_required_picker_with_a_choice_is_not_reported_blank(form, agent):
    assistant = agent
    blanks = assistant.find_required_blanks(form)["required_still_blank"]
    assert not [b for b in blanks if "clearance" in b.lower()]


def test_the_phone_country_list_is_never_offered_as_an_answer(form):
    """The dialling-code list is open on the page at the same time as every
    other question; a clearance question was once offered 'Afghanistan+93'."""
    dial_code = re.compile(r"\+\d{1,4}$")
    options = [t.strip() for t in form.locator("[class*=select__option]").all_inner_texts()]
    assert sum(bool(dial_code.search(t)) for t in options) > len(options) / 2


@pytest.mark.parametrize("bands, expected", [
    (["$80,000 - $100,000", "$120,000 - $160,000", "$200,000+"], "$120,000 - $160,000"),
    (["$60k - $90k", "$150k - $190k"], "$150k - $190k"),
])
def test_the_pay_band_matching_the_profile_range_is_chosen(bands, expected, agent):
    assistant = agent
    assistant._profile = type("P", (), {"salary_min": 125_000, "salary_max": 185_000})()
    assert bands[assistant._salary_band(bands)] == expected


def test_a_band_outside_the_profile_range_is_left_for_the_user(agent):
    """Picking a band that doesn't overlap the range would ask for money the
    user never said they wanted."""
    assistant = agent
    assistant._profile = type("P", (), {"salary_min": 125_000, "salary_max": 185_000})()
    assert assistant._salary_band(["$40,000 - $60,000", "$1 - $999"]) is None


NEIGHBOURING_FIELDS = """
<html><body>
  <div class="field-wrapper"><div class="input-wrapper">
    <label for="preferred_name">Preferred First Name</label>
    <input id="preferred_name" class="input" type="text" value="">
  </div></div>
  <div class="field-wrapper"><div class="input-wrapper">
    <label for="phone_country">Country*</label>
    <div class="select__control"><div class="select__value-container">
      <div class="select__single-value">+1</div>
      <div class="select__input-container">
        <input id="phone_country" class="select__input" role="combobox" required></div>
    </div></div>
  </div></div>
</body></html>
"""


def test_a_value_is_never_read_from_the_field_next_door(page, agent):
    """The phone widget's "+1" was reported as the answer to Preferred First
    Name, which is empty. A value picked up from a neighbouring control would
    be shown as answered, learned as the user's answer, and could let a blank
    required field pass as filled."""
    page.set_content(NEIGHBOURING_FIELDS)
    by_label = {f["label"]: f["value"] for f in agent.read_back_fields(page)}
    assert by_label["Preferred First Name"] == ""
    assert by_label["Country*"] == "+1"


def test_an_empty_field_beside_an_answered_one_is_still_reported_blank(page, agent):
    page.set_content(NEIGHBOURING_FIELDS.replace('id="preferred_name" class="input" type="text"',
                                                 'id="preferred_name" class="input" type="text" required'))
    blanks = agent.find_required_blanks(page)["required_still_blank"]
    assert any("Preferred First Name" in b for b in blanks)


def test_the_tailored_resume_is_always_asked_for():
    """The tailoring call must work with no correction instruction.

    A leftover reference to a removed parameter made every first attempt raise
    NameError, and the run quietly attached the generic resume instead -- an
    Amazon application went out with it before this was noticed.
    """
    import inspect

    from claude_integration import ClaudeClient

    signature = inspect.signature(ClaudeClient.tailor_resume)
    assert signature.parameters["extra_instruction"].default == ""

    source = inspect.getsource(ClaudeClient.tailor_resume)
    client = ClaudeClient.__new__(ClaudeClient)
    asked = {}

    def fake_call(system, user_message, max_tokens=0):
        asked["user_message"] = user_message
        return "TAILORED"

    client._call = fake_call
    job = type("J", (), {"title": "Support Engineer", "company": "Amazon", "raw_text": "networking"})()
    resume = type("R", (), {"raw_text": "five years of networking"})()
    profile = type("P", (), {"full_name": "Someone", "years_experience": 6})()
    assert client.tailor_resume(resume, job, profile) == "TAILORED"
    assert "networking" in asked["user_message"]
    assert "IMPORTANT CORRECTION" in source  # the retry path still exists


def test_a_missing_adapter_hook_does_not_end_the_run(page, agent):
    """Hot-reloading brings in new code while the run still holds adapters
    built from the old. A hook that isn't there yet cost a live Amazon
    application its browser; it must cost only the feature."""
    page.set_content("<html><body><p>a form</p></body></html>")

    class OldAdapter:
        name = "old"

    agent.adapter = lambda _page: OldAdapter()
    assert agent._adapter_hook(page, "platform_questions", []) == []
    assert agent._adapter_hook(page, "answer_platform_question", False) is False


def test_an_adapter_hook_that_raises_does_not_end_the_run(page, agent):
    page.set_content("<html><body><p>a form</p></body></html>")

    class BrokenAdapter:
        name = "broken"

        def platform_questions(self, _page):
            raise RuntimeError("the page moved")

    agent.adapter = lambda _page: BrokenAdapter()
    assert agent._adapter_hook(page, "platform_questions", [], page) == []
