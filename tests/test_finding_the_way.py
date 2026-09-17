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
