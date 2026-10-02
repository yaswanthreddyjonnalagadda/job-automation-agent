"""The account step as one loop: act, read what the page says, decide again (owner's plan, 2 October 2026).

Two classes of failure, seen across 40 account attempts on nine portals:
  * A result assumed instead of read. After Create Account the agent logged "created, not yet verified" and went on
    to sign in, whatever the page showed, and kept nothing of it (Waystar on Workday, six times, 1-2 October).
  * Every portal treated as one employer's. Dayforce, UKG and iCIMS keep one account for all their employers behind
    a shared login: creating first there only meets "already exists" and spends the day's creations.
And a refused password for an account the agent has no record of stopped for the owner, when the site itself can be
asked: creating the account either makes it or the site says it exists, and the owner's reset rule then applies.
"""
import itertools
from dataclasses import fields

import pytest

import account_state as acc
import browser_automation
from sites import accounts

DAYFORCE = "https://dfid.dayforcehcm.com/globalidentity/account/login"
WORKDAY = "https://waystar.wd1.myworkdayjobs.com/en-US/Waystar/login"


# --- whose account a page is for ------------------------------------------------------------------

@pytest.mark.parametrize("url, portal", [
    (DAYFORCE, "Dayforce"),
    ("https://jobs.dayforcehcm.com/en-US/lumos/CANDIDATEPORTAL/jobs/9416/apply", "Dayforce"),
    ("https://login.icims.com/u/login/identifier?state=x", "iCIMS"),
    ("https://signin-us.ultipro.com/u/login", "UKG"),
    (WORKDAY, ""),
    ("https://career2.successfactors.eu/career?company=igt", ""),
    ("https://career-schwab.icims.com/jobs/126880/login", ""),      # the employer's own iCIMS page, before the shared login
    ("", ""),
])
def test_a_shared_login_is_told_from_one_employers(url, portal):
    assert accounts.shared_portal(url) == portal


def test_a_shared_portals_account_is_recorded_once_for_every_employer():
    assert accounts.record_key("Segra", DAYFORCE) == accounts.record_key("Lumos", DAYFORCE)
    assert accounts.record_key("Waystar", WORKDAY) == "Waystar"


# --- the decision table, over every memory it can hold ----------------------------------------------

FLAGS = [f.name for f in fields(acc.Memory) if f.type in (bool, "bool")]


def memories(**fixed):
    """Every combination of the table's yes/no memories, with `fixed` held."""
    free = [name for name in FLAGS if name not in fixed]
    for values in itertools.product((False, True), repeat=len(free)):
        yield acc.Memory(**dict(zip(free, values)), **fixed)


@pytest.mark.parametrize("kind", [acc.CREATE_FORM, acc.SIGN_IN_FORM, acc.CHOOSER, acc.EMAIL_FIRST])
def test_on_a_shared_login_the_agent_never_creates_before_it_has_tried_to_sign_in(kind):
    state = acc.AccountState(kind, can_create=True, password_boxes=2 if kind == acc.CREATE_FORM else 1)
    # refused_before is a sign-in tried in an earlier run.
    for memory in memories(signed_in_tried=False, refused_before=False):
        memory.shared_portal = "Dayforce"
        assert acc.next_step(state, memory).action not in (acc.CREATE, acc.OPEN_CREATE), memory


def test_on_one_employers_site_the_account_is_still_created_first():
    state = acc.AccountState(acc.SIGN_IN_FORM, can_create=True, password_boxes=1)
    assert acc.next_step(state, acc.Memory()).action == acc.OPEN_CREATE
    assert acc.next_step(acc.AccountState(acc.CREATE_FORM, password_boxes=2), acc.Memory()).action == acc.CREATE


def test_a_shared_login_goes_from_its_create_form_to_sign_in():
    step = acc.next_step(acc.AccountState(acc.CREATE_FORM, password_boxes=2), acc.Memory(shared_portal="UKG"))
    assert step.action == acc.OPEN_SIGN_IN


def test_a_refused_password_is_never_typed_again():
    for kind in (acc.WRONG_PASSWORD,):
        for can_create in (False, True):
            for memory in memories():
                step = acc.next_step(acc.AccountState(kind, can_create=can_create), memory)
                assert step.action != acc.SIGN_IN, memory


def test_a_refused_password_for_an_account_on_no_record_asks_the_site_by_creating_it():
    refused = acc.AccountState(acc.WRONG_PASSWORD, "wrong email address or password", can_create=True)
    assert acc.next_step(refused, acc.Memory(signed_in_tried=True)).action == acc.OPEN_CREATE
    # Already created this run: the site's answer is in; the owner is told what it said.
    step = acc.next_step(refused, acc.Memory(signed_in_tried=True, created=True))
    assert step.action == acc.FOR_OWNER and "wrong email address or password" in step.why


def test_an_account_the_site_says_exists_and_refused_is_reset_by_the_owners_rule():
    exists = acc.AccountState(acc.ACCOUNT_EXISTS, "an account with this email already exists")
    assert acc.next_step(exists, acc.Memory()).action == acc.OPEN_SIGN_IN
    assert acc.next_step(exists, acc.Memory(signed_in_tried=True)).action == acc.RESET_PASSWORD
    assert acc.next_step(exists, acc.Memory(signed_in_tried=True, reset_tried=True)).action == acc.FOR_OWNER


def test_a_password_refused_in_an_earlier_run_with_no_record_asks_the_site_by_creating_it():
    form = acc.AccountState(acc.SIGN_IN_FORM, can_create=True, password_boxes=1)
    assert acc.next_step(form, acc.Memory(refused_before=True)).action == acc.OPEN_CREATE
    assert acc.next_step(form, acc.Memory(refused_before=True, account_exists=True)).action == acc.RESET_PASSWORD


# --- the result of a press is read, not assumed -------------------------------------------------------

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


from test_account_email_first import EMAIL, EMAIL_BOXES, assistant_for, create_page, serve  # noqa: E402
from sites.workday import WorkdayAdapter  # noqa: E402


def recorded(a, monkeypatch):
    made = []
    monkeypatch.setattr(a, "remember_account", lambda page, email, method: made.append(method))
    return made


def test_a_create_press_that_leads_nowhere_records_no_account(page, monkeypatch):
    serve(page, create_page(EMAIL_BOXES["workday"], after_create="''"))
    a = assistant_for(monkeypatch, WorkdayAdapter())
    made = recorded(a, monkeypatch)
    assert a.fill_create_account_form(page, EMAIL) is False
    assert made == []


def test_what_the_page_said_after_create_is_logged_and_saved(page, monkeypatch, caplog):
    after = "'<h2>Sign In</h2><div role=alert>Please verify your email before you sign in.</div>'"
    serve(page, create_page(EMAIL_BOXES["workday"], after_create=after))
    a = assistant_for(monkeypatch, WorkdayAdapter())
    recorded(a, monkeypatch)
    monkeypatch.setattr(a, "verify_account_by_email_link", lambda page, wait_seconds=150: False)
    caplog.set_level("INFO")
    a.fill_create_account_form(page, EMAIL)
    said = [r.getMessage() for r in caplog.records if "ACCOUNT_RESULT" in r.getMessage()]
    assert said and "Please verify your email before you sign in." in said[0]
    saved = list(browser_automation.ACCOUNT_STEPS.glob("*_create.txt"))
    assert saved and "Please verify your email" in saved[0].read_text(encoding="utf-8")


def test_an_exists_message_after_the_form_has_gone_signs_in(page, monkeypatch):
    after = "'<h2>Sign In</h2><div role=alert>An account with this email address already exists.</div>'"
    serve(page, create_page(EMAIL_BOXES["workday"], after_create=after))
    a = assistant_for(monkeypatch, WorkdayAdapter())
    made = recorded(a, monkeypatch)
    signed = []
    monkeypatch.setattr(a, "sign_in_to_existing_account", lambda page, email: signed.append(email) or True)
    assert a.fill_create_account_form(page, EMAIL) is True
    assert signed == [EMAIL] and made == []


def test_a_verify_message_after_create_opens_the_link_before_signing_in(page, monkeypatch):
    after = "'<h2>Account created</h2><p>We have sent you an email. Verify your email address to continue.</p>'"
    serve(page, create_page(EMAIL_BOXES["workday"], after_create=after))
    a = assistant_for(monkeypatch, WorkdayAdapter())
    made = recorded(a, monkeypatch)
    opened = []
    monkeypatch.setattr(a, "verify_account_by_email_link",
                        lambda page, wait_seconds=150: opened.append(wait_seconds) or False)
    a.fill_create_account_form(page, EMAIL)
    assert made == ["password (created, not yet verified)"]
    assert opened and opened[0] == 120            # asked straight away, not after a refused sign-in


# --- a bare "Yes, I consent" means the text above it (Marathon Petroleum, 2 October) ------------------

def consent_box(paragraphs):
    return (f"<div><div>{paragraphs}</div><button type=button>Read More</button></div>"
            '<div><input type="checkbox" id="c"><label for="c">Yes, I consent</label></div>')


CALLS_AND_TEXTS = ("<p>I agree to creating this account to allow me to apply for positions with Example Co.</p>"
                   "<p>By providing my phone number on my application or during creation of this account, I agree to "
                   "receive recurring calls and text messages, including by automated means, regarding my "
                   "application.</p>")


def test_a_consent_that_also_agrees_to_calls_and_texts_is_left_for_the_owner_before_create(page, monkeypatch):
    serve(page, create_page(EMAIL_BOXES["workday"], extra=consent_box(CALLS_AND_TEXTS)))
    a = assistant_for(monkeypatch, WorkdayAdapter())
    recorded(a, monkeypatch)
    assert a.fill_create_account_form(page, EMAIL) is False
    assert page.evaluate("window.creates") == 0                  # no account attempt spent on a form it cannot finish
    assert page.locator("#c").is_checked() is False
    assert "recurring calls and text messages" in a._account_form_held
    assert not getattr(a, "_login_paused", "")                    # no hold on the rest of the run's account steps
    # The owner ticks it and presses Continue: the form goes through.
    page.locator("#c").check()
    a.fill_create_account_form(page, EMAIL)
    assert page.evaluate("window.creates") == 1 and a._account_form_held == ""


def test_a_consent_only_to_creating_the_account_is_ticked(page, monkeypatch):
    only_account = "<p>I agree to creating this account to allow me to apply for positions with Example Co.</p>"
    serve(page, create_page(EMAIL_BOXES["workday"], extra=consent_box(only_account)))
    a = assistant_for(monkeypatch, WorkdayAdapter())
    recorded(a, monkeypatch)
    a.fill_create_account_form(page, EMAIL)
    assert page.evaluate("window.creates") == 1


def test_a_create_form_still_showing_after_the_attempt_stops_the_run_not_the_form_filler():
    import page_agent

    class Assistant:
        _last_account_result = ("create", acc.AccountState(acc.CREATE_FORM, form_error="Please check the box"), "x")
    agent = page_agent.PageAgent.__new__(page_agent.PageAgent)
    agent.assistant = Assistant()
    assert agent._create_still_showing(None) is True
    Assistant._last_account_result = ("create", acc.AccountState(acc.CODE_ENTRY), "")
    assert agent._create_still_showing(None) is False            # the code step is next, not a failure
