"""One rule for when the agent may read a one-time code from the owner's mail.

Three places used to decide this for themselves: the code-entry step of a form (page_agent), the older
sign-in / account-setup step (browser_automation.complete_emailed_passcode) and the reset of an existing
account's password. Each checked some of the conditions and none checked all -- the reset never asked whether
the owner had allowed the agent to read their mail, and the code-entry step did not check the site was one the
agent may enter anything on.

The rule, in one place (the owner's decisions of 15 and 24 September 2026, and of 25 September for the last):
  * only where the owner has allowed the agent to read their mail (profile.check_gmail_for_confirmation);
  * only on employer sites (safety.password_allowed: never Google, Microsoft, Apple, LinkedIn, Indeed, Dice ...);
  * never on a page showing a CAPTCHA -- a challenge is the owner's to solve. A code a site says is "to confirm
    you're a human" (Greenhouse) is an emailed code like any other and the agent enters it: it proves the
    applicant controls the mailbox, which the owner has let the agent read. The rule goes by what is on the
    page, not by how the site words its code step (the wording once stopped a Praxis application one step
    short of done);
  * no more than MAX_CODE_READS reads for one account in 24 hours (login_guard);
  * never a code in a log line (passcode_from_gmail logs only that one was found).
Every reader of the mail goes through why_not(): passcode_from_gmail itself asks it before opening the mail,
so no caller can go around it. Callers pass `captcha` and `email` by name: a run that is reloaded while this
module is already loaded keeps the earlier copy of it.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

import login_guard
import safety


def why_not(profile, url: str, captcha: bool = False, email: str = "") -> Optional[str]:
    """None when the agent may read a code from the owner's mail for this page; else why not, in words for
    the owner (which the hand-over shows). `captcha` is whether a CAPTCHA is showing (safety.captcha_visible)."""
    host = urlparse(url or "").netloc.lower()
    if profile is None or not getattr(profile, "check_gmail_for_confirmation", False):
        return ("the agent has not been allowed to read your mail (check_gmail_for_confirmation in your "
                "profile): enter the code yourself")
    if not safety.password_allowed(url):
        return f"{host} is not a site the agent enters codes on: enter the code yourself"
    if captcha:
        return "a CAPTCHA is showing: that one is yours to solve, and the agent does not go on to the code past it"
    held = login_guard.may_read_code(host, email or getattr(profile, "email", ""))
    if held:
        return held
    return None
