"""HonorHealth (Workday), 25 September: the agent recorded the application as SUBMITTED
("Already submitted on site -- confirmed by portal") while it was still on the create-account page.

The page says "New Candidate: Thank you for your interest in a career with HonorHealth. Please
create an account ...". The agent's confirmation pattern took "thank you for your interest" for a
confirmation, and a false "submitted" record also makes the agent refuse to apply for that job again.

The class: a success judged from loose wording on a page that is not a confirmation. A confirmation
is a page that says so in words only a confirmation uses, and that no longer asks for anything.
"""
from types import SimpleNamespace

import pytest

import config
import page_agent
import safety
from browser_automation import JobApplicationAssistant

CREATE_ACCOUNT = """<html><body><h1>Create Account</h1>
<p><b>Current Employees:</b> Please DO NOT apply here.</p>
<p><b>New Candidate:</b> Thank you for your interest in a career with HonorHealth. Please create an
account with your email address and desired password.</p>
<label for="e">Email Address*</label><input id="e" type="email">
<label for="p">Password*</label><input id="p" type="password">
<label for="p2">Verify New Password*</label><input id="p2" type="password">
<button>Create Account</button></body></html>"""


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
    job = SimpleNamespace(title="Engineer", company="Example", url="https://jobs.example.com/apply")
    return page_agent.PageAgent(assistant, planner or SimpleNamespace(), cfg, config.UserProfile(email="o@example.com"),
                                SimpleNamespace(raw_text="x"), job, resume_file=None)


# --- the wording ----------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Thank you for your interest in a career with HonorHealth.",
    "Thank you for your interest in this position",
    "Thanks for your interest in joining our team!",
    "We appreciate your interest. Please create an account to continue.",
    "Your application will be submitted once you have reviewed it",
])
def test_ordinary_politeness_is_not_a_confirmation(text):
    assert not page_agent.CONFIRMATION_TEXT.search(text)


@pytest.mark.parametrize("text", [
    "Thank you for applying!",
    "Thank you for your application",
    "Your application has been submitted",
    "Application received",
    "We have received your application",
    "We've received your application.",
    "Your application has been sent",
    "Your application is complete",
    "You've already applied to this job",
    "You have applied for this position",
])
def test_a_confirmations_own_words_still_count(text):
    assert page_agent.CONFIRMATION_TEXT.search(text)


# --- the page ---------------------------------------------------------------------------------

def test_a_create_account_page_that_thanks_the_candidate_is_not_a_confirmation(page):
    page.set_content(CREATE_ACCOUNT)
    assert make_agent()._site_confirms(page) is False


@pytest.mark.parametrize("body", [
    "<h1>Thank you for applying</h1><p>Please complete the form below.</p>"
    '<input placeholder="First"><input placeholder="Last"><input placeholder="Email">',
    '<p>You have applied before? Sign in.</p><input type="password">',
])
def test_a_page_that_still_asks_for_something_is_not_a_confirmation(page, body):
    page.set_content(f"<html><body>{body}</body></html>")
    assert make_agent()._site_confirms(page) is False


@pytest.mark.parametrize("body", [
    "<h1>Thank you for applying!</h1><p>We have received your application.</p>",
    "<h1>Application received</h1><p>We will be in touch.</p><input type='search' placeholder='Search jobs'>",
    "<p>You've already applied to this job.</p><button>View my applications</button>",
])
def test_a_page_that_confirms_and_asks_for_nothing_is_a_confirmation(page, body):
    page.set_content(f"<html><body>{body}</body></html>")
    assert make_agent()._site_confirms(page) is True


def test_the_form_is_looked_for_inside_frames_too(page):
    form = "<body><h1>Thank you for applying</h1><input id=a><input id=b><input id=c></body>"
    page.route("https://jobs.example.com/**", lambda route: route.fulfill(
        status=200, content_type="text/html",
        body='<html><body><iframe src="https://jobs.example.com/inner" width="400" height="300"></iframe></body></html>'
        if route.request.url.endswith("/outer") else form))
    page.goto("https://jobs.example.com/outer")
    page.wait_for_timeout(500)
    assert make_agent()._site_confirms(page) is False


# --- the run ----------------------------------------------------------------------------------

class ReadAgain:
    def plan_page(self, snapshot, facts, feedback=""):
        return {"page_kind": "other", "step": "", "answers": [], "for_owner": [], "mismatches": [],
                "next": {"ref": "", "label": "", "kind": "none"}}


def test_the_run_never_reports_an_application_already_sent_from_a_create_account_page(page):
    page.set_content(CREATE_ACCOUNT)
    agent = make_agent(ReadAgain())
    outcome = agent.run(page)
    assert "already sent" not in " ".join(outcome.reasons + [outcome.summary or ""]).lower()
    assert outcome.kind != "submitted"
