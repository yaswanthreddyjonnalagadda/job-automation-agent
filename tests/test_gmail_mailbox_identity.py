"""Gmail can silently fall back to its default account despite authuser=email."""
import pytest

from test_emailed_code_rule import (  # noqa: F401
    ALLOWED, assistant_for, browser, context, employer_page,
)


@pytest.mark.parametrize("reader", ["passcode", "verification", "confirmation"])
def test_wrong_mailbox_is_not_read_and_sign_in_tab_stays_open(context, reader):
    html = ('<button aria-label="Google Account: Other (other@example.com)">Account</button>'
            '<table><tr class="zA"><td>Example ATS verification code 482913 '
            'Application received for Network Engineer</td></tr></table>')
    context.route("https://mail.google.com/**", lambda route: route.fulfill(body=html))
    context.route("https://accounts.google.com/**", lambda route: route.fulfill(body="<h1>Sign in</h1>"))
    page = employer_page(context)
    assistant = assistant_for(ALLOWED)
    if reader == "passcode":
        result = assistant.passcode_from_gmail(page, wait_seconds=5)
    elif reader == "verification":
        result = assistant.verification_link_from_gmail(page, wait_seconds=5)
    else:
        result = assistant.gmail_shows_confirmation(page, "Example ATS", "Network Engineer")
    assert not result
    assert "Sign in" in assistant._login_paused
    assert len(context.pages) == 2
    assert context.pages[-1].url.startswith("https://accounts.google.com/AddSession?")
    assert "login_hint=owner%40example.com" in context.pages[-1].url


@pytest.mark.parametrize("label, matches", [
    ("Google Account: Owner (OWNER@example.com)", True),
    ("Google Account: Other (other@example.com)", False),
    ("Google Account: Owner", False),
    ("Google Account: Owner (prefixowner@example.com)", False),
])
def test_visible_account_identity_must_match_exactly(context, label, matches):
    context.route("https://mail.google.com/**", lambda route: route.fulfill(
        body=f'<button aria-label="{label}">Account</button>'))
    context.route("https://accounts.google.com/**", lambda route: route.fulfill(body="Sign in"))
    page = context.new_page()
    page.goto("https://mail.google.com/mail/u/0/")
    assert assistant_for(ALLOWED)._gmail_mailbox_matches(page) is matches
