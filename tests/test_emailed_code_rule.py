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


SMS_CODE_STEP = """<html><body><h2>Verify your identity</h2>
<p>We texted a 6-digit code to your phone number ending in 1234.</p>
<label for="c">Enter the 6-digit one-time code</label>
<input id="c"><button>Submit</button></body></html>"""

AUTHENTICATOR_CODE_STEP = """<html><body><h2>Two-factor authentication</h2>
<p>Enter the one-time code from your authenticator app.</p>
<label for="c">One-time code</label>
<input id="c"><button>Verify</button></body></html>"""


@pytest.mark.parametrize("body", [SMS_CODE_STEP, AUTHENTICATOR_CODE_STEP])
def test_a_code_from_an_unauthorized_channel_is_never_read_from_mail(context, body):
    """P0-B2, 7 October 2026: ACCOUNT_CODE's own wording match ("one-time code"/"authentication
    code") is not enough on its own -- the page's own channel wording (texted to a phone, or an
    authenticator app) must refuse before passcode_from_gmail is ever called, on an employer site
    where the owner has allowed mail reads and no CAPTCHA is showing: every other condition that
    would normally allow a read is satisfied here, isolating the channel check itself."""
    import page_agent
    page = employer_page(context, body)
    assistant = assistant_for(ALLOWED)
    called = []
    assistant.passcode_from_gmail = lambda *a, **k: (called.append(1), "482913")[1]
    agent = page_agent.PageAgent(assistant, SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email=""),
                                 ALLOWED, SimpleNamespace(raw_text="x"),
                                 SimpleNamespace(title="t", company="c", url=URL), resume_file=None)
    snapshot = agent.snapshot(page)
    controls = page_agent.parse_snapshot(snapshot)
    assert agent.complete_account_code(page, controls, snapshot) is False
    assert called == []                                    # the mail was never even opened
    assert page.locator("#c").input_value() == ""
    assert agent.account_blocker and "authoriz" in agent.account_blocker.lower()


def test_no_step_refuses_a_code_for_how_the_site_words_it():
    """Read the sources: the words a site uses for its code step decide nothing anywhere (a CAPTCHA does,
    and safety.py's detection of one is the only place that knows its wording)."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for name in ("page_agent.py", "browser_automation.py", "emailed_codes.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "HUMAN_CHECK" not in text and "not a robot" not in text, name



# --- the account-verification link (the owner's decision of 30 September 2026) ----------------------------------------

@pytest.mark.parametrize("link, text, ok", [
    ("https://ciena.wd5.myworkdayjobs.com/en-US/Ciena_Careers/activate/abc", "Verify Account", True),
    ("https://www.google.com/url?q=https://ciena.wd5.myworkdayjobs.com/activate/abc&sa=D", "Verify", True),
    ("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternal/activate/abc", "Verify", False),     # another employer
    ("https://u123.ct.sendgrid.net/ls/click?upn=abc", "Verify your account", False),          # a tracking redirect
    ("https://ciena.wd5.myworkdayjobs.com/en-US/Ciena_Careers/passwordreset/abc", "Reset password", False),
    ("http://ciena.wd5.myworkdayjobs.com/activate/abc", "Verify", False),                     # not https
    ("https://evil-myworkdayjobs.com.attacker.net/activate", "Verify", False),
])
def test_only_this_sites_own_verification_link_is_opened(link, text, ok):
    import emailed_codes
    assert emailed_codes.verification_link_ok(link, text, "https://ciena.wd5.myworkdayjobs.com/en-US/Ciena_Careers/login") is ok


# --- the code's own delivery channel (P0-B2, 7 October 2026) --------------------------------------
#
# ACCOUNT_CODE (page_agent.complete_account_code) matched a box by words like "verification code"
# or "one-time code" alone, with nothing excluding a code the page itself says was sent by SMS/text
# or must come from an authenticator app -- channels why_not's own permission (the owner's Gmail)
# has nothing to do with. A page reading "Enter the one-time code we texted to your phone" matched
# that wording and would have had passcode_from_gmail search an inbox the real code was never going
# to reach.

@pytest.mark.parametrize("context", [
    "We texted a 6-digit code to your phone number ending in 1234.",
    "Enter the code we sent via SMS.",
    "A verification code was sent to your mobile phone.",
    "Enter the 6-digit code from your authenticator app.",
    "Open Google Authenticator and enter the code shown there.",
    "Enter your Authy code to continue.",
    "Enter the TOTP code from your authenticator.",
    "Insert your security key and press the button on it.",
    "Tap your hardware key to verify.",
])
def test_a_non_email_channel_is_never_treated_as_readable_mail(context):
    assert emailed_codes.code_channel_is_email(context) is False


@pytest.mark.parametrize("context", [
    "",
    "We sent a verification code to your email. Check your inbox.",
    "Enter the one-time code we emailed you.",
    "A verification code was sent to j***@example.com",
    "Enter the 8-character code to confirm you're a human.",
])
def test_an_email_or_unlabeled_channel_is_still_treated_as_readable_mail(context):
    assert emailed_codes.code_channel_is_email(context) is True


# --- the three-way channel report, and local vs. page-wide scope (the follow-up, 7 October 2026) ---

@pytest.mark.parametrize("context, channel", [
    ("We sent a verification code to your email. Check your inbox.", "EMAIL"),
    ("A verification code was sent to j***@example.com", "EMAIL"),
    ("We texted a 6-digit code to your phone.", "NON_EMAIL"),
    ("Enter the code from your authenticator app.", "NON_EMAIL"),
    ("Enter the 8-character code to confirm you're a human.", "UNKNOWN"),   # no channel named
    ("", "UNKNOWN"),
    ("We emailed your verification code. We also texted the code to your phone.", "UNKNOWN"),  # contradictory
])
def test_code_channel_reports_the_evidence_honestly(context, channel):
    assert emailed_codes.code_channel(context) == channel


@pytest.mark.parametrize("context", [
    "We emailed your verification code. We also texted the code to your phone.",
    "Check your inbox -- or enter the code from your authenticator app if you prefer.",
    "A verification code was sent to j***@example.com. SMS delivery is also available.",
])
def test_contradictory_local_evidence_is_never_read_as_email(context):
    """code_channel() reports this as UNKNOWN (genuinely ambiguous), and
    code_channel_is_email() treats it the same as a confirmed non-email channel -- never guessed,
    even though an email channel is ALSO named in the same local context."""
    assert emailed_codes.code_channel(context) == "UNKNOWN"
    assert emailed_codes.code_channel_is_email(context) is False


def test_unknown_with_no_channel_named_is_still_the_preserved_default():
    """The other way UNKNOWN arises -- no channel named at all -- keeps this project's existing,
    deliberate default (allowed), distinguishing it from the contradictory case above even though
    code_channel() itself reports both as the same "UNKNOWN" value."""
    context = "Enter the 8-character code to confirm you're a human."
    assert emailed_codes.code_channel(context) == "UNKNOWN"
    assert emailed_codes.code_channel_is_email(context) is True


def test_account_state_mfa_and_emailed_codes_channel_share_one_core_vocabulary():
    """Avoids semantic drift (the follow-up's own explicit requirement, 7 October 2026):
    account_state._MFA imports emailed_codes.NON_EMAIL_CHANNEL_CORE directly rather than
    maintaining its own, independent copy of the same words."""
    import account_state
    assert emailed_codes.NON_EMAIL_CHANNEL_CORE.pattern in account_state._MFA.pattern


# --- the real production path: PageAgent.complete_account_code() (the follow-up, 7 October 2026) ---

def _gmail_mock(calls):
    return lambda *a, **k: (calls.append(1), "482913")[1]


def _complete_code_through(context, html):
    """Runs the real complete_account_code() against `html`, with passcode_from_gmail mocked to
    prove whether Gmail would actually have been opened. Returns (outcome, gmail_was_called,
    account_blocker)."""
    import page_agent
    page = employer_page(context, html)
    assistant = assistant_for(ALLOWED)
    calls = []
    assistant.passcode_from_gmail = _gmail_mock(calls)
    agent = page_agent.PageAgent(assistant, SimpleNamespace(), SimpleNamespace(auto_submit=False, ats_email=""),
                                 ALLOWED, SimpleNamespace(raw_text="x"),
                                 SimpleNamespace(title="t", company="c", url=URL), resume_file=None)
    snapshot = agent.snapshot(page)
    controls = page_agent.parse_snapshot(snapshot)
    outcome = agent.complete_account_code(page, controls, snapshot)
    return outcome, bool(calls), agent.account_blocker


# A/B/C: an email-delivered code must not be blocked by unrelated SMS/phone/mobile-app wording
# elsewhere on the same page -- the original false-negative this follow-up exists to fix.
_EMAIL_CODE_WITH_UNRELATED_NOISE = [
    ("A_unrelated_sms_consent_after",
     '<h2>Verify your email</h2><p>We sent a verification code to your email.</p>'
     '<label for="c">Verification Code</label><input id="c">'
     '<p>Would you like to receive job updates via SMS?</p>'
     '<input type="checkbox" id="smsconsent">'),
    ("B_unrelated_phone_field",
     '<h2>Verify your email</h2><p>We sent a verification code to your email.</p>'
     '<label for="c">Verification Code</label><input id="c">'
     '<label for="ph">Phone Number</label><input id="ph">'),
    ("C_unrelated_mobile_app_text",
     '<h2>Senior Engineer</h2><p>Experience with mobile app development preferred.</p>'
     '<p>We sent a verification code to your email.</p>'
     '<label for="c">Verification Code</label><input id="c">'),
]


@pytest.mark.parametrize(("case_id", "html"), _EMAIL_CODE_WITH_UNRELATED_NOISE)
def test_email_code_is_still_read_despite_unrelated_page_wide_noise(context, case_id, html):
    outcome, gmail_called, blocker = _complete_code_through(context, html)
    assert gmail_called, case_id       # Gmail path IS allowed and IS called
    assert not blocker, case_id        # no owner blocker caused by unrelated wording


# D/E/F/G: every non-email channel remains blocked, using the real production path end to end.
_NON_EMAIL_CODE_CASES = [
    ("D_sms_code",
     '<h2>Verify your identity</h2><p>We texted a 6-digit code to your phone.</p>'
     '<label for="c">One-time code</label><input id="c">'),
    ("E_authenticator_app_code",
     '<h2>Two-factor authentication</h2><p>Enter the one-time code from your authenticator app.</p>'
     '<label for="c">One-time code</label><input id="c">'),
    ("F_totp_authy_google_authenticator",
     '<h2>Verify</h2><p>Open Google Authenticator and enter the code shown there.</p>'
     '<label for="c">Verification code</label><input id="c">'),
    ("G_security_key",
     '<h2>Verify</h2><p>Insert your security key and enter the code it displays.</p>'
     '<label for="c">Verification code</label><input id="c">'),
]


@pytest.mark.parametrize(("case_id", "html"), _NON_EMAIL_CODE_CASES)
def test_non_email_channels_are_never_read_through_the_real_path(context, case_id, html):
    outcome, gmail_called, blocker = _complete_code_through(context, html)
    assert outcome is False, case_id
    assert not gmail_called, case_id   # Gmail is never opened
    assert blocker and "authoriz" in blocker.lower(), case_id   # precise owner handoff remains


def test_unknown_channel_preserves_the_existing_default_through_the_real_path(context):
    """H: no delivery channel named at all -- the existing, deliberate default (allowed)."""
    html = '<h2>Verify</h2><label for="c">Verification code</label><input id="c">'
    outcome, gmail_called, blocker = _complete_code_through(context, html)
    assert gmail_called
    assert not blocker


def test_contradictory_local_evidence_fails_closed_through_the_real_path(context):
    """I: the same local verification context names both an email and a non-email channel --
    never guessed, through the real production path."""
    html = ('<h2>Verify</h2><p>We emailed your verification code. We also texted the code to '
            'your phone.</p><label for="c">Verification code</label><input id="c">')
    outcome, gmail_called, blocker = _complete_code_through(context, html)
    assert outcome is False
    assert not gmail_called
    assert blocker
