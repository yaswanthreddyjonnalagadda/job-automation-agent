"""An employer site that says an account already exists for the owner's email.

The owner's rule: sign in with the existing ATS_PASSWORD; only if the site rejects
it, reset the password to that same ATS_PASSWORD with the one-time code emailed to
the owner (read from the Gmail the browser is signed in to). Employer ATS sites
only, once per site per run, never a generated or different password.

The site below is a small stand-in for one: create account, sign in, forgot
password, an emailed code, a new password. Only the Gmail read is faked.
"""
import json
import logging

import pytest

import config
import safety
from browser_automation import JobApplicationAssistant
from sites.base import SiteAdapter

ATS_PASSWORD = "s3cret-ATS"
OLD_PASSWORD = "the-old-password-on-the-site"
CODE = "482913"
EMAIL = "owner@example.com"
HOST = "careers.example-ats.com"
EXISTS = "An account with this email address already exists."

SITE = """<!doctype html><html><body>
<h1 id="title"></h1>
<div id="alert" role="alert"></div>
<div id="view"></div>
<script>
const cfg = %CFG%;
const S = window.__site = {password: cfg.password, code: "%CODE%", codeRequests: 0,
                           signIns: [], resets: [], attempts: [], view: ""};
const $ = id => document.getElementById(id);
const say = t => { $('alert').textContent = t; };
const field = (id, label, type) =>
  `<div><label for="${id}">${label}</label><input id="${id}" type="${type}"></div>`;
function show(v, message) { S.view = v; render(); say(message || ''); }
function render() {
  const el = $('view'), v = S.view;
  if (v === 'create') {
    $('title').textContent = 'Create Account';
    el.innerHTML = field('c-email', 'Email Address', 'email') + field('c-pw', 'Password', 'password')
      + field('c-pw2', 'Verify New Password', 'password')
      + '<button id="do-create">Create Account</button> <a href="#" id="to-signin">Already have an account? Sign In</a>';
    $('do-create').onclick = () => say(cfg.createMessage);
    $('to-signin').onclick = e => { e.preventDefault(); show('signin'); };
  } else if (v === 'signin') {
    $('title').textContent = 'Sign In';
    el.innerHTML = field('s-email', 'Email Address', 'email') + field('s-pw', 'Password', 'password')
      + '<button id="do-signin">Sign In</button> <a href="#" id="to-forgot">Forgot your password?</a> '
      + '<a href="#" id="to-create">Create Account</a>';
    $('do-signin').onclick = () => {
      const ok = $('s-email').value === cfg.email && $('s-pw').value === S.password;
      S.signIns.push(ok);
      if (ok) show('home'); else say('Invalid email or password.');
    };
    $('to-forgot').onclick = e => { e.preventDefault(); show('forgot'); };
    $('to-create').onclick = e => { e.preventDefault(); show('create'); };
  } else if (v === 'forgot') {
    $('title').textContent = 'Reset Password';
    el.innerHTML = field('f-email', 'Email Address', 'email') + '<button id="do-send">Send Code</button>';
    $('do-send').onclick = () => {
      S.codeRequests++;
      show(cfg.mode === 'link' ? 'linksent' : cfg.mode === 'onepage' ? 'onepage' : 'code');
    };
  } else if (v === 'linksent') {
    $('title').textContent = 'Check your email';
    el.innerHTML = '<p>We have emailed you a link to reset your password. Click the link in the email.</p>';
  } else if (v === 'code') {
    $('title').textContent = 'Enter your code';
    el.innerHTML = "<p>We've sent a 6-digit verification code to your email.</p>"
      + field('code', 'Verification code', 'text') + '<button id="do-verify">Verify</button>';
    $('do-verify').onclick = () => {
      if ($('code').value === S.code) show('newpass'); else say('The code is invalid or expired.');
    };
  } else if (v === 'newpass' || v === 'onepage') {
    $('title').textContent = v === 'newpass' ? 'Choose a new password' : 'Reset your password';
    el.innerHTML = (v === 'onepage'
        ? "<p>We've sent a 6-digit verification code to your email.</p>" + field('code', 'Verification code', 'text') : '')
      + field('np1', 'New password', 'password') + field('np2', 'Confirm new password', 'password')
      + '<button id="do-reset">Reset Password</button>';
    $('do-reset').onclick = () => {
      const a = $('np1').value, b = $('np2').value;
      if (v === 'onepage' && $('code').value !== S.code) return say('The code is invalid or expired.');
      S.attempts.push([a, b]);
      if (a !== b) return say('Passwords do not match.');
      if (cfg.rejectNew || a.length < 8) return say('Password does not meet the requirements.');
      S.password = a; S.resets.push(a);
      show('signin', 'Your password has been reset. Please sign in.');
    };
  } else if (v === 'home') {
    $('title').textContent = 'Candidate Home';
    el.innerHTML = '<p>Welcome back.</p>';
  }
}
show(cfg.start, cfg.showOnLoad ? cfg.createMessage : '');
</script></body></html>"""


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
def agent(monkeypatch):
    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a.values = safety.AgentValues()
    a._profile = config.UserProfile(email=EMAIL, check_gmail_for_confirmation=True)   # the owner allowed mail reads
    a.adapter = lambda _page: SiteAdapter()
    a.RECOVERY_WAIT_SECONDS = 2
    a.gmail_calls = []
    a.codes = [CODE]

    def gmail(page, previous="", wait_seconds=150, length=0):
        a.gmail_calls.append(previous)
        return a.codes.pop(0) if a.codes else ""

    a.passcode_from_gmail = gmail
    monkeypatch.setattr(JobApplicationAssistant, "_read_ats_password", staticmethod(lambda: ATS_PASSWORD))
    return a


def serve(page, *, password=OLD_PASSWORD, start="create", mode="code", reject_new=False, host=HOST,
          message=EXISTS, show_on_load=False):
    cfg = json.dumps({"password": password, "email": EMAIL, "start": start, "mode": mode,
                      "rejectNew": reject_new, "createMessage": message, "showOnLoad": show_on_load})
    body = SITE.replace("%CFG%", cfg).replace("%CODE%", CODE)
    page.route(f"https://{host}/**", lambda route: route.fulfill(status=200, content_type="text/html", body=body))
    page.goto(f"https://{host}/careers/apply")


def site(page):
    return page.evaluate("window.__site")


# --- recognising the message --------------------------------------------------------

def test_account_exists_messages_are_recognised_and_look_alikes_are_not():
    said = JobApplicationAssistant._ACCOUNT_EXISTS
    for text in ("An account with this email address already exists.",
                 "This email is already registered.",
                 "User already exists",
                 "The email address is already in use.",
                 "An account already exists for this email.",
                 "You already have an account with this email."):
        assert said.search(text), text
    for text in ("Already have an account? Sign In",
                 "Create an account to apply",
                 "Passwords do not match.",
                 "Email is required",
                 "Sign in to your existing account"):
        assert not said.search(text), text


# --- the existing password first -------------------------------------------------------

def test_the_existing_password_is_tried_first_and_nothing_else_happens(page, agent):
    serve(page, password=ATS_PASSWORD)
    assert agent.sign_in_to_existing_account(page, EMAIL) is True
    state = site(page)
    assert state["view"] == "home" and state["signIns"] == [True]
    assert agent.gmail_calls == [] and state["codeRequests"] == 0 and state["resets"] == []


# --- then a reset to that same password, with the emailed code --------------------------

def test_a_rejected_password_is_reset_to_the_existing_one_with_the_emailed_code(page, agent):
    serve(page, password=OLD_PASSWORD)
    assert agent.sign_in_to_existing_account(page, EMAIL) is True
    state = site(page)
    assert state["password"] == ATS_PASSWORD and state["resets"] == [ATS_PASSWORD]
    assert state["signIns"] == [False, True] and state["view"] == "home"
    assert agent.gmail_calls == [""] and state["codeRequests"] == 1


def test_a_site_that_asks_for_the_code_and_the_password_together_is_handled(page, agent):
    serve(page, password=OLD_PASSWORD, mode="onepage")
    assert agent.sign_in_to_existing_account(page, EMAIL) is True
    state = site(page)
    assert state["password"] == ATS_PASSWORD and state["resets"] == [ATS_PASSWORD]
    assert state["attempts"] == [[ATS_PASSWORD, ATS_PASSWORD]]


def test_a_wrong_code_is_retried_once_with_a_fresh_one(page, agent):
    agent.codes = ["000000", CODE]
    serve(page, password=OLD_PASSWORD)
    assert agent.sign_in_to_existing_account(page, EMAIL) is True
    assert agent.gmail_calls == ["", "000000"]
    assert site(page)["password"] == ATS_PASSWORD


# --- what it never does --------------------------------------------------------------

def test_the_new_password_is_never_anything_but_the_existing_one(page, agent):
    serve(page, password=OLD_PASSWORD, reject_new=True)
    assert agent.sign_in_to_existing_account(page, EMAIL) is False
    state = site(page)
    assert state["password"] == OLD_PASSWORD and state["resets"] == []
    assert state["attempts"] and all(pair == [ATS_PASSWORD, ATS_PASSWORD] for pair in state["attempts"])


def test_a_site_that_sends_a_link_instead_of_a_code_is_left_to_the_owner(page, agent):
    serve(page, password=OLD_PASSWORD, mode="link")
    assert agent.sign_in_to_existing_account(page, EMAIL) is False
    assert agent.gmail_calls == [] and site(page)["password"] == OLD_PASSWORD


def test_no_code_in_gmail_leaves_it_to_the_owner(page, agent):
    agent.codes = []
    serve(page, password=OLD_PASSWORD)
    assert agent.sign_in_to_existing_account(page, EMAIL) is False
    assert site(page)["password"] == OLD_PASSWORD


def test_it_never_types_a_password_on_a_site_that_is_off_limits(page, agent):
    serve(page, password=OLD_PASSWORD, host="www.linkedin.com")
    assert agent.sign_in_to_existing_account(page, EMAIL) is False
    state = site(page)
    assert state["view"] == "create" and state["signIns"] == [] and state["attempts"] == []
    assert agent.gmail_calls == []


def test_it_is_tried_once_per_site_per_run(page, agent):
    serve(page, password=OLD_PASSWORD, reject_new=True)
    assert agent.sign_in_to_existing_account(page, EMAIL) is False
    assert agent.sign_in_to_existing_account(page, EMAIL) is False
    assert site(page)["codeRequests"] == 1


def test_neither_the_password_nor_the_code_is_ever_logged(page, agent, caplog):
    caplog.set_level(logging.DEBUG)
    serve(page, password=OLD_PASSWORD)
    assert agent.sign_in_to_existing_account(page, EMAIL) is True
    assert ATS_PASSWORD not in caplog.text and CODE not in caplog.text


def test_the_gmail_reader_does_not_log_the_code_from_the_mail_it_read(browser, agent, caplog):
    """The real reader, on a stand-in inbox whose subject line shows the code."""
    caplog.set_level(logging.DEBUG)
    inbox = ('<!doctype html><table><tr class="zA"><td>Example ATS</td>'
             f'<td>Your verification code is {CODE}</td></tr></table>')
    context = browser.new_context()  # the reader opens its own tab, which needs a real context
    try:
        context.route("https://mail.google.com/**",
                      lambda route: route.fulfill(status=200, content_type="text/html", body=inbox))
        code = JobApplicationAssistant.passcode_from_gmail(agent, context.new_page(), wait_seconds=5)
    finally:
        context.close()
    assert code == CODE
    assert CODE not in caplog.text


# --- when it runs --------------------------------------------------------------------------

def test_a_create_form_that_says_the_account_exists_signs_in_instead(page, agent):
    serve(page, password=ATS_PASSWORD)
    assert agent.fill_create_account_form(page, EMAIL) is True
    state = site(page)
    assert state["view"] == "home" and agent.gmail_calls == []


def test_a_create_form_that_says_the_account_exists_resets_a_rejected_password(page, agent):
    serve(page, password=OLD_PASSWORD)
    assert agent.fill_create_account_form(page, EMAIL) is True
    assert site(page)["password"] == ATS_PASSWORD and agent.gmail_calls == [""]


def test_any_other_create_form_error_does_not_touch_sign_in_or_gmail(page, agent):
    serve(page, password=OLD_PASSWORD, message="Passwords do not match.")
    assert agent.fill_create_account_form(page, EMAIL) is False
    state = site(page)
    assert state["view"] == "create" and state["signIns"] == [] and agent.gmail_calls == []


def test_the_auth_gate_acts_on_an_exists_alert_already_on_the_page(page, agent):
    serve(page, password=ATS_PASSWORD, show_on_load=True)
    assert agent.handle_auth_gate(page, EMAIL) is True
    assert site(page)["view"] == "home"


# --- a refused sign-in on an account the site knows (Mutual of Enumclaw, iCIMS, 29 September) ------------------------

def test_a_refused_sign_in_is_reset_to_the_existing_password_and_signed_in(page, agent):
    serve(page, password=OLD_PASSWORD, start="signin")
    assert agent.attempt_auto_login(page, EMAIL, ATS_PASSWORD, create_if_missing=False) is False
    assert agent.recover_rejected_sign_in(page, EMAIL) is True
    state = site(page)
    assert state["password"] == ATS_PASSWORD and state["resets"] == [ATS_PASSWORD]
    assert state["signIns"] == [False, True] and state["view"] == "home"
    assert agent.gmail_calls == [""] and state["codeRequests"] == 1


def test_one_reset_per_site_per_run_whichever_route_asked_for_it(page, agent):
    serve(page, password=OLD_PASSWORD, reject_new=True)
    assert agent.sign_in_to_existing_account(page, EMAIL) is False       # the 'account exists' route: one reset
    serve(page, password=OLD_PASSWORD, start="signin")
    assert agent.attempt_auto_login(page, EMAIL, ATS_PASSWORD, create_if_missing=False) is False
    assert agent.recover_rejected_sign_in(page, EMAIL) is False          # the sign-in route: not a second one
    assert site(page)["codeRequests"] == 0 and agent.gmail_calls == [""]


def test_a_refused_sign_in_is_never_reset_on_a_site_that_is_off_limits(page, agent):
    serve(page, password=OLD_PASSWORD, start="signin", host="www.linkedin.com")
    assert agent.recover_rejected_sign_in(page, EMAIL) is False
    state = site(page)
    assert state["codeRequests"] == 0 and state["attempts"] == [] and agent.gmail_calls == []
