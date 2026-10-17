"""The agent opens the mailbox of the account it applies with, not whichever Google account comes first (f182).

The agent read verification links and one-time codes from mail.google.com/mail/u/0/, the first Google account signed in
to its browser. If that was another account (the owner has more than one), it searched the wrong inbox and found no link.
Gmail opens a given account by address (/mail/u/<address>/), and shows a sign-in instead when that account is not signed
in -- which the readers already treat as 'this browser isn't signed in to Gmail'.
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
    assert url == "https://mail.google.com/mail/u/jobs.owner@gmail.com/#search/verify"


def test_the_profile_email_is_used_when_no_ats_email_is_set():
    assert "/mail/u/me@gmail.com/#" in assistant("", "me@gmail.com")._gmail_search_url("q")


def test_with_no_known_address_the_first_account_is_used_as_before():
    assert "/mail/u/0/#" in assistant("", "")._gmail_search_url("q")


@pytest.mark.parametrize("odd", ["a b@gmail.com", "x/../y@gmail.com", "evil@gmail.com/#x", "a@b.com?authuser=1"])
def test_an_address_that_is_not_a_plain_mailbox_is_never_put_in_the_address(odd):
    assert "/mail/u/0/#" in assistant(odd, "")._gmail_search_url("q")


def test_every_mail_reader_goes_through_that_one_function():
    import inspect
    source = inspect.getsource(JobApplicationAssistant)
    assert source.count("mail/u/0/") == 0 or source.count("/mail/u/{account}/#search/") == 1
    assert source.count("self._gmail_search_url(") >= 3      # the three readers: confirmation, passcode, verification link
