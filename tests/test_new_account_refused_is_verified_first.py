"""
An account the agent has just created, then refused at sign-in, is verified from the mail first, then reset (f195).

R. L. Polk Mobility (Workday wd503), 18 October: the agent created the account, Workday showed neither Candidate Home nor
the application, the one sign-in got "You may have entered the wrong email address or password or your account might be
locked." and the run handed the sign-in page to the owner. On Workday a new account is refused with exactly that generic
wording until its email is verified -- the page never says "verify". The decision table did not count "created here a
moment ago" as an account the site knows, so neither owner-approved recovery (CLAUDE.md §5: the verification link from
Gmail, the reset to the ATS password) was tried.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import account_state as A  # noqa: E402
import config  # noqa: E402
import safety  # noqa: E402
from browser_automation import JobApplicationAssistant  # noqa: E402


def step(kind, **memory):
    return A.next_step(A.AccountState(kind, "You may have entered the wrong email address or password"), A.Memory(**memory))


def test_a_just_created_account_that_is_refused_is_verified_from_the_mail_first():
    assert step(A.WRONG_PASSWORD, created=True).action == A.VERIFY_BY_LINK


def test_then_its_password_is_reset_to_the_ats_password():
    assert step(A.WRONG_PASSWORD, created=True, verify_tried=True).action == A.RESET_PASSWORD


def test_then_it_is_the_owners():
    assert step(A.WRONG_PASSWORD, created=True, verify_tried=True, reset_tried=True).action == A.FOR_OWNER


def test_a_sign_in_form_refused_before_on_a_just_created_account_is_reset_not_handed_over():
    assert step(A.SIGN_IN_FORM, created=True, refused_before=True).action == A.RESET_PASSWORD


def test_an_account_nobody_knows_is_still_the_owners():
    assert step(A.WRONG_PASSWORD).action == A.FOR_OWNER


# --- the link step itself, on the page Workday shows (no "verify" wording) ---------------------------------------------

HOST = "tenant.wd503.myworkdayjobs.com"
REFUSED = ('<h1>Sign In</h1><div role="alert"><p>You may have entered the wrong email address or password or your '
           'account might be locked.</p></div><label>Email Address<input type=email value="owner@example.com"></label>'
           '<label>Password<input type=password></label><button>Sign In</button>')


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    with browser.new_context() as context:
        yield context.new_page()


def assistant():
    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a._profile = config.UserProfile(email="owner@example.com", check_gmail_for_confirmation=True)
    a._config = SimpleNamespace(ats_email="owner@example.com")
    a.values = safety.AgentValues()
    a.why_not_read_a_code = lambda _page: None
    return a


def test_the_mail_is_read_for_a_just_created_account_on_a_refused_sign_in(page):
    page.context.route(f"https://{HOST}/**", lambda route: route.fulfill(
        body="<h1>Your account has been verified</h1>" if "/activate/" in route.request.url else REFUSED))
    page.goto(f"https://{HOST}/en-US/Mobility/login")
    a = assistant()
    asked = []
    a.verification_link_from_gmail = lambda _p: asked.append(1) or f"https://{HOST}/en-US/Mobility/activate/token"
    assert a.verify_account_by_email_link(page, just_created=True) is True
    assert asked == [1]


def test_the_mail_stays_closed_on_a_refused_sign_in_for_an_account_not_made_here(page):
    page.set_content(REFUSED)
    a = assistant()
    a.verification_link_from_gmail = lambda _p: pytest.fail("the mail must stay closed")
    assert a.verify_account_by_email_link(page) is False
