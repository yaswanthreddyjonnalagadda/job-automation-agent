"""Getting to the form and through it when the page's code doesn't say how.

Charles Schwab's application (iCIMS) sat on the job posting for a whole run:
the posting and the form live inside an embedded frame the agent never looked
in, and the run had been resumed at the posting's address, so Apply was never
pressed. These cover the three things added for it -- opening a frame, telling
a posting from the form, and looking at a screenshot when the rules find no way
forward -- and the limits on what the agent will click on Claude's say-so.
"""

import json
from types import SimpleNamespace

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


POSTING = """
<html><body>
  <h1>Senior Site Reliability Engineer</h1>
  <input type="search" placeholder="Search jobs">
  <h2>Responsibilities</h2><p>Keep things running.</p>
  <h2>Qualifications</h2><p>Six years.</p>
  <a href="/jobs/126880/login">Apply for this job online</a>
</body></html>
"""

FORM = """
<html><body>
  <h1>Your details</h1>
  <label>First name <input name="first"></label>
  <label>Last name <input name="last"></label>
  <label>Email <input name="email" type="email"></label>
  <label>Phone <input name="phone" type="tel"></label>
  <p>Requirements for this job are listed on the posting.</p>
  <button type="button">Next</button>
</body></html>
"""

REVIEW_WITH_APPLY = """
<html><body>
  <h1>Review your application</h1>
  <p>Job requisition 126880 -- Qualifications confirmed.</p>
  <button type="submit">Apply</button>
</body></html>
"""


def serve(page, pages: dict[str, str]):
    def handler(route):
        path = "/" + route.request.url.split("/", 3)[3].split("?")[0]
        body = pages.get(path)
        if body is None:
            route.fulfill(status=404, body="not found")
        else:
            route.fulfill(status=200, content_type="text/html", body=body)
    page.route("https://career-schwab.icims.com/**", handler)
    page.route("https://ads.example.net/**", handler)


# --- telling the posting from the form ---------------------------------------

def test_a_posting_is_recognised(page, agent):
    page.set_content(POSTING)
    assert agent.on_job_description(page)


def test_a_form_is_not_a_posting(page, agent):
    page.set_content(FORM)
    assert not agent.on_job_description(page)


def test_a_review_page_whose_submit_says_apply_is_not_a_posting(page, agent):
    page.set_content(REVIEW_WITH_APPLY)
    assert not agent.on_job_description(page)


def test_nothing_is_a_posting_once_the_agent_has_filled_the_form(page, agent):
    page.set_content(POSTING)
    agent._form_filled_this_run = True
    assert not agent.on_job_description(page)


# --- forms inside frames ------------------------------------------------------

WRAPPER = ('<html><body><h1>Careers</h1>'
           '<iframe id="icims_content_iframe" name="icims_content_iframe" src="/jobs/126880/job-frame?in_iframe=1"'
           ' width="900" height="600"></iframe></body></html>')

POSTING_FRAME = POSTING.replace(
    '<a href="/jobs/126880/login">', '<a href="/jobs/126880/login-frame?in_iframe=1">')


def test_the_sites_own_frame_is_worked_inside(page, agent):
    # iCIMS sends a frame opened on its own straight back to its outer page,
    # so the agent works inside the frame instead.
    serve(page, {"/jobs/126880/job": WRAPPER, "/jobs/126880/job-frame": FORM})
    page.goto("https://career-schwab.icims.com/jobs/126880/job")
    framed = agent.open_embedded_form(page)
    assert framed is not page and framed.top is page
    assert page.url.endswith("/jobs/126880/job")  # the tab stayed where it was
    assert "job-frame" in framed.url
    assert framed.locator("input[name=first]").count() == 1
    assert agent.visible_input_count(framed) == 4
    assert framed.keyboard is page.keyboard  # typing and screenshots belong to the tab


def test_apply_inside_the_frame_leads_to_the_next_step_inside_it(page, agent):
    serve(page, {"/jobs/126880/job": WRAPPER, "/jobs/126880/job-frame": POSTING_FRAME,
                 "/jobs/126880/login-frame": FORM})
    page.goto("https://career-schwab.icims.com/jobs/126880/job")
    framed = agent.open_embedded_form(page)
    assert agent.on_job_description(framed)
    framed = agent.click_apply_button(framed)
    framed.wait_for_timeout(500)
    assert "login-frame" in framed.url
    assert agent.visible_input_count(framed) == 4
    assert agent.open_embedded_form(framed) is framed  # already inside; nothing to change


def test_the_frame_is_found_again_when_the_site_replaces_it(page, agent):
    serve(page, {"/jobs/126880/job": WRAPPER, "/jobs/126880/job-frame": FORM})
    page.goto("https://career-schwab.icims.com/jobs/126880/job")
    framed = agent.open_embedded_form(page)
    page.goto("https://career-schwab.icims.com/jobs/126880/job")  # a new outer page, a new frame
    page.wait_for_timeout(300)
    assert framed.locator("input[name=first]").count() == 1


def test_an_application_system_framed_on_another_site_is_opened_on_its_own(page, agent):
    def handler(route):
        url = route.request.url
        body = ('<html><body><h1>Careers</h1><iframe src="https://boards.greenhouse.io/embed/job_app?token=1">'
                '</iframe></body></html>') if "jumptrading" in url else FORM
        route.fulfill(status=200, content_type="text/html", body=body)
    page.route("https://www.jumptrading.com/**", handler)
    page.route("https://boards.greenhouse.io/**", handler)
    page.goto("https://www.jumptrading.com/hr/job?gh_jid=1")
    assert agent.open_embedded_form(page) is page
    assert page.url.startswith("https://boards.greenhouse.io/")


def test_an_advert_frame_is_not_followed(page, agent):
    serve(page, {
        "/jobs/126880/job": '<html><body><h1>Careers</h1>'
                            '<iframe src="https://ads.example.net/signup"></iframe></body></html>',
        "/signup": '<html><body><input name="email"><button>Join</button></body></html>',
    })
    page.goto("https://career-schwab.icims.com/jobs/126880/job")
    assert agent.open_embedded_form(page) is page
    assert page.url.endswith("/jobs/126880/job")


def test_a_page_that_is_already_the_form_stays_put(page, agent):
    serve(page, {"/form": FORM.replace("</body>", '<iframe src="/jobs/1/job-frame"></iframe></body>'),
                 "/jobs/1/job-frame": FORM})
    page.goto("https://career-schwab.icims.com/form")
    assert agent.open_embedded_form(page) is page


# --- what the agent will click on Claude's say-so -----------------------------

@pytest.mark.parametrize("label", [
    "Submit Application", "Submit", "Send application", "Finish", "I certify the above is true",
    "Apply with LinkedIn", "Sign in with Indeed", "Continue with Facebook", "Withdraw application",
    "Delete", "Sign out", "E-Sign",
])
def test_never_clicked_on_claudes_say_so(page, agent, label):
    page.set_content(FORM)
    assert not agent.safe_to_click_for_claude(page, label)


@pytest.mark.parametrize("label", [
    "Next", "Continue", "Sign in with Google", "Apply without an account", "Apply Manually", "Start",
])
def test_ordinary_steps_are_allowed(page, agent, label):
    page.set_content(FORM)
    assert agent.safe_to_click_for_claude(page, label)


def test_apply_is_allowed_only_from_a_posting(page, agent):
    page.set_content(POSTING)
    assert agent.safe_to_click_for_claude(page, "Apply")
    page.set_content(REVIEW_WITH_APPLY)
    assert not agent.safe_to_click_for_claude(page, "Apply")


class FakeClaude:
    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def read_page(self, screenshot_png, page_url, goal):
        assert screenshot_png[:4] == b"\x89PNG"
        self.calls += 1
        return self.answer


def test_looking_clicks_what_claude_points_at(page, agent):
    page.set_content("""
      <html><body><h1>Welcome back</h1>
        <div role="button" tabindex="0" onclick="document.body.dataset.went='yes'">Continue to application</div>
      </body></html>""")
    kind, clicked = agent.look_and_act(page, FakeClaude(
        {"page": "chooser", "click": "Continue to application", "why": "the only way on"}), "apply")
    assert clicked and kind == "chooser"
    assert page.evaluate("document.body.dataset.went") == "yes"


def test_looking_never_presses_submit_even_if_claude_says_so(page, agent):
    page.set_content("""
      <html><body>
        <button onclick="document.body.dataset.sent='yes'">Submit Application</button>
      </body></html>""")
    kind, clicked = agent.look_and_act(page, FakeClaude(
        {"page": "application_form", "click": "Submit Application", "why": "done"}), "apply")
    assert not clicked
    assert page.evaluate("document.body.dataset.sent") is None


def test_looking_skips_a_submit_button_matched_by_a_harmless_label(page, agent):
    # Claude says "Next"; the only control whose name contains it is a Submit.
    page.set_content("""
      <html><body>
        <button onclick="document.body.dataset.sent='yes'">Submit and go Next</button>
      </body></html>""")
    _, clicked = agent.look_and_act(page, FakeClaude(
        {"page": "application_form", "click": "Next", "why": "move on"}), "apply")
    assert not clicked
    assert page.evaluate("document.body.dataset.sent") is None


def test_nothing_is_clicked_when_claude_sees_a_form_to_fill(page, agent):
    page.set_content(FORM)
    fake = FakeClaude({"page": "application_form", "click": "", "why": "fields waiting"})
    assert agent.look_and_act(page, fake, "apply") == ("application_form", False)


# --- the Claude call itself ---------------------------------------------------

def test_read_page_sends_the_screenshot_and_reads_the_answer():
    from claude_integration import ClaudeClient
    sent = {}

    def create(**kwargs):
        sent.update(kwargs)
        text = json.dumps({"page": "job_description", "click": "Apply", "why": "a posting"})
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])

    client = ClaudeClient.__new__(ClaudeClient)
    client._config = SimpleNamespace(claude_max_retries=1, anthropic_model="m", claude_request_timeout=5)
    client._client = SimpleNamespace(messages=SimpleNamespace(create=create))
    result = client.read_page(b"\x89PNG....", "https://career-schwab.icims.com/jobs/126880/job", "apply")
    assert result == {"page": "job_description", "click": "Apply", "why": "a posting"}
    blocks = sent["messages"][0]["content"]
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["media_type"] == "image/png"
    assert "never choose a control that submits" in sent["system"]


# --- Schwab's Privacy Notice and Sign-In step (iCIMS) ---------------------------

ICIMS_SIGN_IN = """
<html><body>
<form id="enterEmailForm" onsubmit="document.body.dataset.sent='yes'; return false;">
  <label for="email">Email</label>
  <input type="email" autocomplete="email" id="email" name="css_loginName" value="">
  <div class="country-phone-group">
    <div class="country-code">
      <label for="countryCode">Phone Country Code</label>
      <div class="country-code iCIMS_InfoData">
        <input type="text" id="selectedCountryCode" name="countryCodeSelect" style="display:none">
        <a id="dropdown" class="dropdown-select" role="combobox" aria-controls="dropdownOptions" tabindex="0"
           aria-label="Country Code - Make a Selection -"
           onclick="document.getElementById('dropdownOptions').hidden = !document.getElementById('dropdownOptions').hidden">
          <span id="shown">- Make a Selection -</span></a>
        <div id="dropdownOptions" hidden>
          <input type="text" class="dropdown-search" id="countryCode" placeholder="- Type to Search -"
                 oninput="for (const li of document.querySelectorAll('#dropdownResults li'))
                            li.hidden = !li.textContent.toLowerCase().includes(this.value.toLowerCase())">
          <ul id="dropdownResults" role="listbox">
            <li role="option" value="AF"> (+93) Afghanistan</li>
            <li role="option" value="UM"> (+1) United States Minor Outlying Islands</li>
            <li role="option" value="US"> (+1) United States</li>
            <li role="option" value="VI"> (+1340) Virgin Islands (US)</li>
          </ul>
        </div>
      </div>
    </div>
    <label for="phoneNumber">Number</label>
    <input type="text" autocomplete="tel-national" id="phoneNumber" name="css_phoneNumber" value="">
  </div>
  <input id="enterEmailSubmitButton" type="submit" value="I Acknowledge the Privacy Notice">
</form>
<script>
  for (const li of document.querySelectorAll('#dropdownResults li'))
    li.addEventListener('click', () => {
      document.getElementById('shown').textContent = li.textContent.trim();
      document.getElementById('selectedCountryCode').value = li.getAttribute('value');
      document.getElementById('dropdownOptions').hidden = true;
    });
</script>
</body></html>
"""


def test_a_phone_box_labelled_only_number_is_known_by_its_autofill_hint(page, agent):
    page.set_content(ICIMS_SIGN_IN)
    fields = {f.selector: f.matched_profile_key for f in agent.detect_form_fields(page)}
    assert fields['[id="phoneNumber"]'] == "phone"
    assert fields['[id="email"]'] == "email"


def test_the_country_code_list_is_set_to_the_united_states(page, agent):
    page.set_content(ICIMS_SIGN_IN)
    profile = SimpleNamespace(phone_country_code="+1", country="United States")
    assert agent.set_phone_country(page, profile) == 1
    assert page.locator("#shown").inner_text() == "(+1) United States"
    assert page.locator("#selectedCountryCode").input_value() == "US"


def test_a_country_code_already_chosen_is_left_alone(page, agent):
    page.set_content(ICIMS_SIGN_IN.replace(">- Make a Selection -<", ">(+91) India<"))
    assert agent.set_phone_country(page, SimpleNamespace(phone_country_code="+1", country="United States")) == 0
    assert page.locator("#shown").inner_text() == "(+91) India"


def test_the_privacy_acknowledgement_is_the_way_on(page, agent):
    page.set_content(ICIMS_SIGN_IN)
    agent._profile = SimpleNamespace(accept_application_privacy_prompts=True)
    button = agent._wizard_button(page)
    assert button is not None and button.get_attribute("value") == "I Acknowledge the Privacy Notice"


def test_the_privacy_acknowledgement_waits_when_the_owner_has_not_allowed_it(page, agent):
    page.set_content(ICIMS_SIGN_IN)
    agent._profile = SimpleNamespace(accept_application_privacy_prompts=False)
    assert agent._wizard_button(page) is None


def test_a_legal_declaration_is_never_taken_for_a_privacy_acknowledgement(page, agent):
    page.set_content(ICIMS_SIGN_IN.replace(
        'value="I Acknowledge the Privacy Notice"',
        'value="I acknowledge the privacy notice and certify my answers are true and complete"'))
    agent._profile = SimpleNamespace(accept_application_privacy_prompts=True)
    assert agent._wizard_button(page) is None


# --- a CAPTCHA is never touched -------------------------------------------------
# On Schwab's sign-in, pressing the privacy acknowledgement raised an hCaptcha
# picture puzzle. The screenshot was read as a sign-in prompt and the puzzle's
# own "Skip" was clicked.

HCAPTCHA = ("https://newassets.hcaptcha.com/captcha/v1/static/hcaptcha.html#frame=challenge")
PUZZLE = ("<html><body><div>Select all images with a ball</div>"
          "<button onclick=\"parent.document.body.dataset.skipped='yes'\">Skip</button></body></html>")


def serve_captcha(page, size: int):
    page.route("https://newassets.hcaptcha.com/**",
               lambda route: route.fulfill(status=200, content_type="text/html", body=PUZZLE))
    page.route("https://career-schwab.icims.com/**", lambda route: route.fulfill(
        status=200, content_type="text/html",
        body=f'<html><body><h1>Privacy Notice and Sign-In</h1>'
             f'<iframe src="{HCAPTCHA}" width="{size}" height="{size}"></iframe></body></html>'))
    page.goto("https://career-schwab.icims.com/jobs/126880/login?in_iframe=1")
    page.wait_for_timeout(300)


def test_nothing_is_done_while_a_captcha_is_showing(page, agent):
    serve_captcha(page, 400)
    fake = FakeClaude({"page": "sign_in", "click": "Skip", "why": "skip the login prompt"})
    assert agent.look_and_act(page, fake, "apply") == ("captcha", False)
    assert fake.calls == 0  # not even looked at
    assert page.evaluate("document.body.dataset.skipped") is None


def test_nothing_inside_a_captcha_frame_is_ever_clicked(page, agent):
    # Too small to count as showing -- its controls are still never clicked.
    serve_captcha(page, 40)
    fake = FakeClaude({"page": "sign_in", "click": "Skip", "why": "skip the login prompt"})
    _, clicked = agent.look_and_act(page, fake, "apply")
    assert not clicked
    assert page.evaluate("document.body.dataset.skipped") is None


def test_a_page_claude_calls_a_captcha_is_left_alone(page, agent):
    page.set_content("<button onclick=\"document.body.dataset.skipped='yes'\">Skip</button>")
    fake = FakeClaude({"page": "captcha", "click": "Skip", "why": "a puzzle"})
    assert agent.look_and_act(page, fake, "apply") == ("captcha", False)
    assert page.evaluate("document.body.dataset.skipped") is None


def test_a_captcha_stops_the_wizard(page):
    import apply_flow
    serve_captcha(page, 400)

    class Assistant:
        def is_review_step(self, page):
            raise AssertionError("a CAPTCHA decides before anything else")

    assert apply_flow.decide_next_step(Assistant(), page, 1, {}, False) == "stop"


def test_claude_is_told_to_leave_a_captcha_alone():
    import inspect
    from claude_integration import ClaudeClient
    source = inspect.getsource(ClaudeClient.read_page)
    assert '"captcha"' in source and "never " in source and "Skip" in source
