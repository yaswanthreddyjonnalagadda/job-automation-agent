"""Workday's Create Account was pressed with the passwords filled and the email box empty (28 September,
KBI Biopharma on jsrglobal.wd1.myworkdayjobs.com): the site answered with an error at the top.

The account form found its email box only by attribute guesses (type=email, name/id containing "email"
or "username"). Workday's is a plain type=text box with a generated id, known by its label "Email
Address" and data-automation-id="email", so the form saw no email box, typed only the passwords and
pressed Create Account. The sign-in form had a better lookup of its own; the two had drifted apart.

The class: a field found by its HTML attributes instead of by what it is labelled -- and a form
submitted without checking that each thing it needs is really in it. Also here: after creating the
account the agent now signs in with the email and password (the owner's decision of 28 September),
instead of stopping; a code the site then emails is read from the owner's mail by the existing code step.
"""
import pytest

import login_guard
import safety
from browser_automation import JobApplicationAssistant
from sites.base import SiteAdapter
from sites.workday import WorkdayAdapter

HOST = "tenant.wd1.myworkdayjobs.com"
EMAIL = "owner@example.com"
PASSWORD = "s3cret-ATS"

# Every way a page says "this box is the email", with nothing else about the box giving it away.
EMAIL_BOXES = {
    "label-for": '<label for="i1">Email Address*</label><input type="text" id="i1" class="box">',
    "wrapping-label": '<label>Email Address* <input type="text" class="box"></label>',
    "aria-label": '<input type="text" aria-label="Email Address" class="box">',
    "aria-labelledby": '<span id="l1">Email Address*</span><input type="text" aria-labelledby="l1" class="box">',
    "placeholder": '<input type="text" placeholder="Email" class="box">',
    "workday": '<label for="input-4">Email Address*</label>'
               '<input type="text" id="input-4" data-automation-id="email" class="box">',
    "type-email": '<input type="email" id="x9" class="box">',
    "username": '<label for="u">Username</label><input type="text" id="u" class="box">',
}
# Workday's robot trap: a text box that must stay empty.
HONEYPOT = ('<label for="w">Enter website. This input is for robots only, do not enter if you\'re human.</label>'
            '<input type="text" id="w" name="website">')

RECORD = """<script>
  window.creates = 0; window.signins = 0; window.order = [];
  const box = () => document.querySelector('.box');
  document.addEventListener('input', e => window.order.push(e.target.type === 'password' ? 'password' : 'email'));
  const seen = () => ({email: box() ? box().value : null,
                       website: (document.getElementById('w') || {}).value,
                       passwords: [...document.querySelectorAll('input[type=password]')].map(p => p.value)});
  document.getElementById('create') && document.getElementById('create').addEventListener('click', () => {
    window.creates++; window.at_create = seen(); document.body.innerHTML = AFTER_CREATE; });
  document.getElementById('signin') && document.getElementById('signin').addEventListener('click', () => {
    window.signins++; window.at_signin = seen(); AFTER_SIGN_IN(); });
</script>"""


def create_page(email_box, after_create="'<h1>Candidate Home</h1>'", extra=""):
    return (f"<html><body><h1>Create Account</h1>{HONEYPOT}{email_box}"
            '<label for="p1">Password*</label><input id="p1" type="password">'
            '<label for="p2">Verify New Password*</label><input id="p2" type="password">'
            f'{extra}<button id="create">Create Account</button>'
            + RECORD.replace("AFTER_CREATE", after_create).replace("AFTER_SIGN_IN()", "0")
            + "</body></html>")


def sign_in_page(email_box, on_sign_in="document.body.innerHTML = '<h1>Candidate Home</h1>'"):
    return (f"<html><body><h2>Sign In</h2><div id='alert' role='alert'></div>{email_box}"
            '<label for="p">Password*</label><input id="p" type="password">'
            '<button id="signin">Sign In</button>'
            + RECORD.replace("AFTER_CREATE", "''").replace("AFTER_SIGN_IN()", on_sign_in)
            + "</body></html>")


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


def assistant_for(monkeypatch, adapter=None):
    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a.values = safety.AgentValues()
    a._profile = None
    a.adapter = lambda _page: adapter or SiteAdapter()
    monkeypatch.setattr(JobApplicationAssistant, "_read_ats_password", staticmethod(lambda: PASSWORD))
    return a


def serve(page, body):
    page.route(f"https://{HOST}/**", lambda route: route.fulfill(status=200, content_type="text/html", body=body))
    page.goto(f"https://{HOST}/en-US/careers/apply")


# --- creating the account ------------------------------------------------------------------------

@pytest.mark.parametrize("shape", sorted(EMAIL_BOXES))
def test_the_account_form_types_the_email_however_its_box_is_labelled(page, monkeypatch, shape):
    serve(page, create_page(EMAIL_BOXES[shape]))
    assert assistant_for(monkeypatch).fill_create_account_form(page, EMAIL) is True
    sent = page.evaluate("window.at_create")
    assert sent["email"] == EMAIL
    assert sent["passwords"] == [PASSWORD, PASSWORD]
    assert sent["website"] == ""                                       # the robot trap is left alone


def test_the_email_goes_in_before_the_passwords(page, monkeypatch):
    serve(page, create_page(EMAIL_BOXES["workday"]))
    assistant_for(monkeypatch).fill_create_account_form(page, EMAIL)
    order = page.evaluate("window.order")
    assert order and order[0] == "email" and "password" in order


def test_passwords_alone_are_never_submitted_when_there_is_no_email_box(page, monkeypatch):
    serve(page, create_page(""))
    assert assistant_for(monkeypatch).fill_create_account_form(page, EMAIL) is False
    assert page.evaluate("window.creates") == 0


def test_an_email_box_that_will_not_keep_the_address_stops_the_form(page, monkeypatch):
    wiping = ('<label for="e">Email Address*</label><input type="text" id="e" class="box">'
              "<script>document.getElementById('e').addEventListener('blur', e => { e.target.value = ''; });</script>")
    serve(page, create_page(wiping))
    assert assistant_for(monkeypatch).fill_create_account_form(page, EMAIL) is False
    assert page.evaluate("window.creates") == 0


def test_an_email_the_page_clears_while_it_redraws_is_typed_again_before_create(page, monkeypatch):
    """Workday redraws as the rest of the form fills; the passwords were already re-checked, the email was not."""
    clears = ("<script>document.getElementById('p2').addEventListener('blur', () => {"
              " if (!window.cleared) { window.cleared = 1; document.querySelector('.box').value = ''; } });</script>")
    serve(page, create_page(EMAIL_BOXES["workday"], extra=clears))
    assert assistant_for(monkeypatch).fill_create_account_form(page, EMAIL) is True
    assert page.evaluate("window.at_create")["email"] == EMAIL


# --- signing in ----------------------------------------------------------------------------------

@pytest.mark.parametrize("shape", sorted(EMAIL_BOXES))
def test_the_sign_in_types_the_email_however_its_box_is_labelled(page, monkeypatch, shape):
    serve(page, sign_in_page(EMAIL_BOXES[shape]))
    assert assistant_for(monkeypatch).attempt_auto_login(page, EMAIL, PASSWORD, create_if_missing=False) is True
    sent = page.evaluate("window.at_signin")
    assert sent["email"] == EMAIL and sent["passwords"] == [PASSWORD]


def test_a_sign_in_whose_email_will_not_stay_is_not_submitted(page, monkeypatch):
    wiping = ('<label for="e">Email Address*</label><input type="text" id="e" class="box">'
              "<script>document.getElementById('e').addEventListener('input', e => { e.target.value = ''; });</script>")
    serve(page, sign_in_page(wiping))
    assert assistant_for(monkeypatch).attempt_auto_login(page, EMAIL, PASSWORD, create_if_missing=False) is False
    assert page.evaluate("window.signins") == 0


# --- after the account is created ------------------------------------------------------------------

SIGN_IN_AFTER_CREATE = ("'<h2>Sign In</h2><div id=alert role=alert></div>"
                        "<label for=se>Email Address*</label><input type=text id=se data-automation-id=email>"
                        "<label for=sp>Password*</label><input type=password id=sp>"
                        "<button id=signin2>Sign In</button>'")
WIRE_SIGN_IN = ("<script>new MutationObserver(() => { const b = document.getElementById('signin2');"
                " if (b && !b.wired) { b.wired = 1; b.addEventListener('click', () => { window.signins++;"
                " window.at_signin = {email: document.getElementById('se').value,"
                " password: document.getElementById('sp').value}; ON_SIGN_IN }); } })"
                ".observe(document.documentElement, {childList: true, subtree: true});</script>")


def test_after_creating_the_account_it_opens_the_verification_link_then_signs_in(page, monkeypatch):
    """Waystar, 1 October: the sign-in waits for the evidence -- the site's verification link, opened from Gmail."""
    body = create_page(EMAIL_BOXES["workday"], after_create=SIGN_IN_AFTER_CREATE,
                       extra=WIRE_SIGN_IN.replace("ON_SIGN_IN", "document.body.innerHTML = '<h1>Candidate Home</h1>';"))
    serve(page, body)
    a = assistant_for(monkeypatch, WorkdayAdapter())
    monkeypatch.setattr(a, "verify_account_by_email_link", lambda page, wait_seconds=150: True)      # the link was in the mail
    assert a.fill_create_account_form(page, EMAIL) is True
    assert page.evaluate("window.signins") == 1
    assert page.evaluate("window.at_signin") == {"email": EMAIL, "password": PASSWORD}


def test_a_new_account_with_no_verification_email_is_signed_in_by_the_agent(page, monkeypatch):
    """Waystar, 1 October: no verification email came, and the run stopped to ask the owner. The owner's rule: the
    agent gets itself in -- with no verification email the site may not verify new accounts, so it signs in."""
    body = create_page(EMAIL_BOXES["workday"], after_create=SIGN_IN_AFTER_CREATE,
                       extra=WIRE_SIGN_IN.replace("ON_SIGN_IN", "document.body.innerHTML = '<h1>Candidate Home</h1>';"))
    serve(page, body)
    a = assistant_for(monkeypatch, WorkdayAdapter())
    monkeypatch.setattr(a, "verify_account_by_email_link", lambda page, wait_seconds=150: False)
    assert a.fill_create_account_form(page, EMAIL) is True
    assert page.evaluate("window.signins") == 1


def test_a_refused_sign_in_on_a_page_that_asks_nothing_does_not_go_to_the_mail(page, monkeypatch):
    """Owner, 2 October: "why is the agent going to Gmail for verification with the page showing" -- after Create
    Account the page was a plain Sign In form; the sign-in was refused, and the agent searched the mail for three
    minutes for a verification email the site never mentioned (the account already existed). A refusal is not a
    request to verify: the mail is read only when the page asks."""
    refuse = ("document.getElementById('alert').textContent = 'You may have entered the wrong email address"
              " or password or your account might be locked.';")
    body = create_page(EMAIL_BOXES["workday"], after_create=SIGN_IN_AFTER_CREATE,
                       extra=WIRE_SIGN_IN.replace("ON_SIGN_IN", refuse))
    serve(page, body)
    a = assistant_for(monkeypatch, WorkdayAdapter())
    looks = []
    monkeypatch.setattr(a, "verify_account_by_email_link", lambda page, wait_seconds=150: looks.append(1) or False)
    assert a.fill_create_account_form(page, EMAIL) is False
    assert page.evaluate("window.signins") == 1 and looks == []


def test_a_refused_sign_in_that_says_verify_opens_the_link_then_signs_in(page, monkeypatch):
    """The same refusal, on a page that says the account must be verified: the link is opened, then the sign-in."""
    refuse = ("if (window.signins >= 2) { document.body.innerHTML = '<h1>Candidate Home</h1>'; } else {"
              " document.body.insertAdjacentHTML('afterbegin', '<h2>Verify your account</h2><p>Verify your email"
              " address before you sign in.</p>'); }")
    body = create_page(EMAIL_BOXES["workday"], after_create=SIGN_IN_AFTER_CREATE,
                       extra=WIRE_SIGN_IN.replace("ON_SIGN_IN", refuse))
    serve(page, body)
    a = assistant_for(monkeypatch, WorkdayAdapter())
    looks = []

    def verify(page, wait_seconds=150):
        looks.append(wait_seconds)
        login_guard.clear_hold(HOST, EMAIL)        # what opening the link does
        return True
    monkeypatch.setattr(a, "verify_account_by_email_link", verify)
    a.fill_create_account_form(page, EMAIL)
    assert looks and page.evaluate("window.signins") == 2


def test_a_refused_sign_in_goes_to_the_reset_not_the_mail_when_the_page_asks_nothing(page, monkeypatch):
    """recover_rejected_sign_in: the verification email first only when the page says to verify."""
    serve(page, sign_in_page(EMAIL_BOXES["workday"]))
    a = assistant_for(monkeypatch, WorkdayAdapter())
    steps = []
    monkeypatch.setattr(a, "verify_account_by_email_link", lambda page, wait_seconds=150: steps.append("verify") or True)
    monkeypatch.setattr(a, "_open_sign_in", lambda page: True)
    monkeypatch.setattr(a, "attempt_auto_login", lambda *args, **kw: steps.append("sign in") or True)
    monkeypatch.setattr(a, "_reset_password_with_emailed_code", lambda *args: steps.append("reset") or False)
    assert a.recover_rejected_sign_in(page, EMAIL) is False
    assert steps == ["reset"]


@pytest.mark.parametrize("body, asks", [
    ('<h2>Sign In</h2><label>Email Address <input type=text></label><label>Password <input type=password></label>'
     '<button>Sign In</button>', ""),
    ("<h2>Verify your account</h2><p>We have sent you an email. Verify your email address before you sign in.</p>",
     "link"),
    ('<h2>Enter the verification code</h2><p>We sent a verification code to your email.</p>'
     '<label>Verification code <input type=text></label><button>Verify</button>', "code"),
])
def test_the_mail_is_read_only_when_the_page_asks_for_it(page, body, asks):
    """Crescent Energy, 1 October: the page asked for nothing and the agent spent two minutes in Gmail first."""
    from browser_automation import JobApplicationAssistant
    page.set_content(f"<html><body>{body}</body></html>")
    assert JobApplicationAssistant._page_asks_to_verify(page) == asks


KEYED_SIGN_IN = """<html><body><h2>Sign In</h2><div id=alert role=alert></div>
<label for=se>Email Address*</label><input type=text id=se data-automation-id=email>
<label for=sp>Password*</label><input type=password id=sp>
<button id=go>Sign In</button>
<script>
  // Like a React form: the password it sends is the one it registered from keystrokes, not the box's raw value.
  let typed = '';
  const pw = document.getElementById('sp');
  pw.addEventListener('keydown', e => { if (e.key.length === 1) typed += e.key; else if (e.key === 'Backspace') typed = ''; });
  window.sent = null;
  document.getElementById('go').addEventListener('click', () => {
    window.sent = typed;
    if (typed === 'Corr3ct!Horse#9') { document.body.innerHTML = '<h1>Candidate Home</h1>'; }
    else { document.getElementById('alert').textContent =
           'You may have entered the wrong email address or password or your account might be locked.'; }
  });
</script></body></html>"""


def test_the_sign_in_password_is_typed_so_a_react_form_registers_it(page, monkeypatch):
    """Waystar and Crescent Energy (Workday), 1 October: the agent's sign-in was refused, the owner's -- typed by
    hand, same password -- went through. A value set in one go was not what the form sent."""
    serve(page, KEYED_SIGN_IN)
    a = assistant_for(monkeypatch)
    assert a.attempt_auto_login(page, EMAIL, "Corr3ct!Horse#9", create_if_missing=False) is True
    assert page.evaluate("window.sent") == "Corr3ct!Horse#9"


def test_the_sign_in_never_types_into_a_create_account_form(page, monkeypatch):
    """Waystar, 1 October: the create form also holds an 'Already have an account? Sign In' link; read as a sign-in
    form, it got the email and password typed in and its Sign In link pressed."""
    serve(page, create_page(EMAIL_BOXES["workday"]))
    a = assistant_for(monkeypatch, WorkdayAdapter())
    assert a.attempt_auto_login(page, EMAIL, PASSWORD, create_if_missing=False) is False
    assert page.evaluate("[...document.querySelectorAll('input[type=password]')].every(e => !e.value)")
