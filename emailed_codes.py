"""One rule for when the agent may read a one-time code from the owner's mail.

Three places used to decide this for themselves: the code-entry step of a form (page_agent), the older
sign-in / account-setup step (browser_automation.complete_emailed_passcode) and the reset of an existing
account's password. Each checked some of the conditions and none checked all -- the reset never asked whether
the owner had allowed the agent to read their mail or whether the code was one a site wants to prove a human is
applying, and the code-entry step did not check the site was one the agent may enter anything on.

The rule, in one place (the owner's decisions of 15 September and 24 September 2026):
  * only where the owner has allowed the agent to read their mail (profile.check_gmail_for_confirmation);
  * only on employer sites (safety.password_allowed: never Google, Microsoft, Apple, LinkedIn, Indeed, Dice ...);
  * never a code the site asks for to prove a human is applying, or on a page showing a CAPTCHA -- that one is
    the owner's to enter;
  * no more than MAX_CODE_READS reads for one account in 24 hours (login_guard);
  * never a code in a log line (passcode_from_gmail logs only that one was found).
Every reader of the mail goes through why_not(): passcode_from_gmail itself asks it before opening the mail,
so no caller can go around it.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

import login_guard
import safety

# A code a site asks for to prove a human is applying: never the agent's. Harbinger's says so beside its
# reCAPTCHA; Greenhouse says "enter the 8-character code to confirm you're a human".
HUMAN_CHECK = re.compile(
    r"confirm (that )?(you'?re|you are) (a )?human|prove (that )?you('re| are) (a )?human|"
    r"not a robot|human verification|verify (that )?you are (a )?human", re.IGNORECASE)


def why_not(profile, url: str, page_text: str = "", captcha: bool = False, email: str = "") -> Optional[str]:
    """None when the agent may read a code from the owner's mail for this page; else why not, in words for
    the owner (which the hand-over shows)."""
    host = urlparse(url or "").netloc.lower()
    if profile is None or not getattr(profile, "check_gmail_for_confirmation", False):
        return ("the agent has not been allowed to read your mail (check_gmail_for_confirmation in your "
                "profile): enter the code yourself")
    if not safety.password_allowed(url):
        return f"{host} is not a site the agent enters codes on: enter the code yourself"
    if captcha or HUMAN_CHECK.search(page_text or ""):
        return "the site asks for this code to prove a human is applying: that one is yours to enter"
    held = login_guard.may_read_code(host, email or getattr(profile, "email", ""))
    if held:
        return held
    return None
