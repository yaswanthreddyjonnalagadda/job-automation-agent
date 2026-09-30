"""One rule for when the agent may read a one-time code from the owner's mail.

The older code-entry step, the sign-in / account-setup step and the existing-account password reset each decided
this for themselves, and none checked everything: the reset never asked whether the owner had allowed the agent to
read their mail or whether the code was one a site wants to prove a human is applying; the code-entry step never
asked whether the site was one the agent may enter anything on. The class: one decision written in several places,
each missing a different part. emailed_codes.why_not() now holds it, and passcode_from_gmail -- which every reader
goes through -- asks it before it opens the mail.

The owner decided on 25 September 2026 that the agent enters a code Greenhouse emails "to confirm you're a
human" itself: the code proves the applicant controls the mailbox, and the agent already reads that mailbox with
the owner's permission. What stays the owner's is a CAPTCHA -- a challenge on the page -- and the rule now goes
by whether one is on the page, not by how the site words its code step (the wording had stopped a Praxis
application one step short of done).
"""
import config
import pytest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import emailed_codes
import login_guard
import safety
from browser_automation import JobApplicationAssistant
from sites.base import SiteAdapter

HOST = "careers.example-ats.com"
URL = f"https://{HOST}/en-US/careers/login"
EMAIL = "owner@example.com"
ALLOWED = config.UserProfile(email=EMAIL, check_gmail_for_confirmation=True)
NOT_ALLOWED = config.UserProfile(email=EMAIL, check_gmail_for_confirmation=False)
HUMAN = "A verification code was sent to you. To submit your application, enter the 8-character code to confirm you're a human."
CAPTCHA_FRAME = ('<iframe title="recaptcha challenge expires in two minutes" width="300" height="300" '
                 'srcdoc="<p>challenge</p>"></iframe>')


# --- the rule ---------------------------------------------------------------------------------------

def test_a_code_may_be_read_for_an_employer_site_when_the_owner_allowed_it():
    assert emailed_codes.why_not(ALLOWED, URL) is None


@pytest.mark.parametrize("profile", [None, NOT_ALLOWED])
def test_no_code_is_read_without_the_owners_permission_to_read_the_mail(profile):
    why = emailed_codes.why_not(profile, URL)
    assert why and "allowed" in why


@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/login", "https://accounts.google.com/signin", "https://login.microsoftonline.com/x",
    "https://appleid.apple.com/", "https://secure.indeed.com/auth", "https://www.dice.com/login",
])
def test_no_code_is_read_for_a_site_the_agent_may_not_enter_anything_on(url):
    why = emailed_codes.why_not(ALLOWED, url)
    assert why and "not a site" in why


def test_a_captcha_on_the_page_is_never_a_code_to_read():
    why = emailed_codes.why_not(ALLOWED, URL, captcha=True)
    assert why and "CAPTCHA" in why


def test_how_a_site_words_its_code_step_is_not_what_decides():
    """The rule goes by what is on the page (a CAPTCHA), not by what the page says about itself: it once
    refused a code for saying "to confirm you're a human", though nothing on the page was a challenge."""
    assert not hasattr(emailed_codes, "HUMAN_CHECK")


def test_reads_are_limited_across_runs_and_forgotten_after_a_day(monkeypatch):
    for _ in range(login_guard.MAX_CODE_READS):
        assert emailed_codes.why_not(ALLOWED, URL) is None
        login_guard.record_code_read(HOST, EMAIL)
    why = emailed_codes.why_not(ALLOWED, URL)
    assert why and "24 hours" in why
    later = datetime.now(timezone.utc) + timedelta(hours=25)
    monkeypatch.setattr(login_guard, "_now", lambda: later)
    assert emailed_codes.why_not(ALLOWED, URL) is None


def test_the_reads_of_one_account_are_not_the_reads_of_another():
    for _ in range(login_guard.MAX_CODE_READS):
        login_guard.record_code_read(HOST, EMAIL)
    assert emailed_codes.why_not(ALLOWED, "https://other.example-ats.com/login") is None


# --- every reader goes through it ---------------------------------------------------------------------

INBOX = ('<!doctype html><table><tr class="zA"><td>Example ATS</td><td>Your verification code is 482913</td></tr>'
         "</table>")


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def context(browser):
    ctx = browser.new_context()
    yield ctx
    ctx.close()


def assistant_for(profile, adapter=None):
    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a.values = safety.AgentValues()
    a._profile = profile
    a._config = SimpleNamespace(ats_email=EMAIL)
    a.adapter = lambda _page: adapter or SiteAdapter()
    return a


def serve_mail(context, hits):
    context.route("https://mail.google.com/**", lambda route: (hits.append(1), route.fulfill(
        status=200, content_type="text/html", body=INBOX))[1])


def employer_page(context, body="<h2>Verify</h2><input id='c'>"):
    page = context.new_page()
    context.route(f"https://{HOST}/**", lambda route: route.fulfill(status=200, content_type="text/html", body=body))
    page.goto(URL)
    return page


def test_the_mail_reader_reads_the_code_when_the_rule_allows_it_and_counts_the_read(context):
    hits = []
    serve_mail(context, hits)
    page = employer_page(context)
    assert assistant_for(ALLOWED).passcode_from_gmail(page, wait_seconds=5) == "482913"
    assert hits
    assert len(login_guard._recent(login_guard._entry(login_guard._load(), HOST, EMAIL).get("code_reads"),
                                   login_guard._now())) == 1


@pytest.mark.parametrize("profile, body", [
    (NOT_ALLOWED, "<h2>Verify</h2><input id='c'>"),
    (None, "<h2>Verify</h2><input id='c'>"),
    (ALLOWED, f"<p>{HUMAN}</p>{CAPTCHA_FRAME}<input id='c'>"),          # the same words, beside a real challenge
])
def test_the_mail_reader_never_opens_the_mail_when_the_rule_says_no(context, profile, body):
    hits = []
    serve_mail(context, hits)
    page = employer_page(context, body)
    assert assistant_for(profile).passcode_from_gmail(page, wait_seconds=5) == ""
    assert hits == []                                                  # Gmail was never even requested


def test_a_code_the_site_says_confirms_a_human_is_read_when_no_captcha_is_showing(context):
    """Greenhouse: "enter the 8-character code to confirm you're a human". It is an emailed code like any other,
    and the owner wants the agent to enter it itself (25 September 2026)."""
    hits = []
    serve_mail(context, hits)
    page = employer_page(context, f"<p>{HUMAN}</p><label for='c'>Security code</label><input id='c'>")
    assert assistant_for(ALLOWED).passcode_from_gmail(page, wait_seconds=5) == "482913"
    assert hits


def test_the_reader_stops_at_the_limit(context):
    hits = []
    serve_mail(context, hits)
    page = employer_page(context)
    for _ in range(login_guard.MAX_CODE_READS):
        login_guard.record_code_read(HOST, EMAIL)
    assert assistant_for(ALLOWED).passcode_from_gmail(page, wait_seconds=5) == ""
    assert hits == []


FORGOT_SITE = """<html><body><h2>Sign In</h2><input type="email"><input type="password"><button>Sign In</button>
<a href="#" id="forgot">Forgot your password?</a>
<script>window.forgot = 0; document.getElementById('forgot').addEventListener('click', e => { e.preventDefault(); window.forgot++; });
</script></body></html>"""


@pytest.mark.parametrize("profile, body", [
    (NOT_ALLOWED, FORGOT_SITE), (None, FORGOT_SITE),
    (ALLOWED, FORGOT_SITE.replace("<h2>Sign In</h2>", f"<h2>Sign In</h2>{CAPTCHA_FRAME}")),
])
def test_a_password_reset_is_not_even_requested_when_its_code_could_not_be_read(context, profile, body):
    """The reset used to click Forgot first and find out afterwards it could not read the code: a request spent
    (a site allows a few) for nothing."""
    page = employer_page(context, body)
    assert assistant_for(profile)._reset_password_with_emailed_code(page, EMAIL, "s3cret-ATS") is False
    assert page.evaluate("window.forgot") == 0


CODE_STEP = """<html><body><h2>Verify your email</h2><label for="c">Enter verification code sent to email</label>
<input id="c"><button>Verify</button></body></html>"""


@pytest.mark.parametrize("profile", [NOT_ALLOWED, None])
def test_the_sign_in_and_account_setup_code_step_obeys_the_same_rule(context, profile):
    hits = []
    serve_mail(context, hits)
    page = employer_page(context, CODE_STEP)
    assert assistant_for(profile).complete_emailed_passcode(page) is False
    assert hits == [] and page.locator("#c").input_value() == ""


def test_the_form_code_step_obeys_the_same_rule_on_a_site_it_may_not_enter_anything_on(context):
    import page_agent
    context.route("https://www.linkedin.com/**", lambda route: route.fulfill(
        status=200, content_type="text/html", body=CODE_STEP))
    page = context.new_page()
    page.goto("https://www.linkedin.com/login")
    assistant = assistant_for(ALLOWED)
    assistant.passcode_from_gmail = lambda *a, **k: "482913"           # even if a reader were offered
    agent = page_agent.PageAgent(assistant, SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email=""), ALLOWED,
                                 SimpleNamespace(raw_text="x"), SimpleNamespace(title="t", company="c", url=URL),
                                 resume_file=None)
    controls = page_agent.parse_snapshot(agent.snapshot(page))
    assert agent.complete_account_code(page, controls, agent.snapshot(page)) is False
    assert page.locator("#c").input_value() == ""


def test_no_step_refuses_a_code_for_how_the_site_words_it():
    """Read the sources: the words a site uses for its code step decide nothing anywhere (a CAPTCHA does,
    and safety.py's detection of one is the only place that knows its wording)."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for name in ("page_agent.py", "browser_automation.py", "emailed_codes.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "HUMAN_CHECK" not in text and "not a robot" not in text, name
