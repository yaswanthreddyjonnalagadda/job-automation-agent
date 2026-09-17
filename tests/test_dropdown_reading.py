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
    from config import get_user_profile

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
    profile = get_user_profile()  # the prompt now names most of the profile
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


BOOTSTRAP_RADIOS = """
<html><head><style>
  .custom-control-input { position: absolute; opacity: 0; z-index: -1; }
  .custom-control-label { display: block; padding: 12px; background: #eee; }
</style></head><body>
  <div class="custom-control">
    <input type="radio" class="custom-control-input" id="r-yes" name="gov" value="Yes">
    <label class="custom-control-label" for="r-yes">Yes</label>
  </div>
  <div class="custom-control">
    <input type="radio" class="custom-control-input" id="r-no" name="gov" value="No">
    <label class="custom-control-label" for="r-no">No</label>
  </div>
</body></html>
"""


def test_a_radio_under_its_own_label_is_still_selected(page, agent):
    """Amazon's radios sit under a styled label, so a pointer click lands on
    the label and Playwright retries the input for its whole timeout: one
    question became a thirty-second wait, and the run looped on the same pass
    every three minutes without ever finishing."""
    page.set_content(BOOTSTRAP_RADIOS)
    target = page.query_selector("#r-no")
    assert agent.select_radio(page, target)
    assert page.query_selector("#r-no").is_checked()


GOOGLE_POSTING = """
<html><body>
  <h1>Network Implementation Engineer, Data Center Networking Delivery</h1>
  <a href="/about/careers/applications/jobs/results/123" style="display:none">Apply to an older posting</a>
  <a class="WpHeLc VfPpkd-mRLv6" href="./apply?jobId=CiUAL2Fck&loc=US" aria-label="Apply"><i class="icon"></i></a>
</body></html>
"""


def test_an_unlabelled_apply_link_is_still_found(page, agent):
    """Google's posting renders the Apply button's label in script that had
    not run when the agent looked: the element carried no text and no
    aria-label, only its href. Matching on text alone found nothing, so the
    agent decided it was already on the application form, filled in nothing,
    and reported an application it had never opened."""
    page.set_content(GOOGLE_POSTING)
    assert agent.find_apply_control(page) is None  # nothing visible to click
    assert "apply?jobId" in agent.apply_destination(page)


def test_a_labelled_apply_button_is_found_directly(page, agent):
    page.set_content("""<html><body>
        <button style="width:120px;height:40px" aria-label="Apply">Apply</button>
      </body></html>""")
    assert agent.find_apply_control(page) is not None


def test_a_posting_with_no_way_to_apply_reports_nothing(page, agent):
    page.set_content("<html><body><p>Just a description, no way to apply.</p></body></html>")
    assert agent.find_apply_control(page) is None
    assert agent.apply_destination(page) == ""


def test_the_configured_browser_is_used_even_while_chrome_is_open(monkeypatch, agent):
    """The user asked for real Chrome always, for rendering fidelity, so the
    fallback to the bundled browser is deliberately gone.

    The cost is real and was measured: Chrome will not start a second instance
    while one is running, even against a separate profile -- it hands the
    command to the running copy and exits -- so a run started while their
    Chrome is open fails to launch. Closing Chrome first is the price of this
    setting."""
    from browser_automation import JobApplicationAssistant

    agent._config = type("C", (), {"browser_channel": "chrome",
                                   "browser_profile_dir": "C:/x/browser_profile"})()
    monkeypatch.setattr(JobApplicationAssistant, "_chrome_is_running", staticmethod(lambda: True))
    assert agent._choose_channel() == "chrome"

    monkeypatch.setattr(JobApplicationAssistant, "_chrome_is_running", staticmethod(lambda: False))
    assert agent._choose_channel() == "chrome"


def test_only_browsers_holding_the_agents_own_profile_are_closed(monkeypatch):
    """A run whose process is killed leaves its browser running, still holding
    the profile, and every later run then died at startup. The leftovers are
    identified by the profile path, so the user's own windows are untouched."""
    import json as _json
    from pathlib import Path

    from browser_automation import JobApplicationAssistant

    processes = [
        {"ProcessId": 11, "CommandLine": r"chrome.exe --user-data-dir=C:\agent\browser_profile"},
        {"ProcessId": 22, "CommandLine": r"chrome.exe --user-data-dir=C:\Users\me\AppData\Chrome\User Data"},
        {"ProcessId": 33, "CommandLine": None},
    ]
    killed = []

    class _Result:
        stdout = _json.dumps(processes)

    def fake_run(args, **kwargs):
        if args[0] == "taskkill":
            killed.append(args[2])
        return _Result()

    monkeypatch.setattr("browser_automation.subprocess.run", fake_run)
    monkeypatch.setattr("browser_automation.os.name", "nt")
    closed = JobApplicationAssistant._close_leftover_browsers(Path(r"C:\agent\browser_profile"))
    assert closed == 1 and killed == ["11"]


def test_a_disabled_next_button_is_not_clicked(page, agent):
    """Google's Next stays disabled until the step is complete. Clicking it
    anyway spent a 30-second timeout per attempt, and the run looked busy
    while telling the user nothing about what was missing."""
    page.set_content("""<html><body>
        <button disabled aria-label="Next" style="width:90px;height:32px">Next</button>
      </body></html>""")
    assert agent._wizard_button(page) is None
    assert getattr(agent, "_disabled_next", False) is True


def test_an_enabled_next_button_is_used(page, agent):
    page.set_content("""<html><body>
        <button aria-label="Next" style="width:90px;height:32px">Next</button>
      </body></html>""")
    assert agent._wizard_button(page) is not None


def test_a_click_that_never_lands_does_not_end_the_run(page, agent):
    """A Playwright click timeout escaped click_next_step and killed the whole
    run: the browser closed and a Google application the agent had already
    filled two steps of went with it. A form that cannot be advanced is a
    hand-over, with the window left open."""
    page.set_content("""<html><body>
        <input id="answered" value="something">
        <button id="next" aria-label="Next" style="width:90px;height:32px">Next</button>
        <div id="cover" style="position:fixed;inset:0;background:rgba(0,0,0,.2)"></div>
      </body></html>""")
    assert agent.click_next_step(page) is False  # the overlay swallows every click
    assert not page.is_closed()
    assert page.input_value("#answered") == "something"


SHARED_NAME_RADIOS = """
<html><body>
  <div role="radiogroup" aria-label="Gender radio input">
    <label><input type="radio" name="YPqjbf" value="1">Male</label>
    <label><input type="radio" name="YPqjbf" value="2">Female</label>
    <label><input type="radio" name="YPqjbf" value="3">I choose not to disclose</label>
  </div>
  <div role="radiogroup" aria-label="Veteran status radio input">
    <label><input type="radio" name="YPqjbf" value="1">I identify as a protected veteran</label>
    <label><input type="radio" name="YPqjbf" value="2">I am not a protected veteran</label>
    <label><input type="radio" name="YPqjbf" value="3">I choose not to disclose</label>
  </div>
</body></html>
"""


def test_radios_are_grouped_as_the_page_groups_them(page, agent):
    """Google gives every radio on its form the same name ("YPqjbf") and
    separates the questions with role="radiogroup". Keying on the name made
    five questions look like one question with thirteen answers: four went
    unanswered, and an answer could have been ticked on the wrong question."""
    page.set_content(SHARED_NAME_RADIOS)
    groups = agent.radio_groups(page)
    assert [g["question"] for g in groups] == ["Gender radio input", "Veteran status radio input"]
    assert groups[0]["labels"] == ["Male", "Female", "I choose not to disclose"]


def test_an_answer_lands_on_its_own_question(page, agent):
    page.set_content(SHARED_NAME_RADIOS)
    groups = agent.radio_groups(page)
    assert agent.answer_radio_group(page, groups[1], "I am not a protected veteran")
    checked = page.eval_on_selector_all(
        "input[type=radio]", "els => els.map(e => e.checked)")
    assert checked == [False, False, False, False, True, False]


def test_a_question_is_found_again_after_the_page_rebuilds(page, agent):
    """Answering one question on Google's form re-renders the others, throwing
    away the ids their options were given: the second and third questions then
    had nothing left to click, and were reported unanswerable."""
    page.set_content(SHARED_NAME_RADIOS)
    groups = agent.radio_groups(page)
    veteran = groups[1]

    # the page rebuilds its radios, as Google's does after each answer
    page.evaluate("""() => {
        const groups = document.querySelectorAll('[role=radiogroup]');
        for (const g of groups) g.innerHTML = g.innerHTML.replace(/id="[^"]*"/g, '');
    }""")

    assert agent.answer_radio_group(page, veteran, "I am not a protected veteran")
    checked = page.eval_on_selector_all("input[type=radio]", "els => els.map(e => e.checked)")
    assert checked == [False, False, False, False, True, False]


RACE_CHECKBOXES = """
<html><body>
  <div aria-label="Race/Ethnic group">
    <label><input type="checkbox" name="c206" value="1" aria-label="Black or African American">Black or African American</label>
    <label><input type="checkbox" name="c206" value="2" aria-label="Asian">Asian</label>
    <label><input type="checkbox" name="c206" value="3" aria-label="Hispanic or Latino">Hispanic or Latino</label>
  </div>
  <div aria-label="Consent">
    <label><input type="checkbox" id="agree">I certify that the information given is true and complete</label>
  </div>
</body></html>
"""


def test_a_choose_all_that_apply_question_is_answered_from_the_profile(page, agent):
    """Google requires "Please indicate your race / ethnic group (choose all
    that apply)" as checkboxes. Nothing answered a checkbox group, so its Next
    button stayed disabled with no sign of why."""
    from config import get_user_profile

    page.set_content(RACE_CHECKBOXES)
    agent._answer_checkbox_groups_from_profile(page, agent._standard_answer_rules(get_user_profile()))
    assert page.is_checked("input[value='2']")          # Asian, from the profile
    assert not page.is_checked("input[value='1']")


def test_an_attestation_checkbox_is_never_ticked_by_that_pass(page, agent):
    """The certification box is a legal declaration: it is the user's to give,
    and no profile answer names it."""
    from config import get_user_profile

    page.set_content(RACE_CHECKBOXES)
    agent._answer_checkbox_groups_from_profile(page, agent._standard_answer_rules(get_user_profile()))
    assert not page.is_checked("#agree")


MATERIAL_CHECKBOX = """
<html><body>
  <div aria-label="Race/Ethnic group">
    <span class="mdc-checkbox" onclick="this.querySelector('input').checked = true;
         this.querySelector('input').dispatchEvent(new Event('change', {bubbles:true}))"
         style="display:inline-block;width:40px;height:40px">
      <input type="checkbox" aria-label="Asian" style="position:absolute;opacity:0;pointer-events:none">
    </span>
    <span class="mdc-checkbox" style="display:inline-block;width:40px;height:40px">
      <input type="checkbox" aria-label="Hispanic or Latino" style="position:absolute;opacity:0;pointer-events:none">
    </span>
  </div>
</body></html>
"""


def test_an_option_the_input_ignores_is_chosen_through_its_wrapper(page, agent):
    """Google's Material checkbox ignores a click on the input itself --
    "clicking the checkbox did not change its state" -- so the race/ethnicity
    question stayed unanswered and its Next button stayed disabled."""
    page.set_content(MATERIAL_CHECKBOX)
    box = page.query_selector("input[aria-label='Asian']")
    assert agent.select_radio(page, box)
    assert page.eval_on_selector("input[aria-label='Asian']", "e => e.checked")
    assert not page.eval_on_selector("input[aria-label='Hispanic or Latino']", "e => e.checked")


def test_a_screen_reader_announcement_is_not_a_form_error(page, agent):
    """Next.js announces each route change in a clipped one-pixel role=alert
    holding the page title. It was collected as a form error, so an untouched
    Dayforce page reported "form error: Apply | Dayforce Jobs"."""
    page.set_content("""<html><body>
        <p id="__next-route-announcer__" role="alert"
           style="clip:rect(0px,0px,0px,0px);height:1px;width:1px;overflow:hidden">Apply | Dayforce Jobs</p>
        <div role="alert">Enter a valid phone number</div>
      </body></html>""")
    errors = agent.find_required_blanks(page)["errors_shown"]
    assert errors == ["Enter a valid phone number"]


def test_the_chooser_applies_without_an_account(page, agent):
    """Dayforce offers "Apply without an Account", "Sign In" and "Create one
    now". Without an account needs no credentials and creates nothing, so it
    is the one taken; the agent stopped at this page before."""
    page.set_content("""<html><body>
        <button onclick="document.title='chose guest'">Apply without an Account</button>
        <button onclick="document.title='chose sign in'">Sign In</button>
        <a href="#" onclick="document.title='chose create'">Create one now.</a>
      </body></html>""")
    agent.dismiss_apply_chooser(page)
    assert page.title() == "chose guest"


ANT_SELECT = """
<html><body>
  <div class="ant-select">
    <input type="search" id="country" role="combobox" class="ant-select-selection-search-input"
           aria-expanded="false" style="width:200px;height:32px">
  </div>
  <div class="ant-select-dropdown">
    <div class="ant-select-item ant-select-item-option" title="Canada">Canada</div>
    <div class="ant-select-item ant-select-item-option" title="United States">United States</div>
  </div>
</body></html>
"""


def test_an_ant_design_picker_offers_its_options(page, agent):
    """Dayforce builds Country, State and 'How did you hear' with Ant Design,
    which renders its menu in a portal of divs -- not option elements -- so the
    agent saw no options and left four required fields blank."""
    page.set_content(ANT_SELECT)
    options = page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden) "
                           "[class*=ant-select-item-option]:visible")
    assert [t.strip() for t in options.all_inner_texts()] == ["Canada", "United States"]


def test_a_picker_whose_input_cannot_be_clicked_is_opened_by_its_wrapper(page, agent):
    """Ant Design puts a zero-width search input inside the control and covers
    it with the selector div: clicking the input waited the full thirty-second
    timeout and gave up, leaving Country, State and 'How did you hear' blank on
    every pass."""
    page.set_content("""<html><body>
        <div class="ant-select">
          <div class="ant-select-selector" style="width:220px;height:34px"
               onmousedown="document.title='opened'">
            <input type="search" id="country" role="combobox"
                   class="ant-select-selection-search-input"
                   style="width:0;height:0;opacity:0;pointer-events:none">
          </div>
        </div>
      </body></html>""")
    assert agent.open_picker_control(page, page.locator("#country").first)
    assert page.title() == "opened"


def test_an_ant_design_choice_is_read_back(page, agent):
    """Ant Design shows the chosen value in .ant-select-selection-item, so a
    Country the agent had just chosen read back as blank and the field went on
    being reported as still empty."""
    page.set_content("""<html><body>
        <div class="ant-select"><div class="ant-select-selector">
          <span class="ant-select-selection-item" title="United States">United States</span>
          <span class="ant-select-selection-search">
            <input type="search" id="country" class="ant-select-selection-search-input" value="">
          </span>
        </div></div>
      </body></html>""")
    assert agent.displayed_value(page.locator("#country").first) == "United States"
    blanks = agent.find_required_blanks(page)["required_still_blank"]
    assert not [b for b in blanks if "country" in b.lower()]


def test_an_unlisted_referral_source_is_answered_as_other(page, agent):
    """Segra's 'How did you hear about this job?' offers Indeed, LinkedIn,
    Glassdoor, referrals and Other -- no company website, which is what the
    profile says. Where the stated source is not among the options, Other is
    what it actually was."""
    texts = ["Recruitment Agency or Firm", "CareerBuilder", "Indeed", "Linkedin", "Other"]
    assert agent._best_option(texts, ["Company Career Site", "Company Website"]) is None
    assert next(i for i, t in enumerate(texts) if t.lower() == "other") == 4


def test_a_listed_source_is_preferred_over_other(page, agent):
    texts = ["Indeed", "Company Website", "Other"]
    assert agent._best_option(texts, ["Company Career Site", "Company Website"]) == 1


def test_a_picker_that_already_holds_a_choice_is_left_alone(page, agent):
    """The control scan read an Ant picker's own input, which stays empty, so
    Country, State and Preferred contact were chosen again on every pass and a
    run never finished the page -- five passes, twenty seconds apart."""
    from config import get_user_profile

    page.set_content("""<html><body>
        <label for="country">Country</label>
        <div class="ant-select"><div class="ant-select-selector">
          <span class="ant-select-selection-item">United States of America</span>
          <span class="ant-select-selection-search">
            <input id="country" role="combobox" class="ant-select-selection-search-input" value="">
          </span>
        </div></div>
      </body></html>""")
    agent._profile = get_user_profile()
    answered = []
    agent._answer_combobox_from_profile = lambda *a, **k: answered.append(a)
    from sites import SiteAdapter
    agent.adapter = lambda _page: SiteAdapter()
    agent.answer_standard_questions(page, get_user_profile())
    assert answered == []


def test_a_rejected_phone_number_is_rewritten_as_digits(page, agent):
    """Dayforce keeps the country code in a control of its own and wants the
    number as digits, so the profile's "(571) 354-5212" came back as "Home
    Phone number is invalid" on a field the agent had just filled."""
    page.set_content("""<html><body>
        <input id="homePhone" type="tel" value="(571) 354-5212">
        <div role="alert">Home Phone number is invalid</div>
      </body></html>""")
    assert agent.fix_rejected_phone_numbers(page) == 1
    assert page.input_value("#homePhone") == "5713545212"


def test_a_phone_the_form_accepts_is_left_as_it_is(page, agent):
    page.set_content("""<html><body>
        <input id="homePhone" type="tel" value="(571) 354-5212">
        <div role="alert">Please review your application</div>
      </body></html>""")
    assert agent.fix_rejected_phone_numbers(page) == 0
    assert page.input_value("#homePhone") == "(571) 354-5212"


def test_a_section_is_saved_before_the_wizard_is_advanced(page, agent):
    """Dayforce keeps Candidate Info open for editing with an Update button of
    its own, and will not advance while it is open: the agent pressed Next, the
    page ignored it, and the run called that the last step."""
    page.set_content("""<html><body>
        <button onclick="document.title='section saved'">Update</button>
        <button onclick="document.title='advanced'">Next</button>
      </body></html>""")
    assert agent.commit_open_sections(page) == 1
    assert page.title() == "section saved"


def test_a_sections_save_is_never_a_submit(page, agent):
    """'Save and Submit' sends the application; it is not a section's save."""
    page.set_content("""<html><body>
        <button onclick="document.title='SUBMITTED'">Save and Submit</button>
      </body></html>""")
    assert agent.commit_open_sections(page) == 0
    assert page.title() != "SUBMITTED"


def test_a_form_that_will_not_move_on_is_still_ready_once_everything_is_filled():
    """The user's rule: with every mandatory field filled, an application does
    not need reviewing. A wizard that would not advance is said in the notes,
    not held against the application."""
    status, message = safety.handover_status(
        {"required_still_blank": [], "errors_shown": [], "form_reached": True,
         "fields_filled": 12, "documents_attached": 2, "wizard_stuck": True}
    )
    assert status == "ready_to_submit"
    assert "did not move past this step" in message


def test_review_is_still_asked_for_what_only_the_user_can_do():
    for report, expected in (
        ({"required_still_blank": ["City"]}, "a required field"),
        ({"errors_shown": ["Phone is invalid"]}, "an error the form shows"),
        ({"attestations_pending": ["Typed signature"]}, "an attestation"),
        ({"captcha": True}, "a CAPTCHA"),
    ):
        full = {"form_reached": True, "fields_filled": 12, "documents_attached": 1, **report}
        assert safety.handover_status(full)[0] == "needs_user_review", expected


EMPLOYMENT_QUESTIONNAIRE = """
<html><body>
  <label for="q1">Most Recent Employer: Company Name</label>
  <input id="q1" type="text" value="">
  <label for="q2">Type of Business</label>
  <input id="q2" type="text" value="">
  <label for="q3">Dates of Employment</label>
  <input id="q3" type="text" value="">
  <label for="q4">Position Title</label>
  <input id="q4" type="text" value="">
  <label for="q5">Reason for Leaving</label>
  <input id="q5" type="text" value="">
</body></html>
"""


def test_a_questionnaires_employment_questions_are_answered(page, agent):
    """Dayforce's Questionnaire asks for the most recent employer as plain text
    boxes. The profile held none of it, and those rules lived only on the
    picker path, so five required fields were detected, matched nothing, and
    stayed empty -- and the form would not move on."""
    from config import get_user_profile

    page.set_content(EMPLOYMENT_QUESTIONNAIRE)
    agent._profile = get_user_profile()
    agent.adapter = lambda _page: __import__("sites").SiteAdapter()
    agent._answer_text_questions(page, get_user_profile())
    assert page.input_value("#q1") == "Capital One"
    assert page.input_value("#q2") == "Financial Services"
    assert page.input_value("#q3") == "February 2025 - Present"
    assert page.input_value("#q4") == "Senior Network and Security Engineer"
    assert page.input_value("#q5") == "Currently employed"


BAMBOO_MENU = """
<html><body>
  <div class="fab-FormField">
    <label>State&#8201;*</label>
    <button data-menu-id="fab-menu46" aria-haspopup="true" aria-expanded="false"
            aria-label="State &#8211;Select&#8211;" style="width:180px;height:32px">&#8211;Select&#8211;</button>
  </div>
  <div class="fab-FormField">
    <label>Country&#8201;*</label>
    <button data-menu-id="fab-menu28" aria-haspopup="true" aria-expanded="false"
            aria-label="Country United States" style="width:180px;height:32px">United States</button>
  </div>
  <select aria-hidden="true" name="state.value" style="height:0;opacity:0"><option value=""></option></select>
  <label for="addr">Address *</label><input id="addr" type="text" value="">
</body></html>
"""


def test_a_menu_button_is_read_as_the_question_and_its_answer(page, agent):
    """BambooHR builds State and Country as menu buttons over a hidden select
    of zero height, so the agent read the select, found one empty option --
    "no option for 'State' among ['']" -- and left two required fields."""
    page.set_content(BAMBOO_MENU)
    from config import get_user_profile

    agent._profile = get_user_profile()
    seen = []
    agent._answer_combobox_from_profile = lambda page, control, candidates: seen.append(
        (control["question"], control["value"], candidates[0]))
    agent.adapter = lambda _page: __import__("sites").SiteAdapter()
    agent.answer_standard_questions(page, get_user_profile())

    asked = {q.replace(" ", " ").replace("*", "").strip() for q, _value, _c in seen}
    assert "State" in asked                      # unanswered, so it is answered
    assert "Country" not in asked                # already says United States


def test_an_address_field_is_filled_from_the_profile(page, agent):
    from config import get_user_profile

    page.set_content(BAMBOO_MENU)
    agent._profile = get_user_profile()
    agent.adapter = lambda _page: __import__("sites").SiteAdapter()
    agent._answer_text_questions(page, get_user_profile())
    assert page.input_value("#addr") == "9365 Lee Hwy"


ADP_SOCIAL = """
<html><body>
  <label for="email">Email Address</label><input id="email" type="email">
  <button>Continue</button>
  <div>Or sign in using social media</div>
  <div class="icons-section">
    <sdf-icon-button id="linkedin" label="linkedin"><a href="#li"><img src="/assets/icons/linkedin.svg" alt="LinkedIn" width="40" height="40"></a></sdf-icon-button>
    <sdf-icon-button id="google" label="google"><a href="#g"><img src="/assets/icons/google.svg" alt="Google" width="40" height="40"></a></sdf-icon-button>
    <sdf-icon-button id="indeed" label="indeed"><a href="#in"><img src="/assets/icons/indeed.svg" alt="Indeed" width="40" height="40"></a></sdf-icon-button>
  </div>
</body></html>
"""


def test_google_sign_in_drawn_as_an_icon_is_found(page, agent):
    """ADP offers Google as a bare icon under "Or sign in using social media".
    A search for the words "Sign in with Google" found nothing, so the run
    filled the email box and stopped, though the user asked for Google."""
    page.set_content(ADP_SOCIAL)
    found = agent.find_google_sign_in(page)
    assert found is not None
    assert "google" in (found.evaluate("e => e.outerHTML") or "").lower()
    assert "linkedin" not in (found.evaluate("e => e.outerHTML") or "").lower()


def test_no_google_option_means_nothing_is_clicked(page, agent):
    page.set_content(ADP_SOCIAL.replace('id="google" label="google"', 'id="x"')
                     .replace('google.svg', 'x.svg').replace('alt="Google"', 'alt="X"'))
    assert agent.find_google_sign_in(page) is None


SHADOW_PRIVACY_DIALOG = """
<html><body>
  <sdf-dialog id="host">
    <h2>Employer Privacy Policy</h2>
    <p>Applicant Data Privacy Statement. Casey's needs to collect, use, retain and share
       your personal information to administer the employment relationship.</p>
  </sdf-dialog>
  <script>
    const host = document.getElementById('host');
    const root = host.attachShadow({mode: 'open'});
    root.innerHTML = `
      <div role="dialog" aria-modal="true" style="width:400px;height:200px">
        <slot></slot>
        <div role="button" tabindex="0" id="disagree">Disagree</div>
        <div role="button" tabindex="0" id="agree"
             onclick="document.title='agreed'; this.getRootNode().host.remove()">Agree</div>
      </div>`;
  </script>
</body></html>
"""


def test_a_privacy_pop_up_drawn_in_shadow_dom_is_agreed_to(page, agent):
    """ADP draws its Employer Privacy Policy pop-up inside a web component: the
    words are slotted in from outside the shadow root and Agree is not a
    <button>. The agent read the dialog as just "Disagree Agree", never saw a
    privacy prompt, and left it for the user though the profile allows it."""
    page.set_content(SHADOW_PRIVACY_DIALOG)
    assert agent.accept_consent_dialog(page)
    assert page.title() == "agreed"


def test_a_pop_up_asking_to_certify_is_still_left_alone(page, agent):
    page.set_content(SHADOW_PRIVACY_DIALOG.replace(
        "Casey's needs to collect",
        "I certify under penalty of perjury that my privacy information is true. Casey's needs to collect"))
    assert not agent.accept_consent_dialog(page)
    assert page.title() != "agreed"


def test_a_next_drawn_as_a_plain_box_is_found(page, agent):
    """Casey's older ADP pages draw Next into a <div class="appGo"> after the
    page loads. A search for buttons and links found nothing, so the run took
    step one of two for the end and called the application ready."""
    page.set_content("""<html><body>
        <input type="text" value="filled">
        <div class="appGo center" style="cursor:pointer;width:90px;height:30px"
             onclick="document.title='next step'">Next</div>
      </body></html>""")
    control = agent._wizard_button(page)
    assert control is not None
    control.click()
    assert page.title() == "next step"


def test_plain_text_saying_next_is_not_mistaken_for_a_button(page, agent):
    page.set_content("""<html><body><p>Next</p></body></html>""")
    assert agent._wizard_button(page) is None


def test_a_next_page_arrow_with_no_words_is_found(page, agent):
    """Casey's ADP pages draw Next as a box whose only label is a right-arrow
    icon, put there with CSS. Nothing on the page read "Next", so the run
    stopped on step three of five."""
    page.set_content("""<html><head><style>
        .appGo { cursor:pointer; width:60px; height:40px; display:inline-block; }
        .appGo::after { content: "\\f061"; font-family: FontAwesome; }
      </style></head><body>
        <input type="text" value="filled">
        <div class="appGo center" onclick="document.title='next page'"></div>
      </body></html>""")
    control = agent._wizard_button(page)
    assert control is not None
    control.click()
    assert page.title() == "next page"
