"""HonorHealth (Workday), 25 September -- two things found while the agent created an account.

1. The page text the agent reads (the accessibility snapshot) carries what is typed into every box,
   password boxes included. It was written to output/<job>/pages/page_NN.txt, to the file the session
   brain reads, and -- on runs that used the API -- sent to the API with every page. A secret in a
   snapshot is a secret in a file and on the wire. Box values that are secrets now read "[hidden]".

2. The agent logged ACCOUNT_CREATED and saved the account as existing because Workday showed its Sign In
   page after Create Account. A Sign In page is not proof an account exists: it is what the site shows
   when the account was made, when it was not, and when it needs verifying. It was then rejected at
   sign-in ("wrong email or password"), with nothing to say which of the three it was.
"""
import json
import re
from types import SimpleNamespace

import pytest

import config
import page_agent
import perception
import safety
from browser_automation import JobApplicationAssistant
from sites.base import SiteAdapter
from sites.workday import WorkdayAdapter

SECRET = "hunter2-Xq9"


# --- 1. secrets in snapshots ---------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "Password", "Verify New Password", "Confirm password", "Current Password", "New password *",
    "Passcode", "One-time passcode", "Verification code", "Security code", "PIN", "OTP", "Enter your OTP",
    "Secret answer",
])
def test_a_secret_boxs_value_is_hidden_wherever_the_page_puts_it(name):
    inline = f'''- generic [ref=e1]:
  - textbox "{name}" [active] [ref=e2]: {SECRET}
  - button "Sign In" [ref=e3]'''
    child = f'''- generic [ref=e1]:
  - textbox "{name}" [ref=e2]:
    - /placeholder: Enter it
    - text: {SECRET}
  - button "Sign In" [ref=e3]'''
    for shape in (inline, child):
        out = perception.hide_secrets(shape)
        assert SECRET not in out
        assert "[hidden]" in out
        assert 'button "Sign In" [ref=e3]' in out                       # the rest is untouched


def test_a_name_with_a_colon_is_still_a_secret_box():
    snap = f'''- 'textbox "Password: at least 8 characters" [ref=e2]': {SECRET}'''
    assert SECRET not in perception.hide_secrets(snap)


@pytest.mark.parametrize("line", [
    '- textbox "Email Address" [ref=e2]: jane@example.com',
    '- textbox "Mobile Number" [ref=e3]: (571) 555-0100',
    '- textbox "Enter website. This input is for robots only" [ref=e4]',
    '- textbox "First Name" [ref=e5]: Jane',
    '- combobox "Country" [ref=e6]: United States',
    '- textbox "Compass heading" [ref=e7]: north',
    '- textbox "Spinning tops" [ref=e8]: three',
    '- heading "Reset your password" [level=2] [ref=e9]',
    '- paragraph [ref=e10]: Passwords must be at least 8 characters',
])
def test_nothing_else_is_touched(line):
    assert perception.hide_secrets(line) == line


def test_a_child_line_after_a_secret_box_is_only_hidden_inside_that_box():
    snap = f'''- textbox "Password" [ref=e2]:
  - text: {SECRET}
- textbox "Email" [ref=e3]:
  - text: jane@example.com'''
    out = perception.hide_secrets(snap)
    assert SECRET not in out and "jane@example.com" in out


def test_a_hidden_value_still_reads_as_an_answer_and_the_page_still_parses():
    snap = f'''- textbox "Password" [ref=e2]: {SECRET}
- textbox "Email" [ref=e3]: jane@example.com
- button "Sign In" [ref=e4]'''
    controls = {c.name: c for c in page_agent.parse_snapshot(perception.hide_secrets(snap))}
    assert controls["Password"].answer and controls["Email"].answer == "jane@example.com"
    assert controls["Sign In"].role == "button"


def test_the_forensic_tree_hides_the_same_values():
    tree = {"role": "WebArea", "children": [
        {"role": "textbox", "name": "Password", "value": SECRET},
        {"role": "textbox", "name": "Email", "value": "jane@example.com"}]}
    out = json.dumps(perception.hide_secrets_in_tree(tree))
    assert SECRET not in out and "jane@example.com" in out


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


def make_agent(job_dir=None):
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.values = safety.AgentValues()
    cfg = SimpleNamespace(auto_submit=False, ats_email="")
    job = SimpleNamespace(title="Engineer", company="Example", url="https://jobs.example.com/apply")
    return page_agent.PageAgent(assistant, SimpleNamespace(), cfg, config.UserProfile(email="o@example.com"),
                                SimpleNamespace(raw_text="x"), job, resume_file=None, job_dir=job_dir)


@pytest.mark.parametrize("attrs", ["", 'placeholder="Password"', 'placeholder=""'])
def test_what_the_agent_reads_saves_and_hands_over_never_holds_a_typed_password(page, tmp_path, attrs):
    page.set_content(f'<label for="p">Password*</label><input id="p" type="password" {attrs}>'
                     '<label for="e">Email*</label><input id="e" type="email">')
    page.locator("#p").fill(SECRET)
    page.locator("#e").fill("jane@example.com")
    agent = make_agent(job_dir=tmp_path)
    snapshot = agent.snapshot(page)
    assert SECRET not in snapshot and "jane@example.com" in snapshot
    agent.pages_read = 1
    agent._save(snapshot)
    assert SECRET not in (tmp_path / "pages" / "page_01.txt").read_text(encoding="utf-8")
    assert SECRET not in page_agent.compact_snapshot(snapshot)


# --- 2. an account is created only on evidence ----------------------------------------------------

def test_a_sign_in_page_is_not_proof_that_an_account_was_created(page):
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.adapter = lambda _page: WorkdayAdapter()
    page.set_content("<h2>Sign In</h2><form data-automation-id='signInForm'>"
                     "<input type='email'><input type='password'><button>Sign In</button></form>")
    assert not assistant._account_creation_is_confirmed(page)


def test_a_login_address_is_not_proof_either(page):
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.adapter = lambda _page: WorkdayAdapter()
    page.route("https://tenant.wd1.myworkdayjobs.com/**", lambda route: route.fulfill(
        status=200, content_type="text/html", body="<h2>Sign In</h2><input type='password'>"))
    page.goto("https://tenant.wd1.myworkdayjobs.com/en-US/careers/login?redirect=x")
    assert not assistant._account_creation_is_confirmed(page)


def test_candidate_home_and_the_application_still_are(page):
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant.adapter = lambda _page: WorkdayAdapter()
    page.set_content("<h1>Candidate Home</h1><p>My Applications</p>")
    assert assistant._account_creation_is_confirmed(page)
    page.set_content('<div data-automation-id="progressBarActiveStep">My Experience</div>')
    assert assistant._account_creation_is_confirmed(page)


CREATE = """<html><body><h1>Create Account</h1>
<label for="email">Email Address*</label><input id="email" type="email">
<label for="pw">Password*</label><input id="pw" type="password">
<label for="pw2">Verify New Password*</label><input id="pw2" type="password">
<input type="checkbox" id="agree"><label for="agree">I agree to creating this account to apply for positions</label>
<button id="create">Create Account</button>
<script>document.getElementById('create').addEventListener('click', () => {
  document.body.innerHTML = '<h2>Sign In</h2><form data-automation-id="signInForm"><input type="email">' +
    '<input type="password"><button>Sign In</button></form>'; });</script></body></html>"""


def test_landing_on_sign_in_after_create_is_reported_unverified_and_not_recorded(page, monkeypatch, caplog):
    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a.values = safety.AgentValues()
    a._profile = None
    a.adapter = lambda _page: WorkdayAdapter()
    remembered = []
    a.remember_account = lambda *args, **kwargs: remembered.append(args)
    monkeypatch.setattr(JobApplicationAssistant, "_read_ats_password", staticmethod(lambda: "s3cret-ATS"))
    page.route("https://careers.example-ats.com/**", lambda route: route.fulfill(
        status=200, content_type="text/html", body=CREATE))
    page.goto("https://careers.example-ats.com/en-US/careers/apply")
    caplog.set_level("INFO")
    a.fill_create_account_form(page, "owner@example.com")
    assert remembered == []                                             # nothing was saved as an account
    assert "ACCOUNT_CREATED" not in caplog.text and "ACCOUNT_UNVERIFIED" in caplog.text
