"""A harmless 'Read More' in the account form must not stop the Create Account click (f171).

Marathon's Workday Create Account form carries a 'Read More' button that expands the consent text. The guard authorises
a credential form only when every other button in it is a known non-final label; 'Read More' was not, so the
account-creation click was never authorised and the run stopped: 'the account-creation click could not be confirmed'.
An unknown action button ('Confirm & Send') still blocks the authorisation.
"""
import pytest

from submission_guard import SubmissionGuardV0
from test_account_creation_gateway import browser, page, serve  # noqa: F401

WORKDAY = """<form onsubmit="window.created=(window.created||0)+1; return false">
<label>Email Address<input type="text" aria-label="Email Address" value="owner@example.com"></label>
<label>Password<input type="password" aria-label="Password" value="Synthetic-Password1!"></label>
<label>Verify New Password<input type="password" aria-label="Verify New Password" value="Synthetic-Password1!"></label>
<p>I agree to creating this account.</p>
<button type="button">{extra}</button>
<label><input type="checkbox" checked> Yes, I consent</label>
<div role="button" aria-label="Create Account" tabindex="0" data-automation-id="click_filter" style="display:block;width:120px;height:32px"
     onclick="this.closest('form').requestSubmit()"></div>
<button type="submit" aria-hidden="true" tabindex="-1">Create Account</button></form>"""


@pytest.mark.parametrize("extra", ["Read More", "Read more", "Show More", "Learn More"])
def test_a_read_more_button_does_not_stop_the_account_click(page, extra):
    serve(page, WORKDAY.format(extra=extra))
    guard = SubmissionGuardV0(page.context)
    assert guard.click_account_creation(page, page.locator("[data-automation-id=click_filter]"))
    assert page.evaluate("window.created") == 1


def test_an_unknown_action_button_still_blocks_the_account_click(page):
    serve(page, WORKDAY.format(extra="Confirm &amp; Send"))
    guard = SubmissionGuardV0(page.context)
    assert not guard.click_account_creation(page, page.locator("[data-automation-id=click_filter]"))
    assert page.evaluate("window.created") is None
