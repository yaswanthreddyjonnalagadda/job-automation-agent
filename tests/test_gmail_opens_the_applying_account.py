"""The agent opens the mailbox of the account it applies with, not whichever Google account comes first (f182).

The agent read verification links and one-time codes from mail.google.com/mail/u/0/, the first Google account signed in
to its browser. If that was another account (the owner has more than one), it searched the wrong inbox and found no link.
Gmail opens a given account with ?authuser=<address> (checked against real Gmail: the form /mail/u/<address>/ answers
"Temporary Error"), and shows a sign-in instead when that account is not signed in -- which the readers already treat as
'this browser isn't signed in to Gmail'.
"""
from types import SimpleNamespace

import pytest

from browser_automation import JobApplicationAssistant


def assistant(ats_email="", profile_email=""):
    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a._config = SimpleNamespace(ats_email=ats_email)
    a._profile = SimpleNamespace(email=profile_email)
    return a


def test_the_account_the_job_accounts_use_is_the_one_opened():
    url = assistant("jobs.owner@gmail.com", "other@gmail.com")._gmail_search_url("verify")
    assert url == "https://mail.google.com/mail/?authuser=jobs.owner%40gmail.com#search/verify"


def test_the_profile_email_is_used_when_no_ats_email_is_set():
    assert "?authuser=me%40gmail.com#" in assistant("", "me@gmail.com")._gmail_search_url("q")


def test_with_no_known_address_the_first_account_is_used_as_before():
    assert "/mail/u/0/#" in assistant("", "")._gmail_search_url("q")


@pytest.mark.parametrize("odd", ["a b@gmail.com", "x/../y@gmail.com", "evil@gmail.com/#x", "a@b.com?authuser=1"])
def test_an_address_that_is_not_a_plain_mailbox_is_never_put_in_the_address(odd):
    assert "/mail/u/0/#" in assistant(odd, "")._gmail_search_url("q")


def test_every_mail_reader_goes_through_that_one_function():
    import inspect
    from email_verification import EmailVerificationMixin
    # The readers live in the mixin the assistant inherits (email_verification.py); the rule covers both.
    source = inspect.getsource(JobApplicationAssistant) + inspect.getsource(EmailVerificationMixin)
    assert source.count("mail/u/0/#search/") == 1           # only the fallback inside _gmail_search_url
    assert source.count("self._gmail_search_url(") >= 3      # the three readers: confirmation, passcode, verification link


def test_the_address_form_that_gmail_rejects_is_not_used():
    """Real Gmail answered 'Temporary Error' to /mail/u/<address>/ (17 October 2026): it must never be built."""
    url = assistant("jobs.owner@gmail.com", "")._gmail_search_url("q")
    assert "/mail/u/jobs.owner" not in url
