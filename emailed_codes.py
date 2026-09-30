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
The same rule covers the one link the agent may open from the owner's mail (the owner's decision of 30 September
2026): the link a site emails to verify an account it has just made for the owner's email -- and only a link that
leads back to that same site (verification_link_ok). A password-reset link is still the owner's.
Every reader of the mail goes through why_not(): passcode_from_gmail and verification_link_from_gmail ask it before
opening the mail, so no caller can go around it. Callers pass `captcha` and `email` by name: a run that is reloaded while this
module is already loaded keeps the earlier copy of it.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

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


_VERIFY_WORDS = re.compile(r"verif|activat|confirm|validat", re.IGNORECASE)
_RESET_WORDS = re.compile(r"reset|forgot|password", re.IGNORECASE)


def _site_of(host: str) -> str:
    """The site a host belongs to: its last two labels (ciena.wd5.myworkdayjobs.com -> myworkdayjobs.com)."""
    return ".".join((host or "").lower().split(".")[-2:])


def unwrap(link: str) -> str:
    """Gmail may hand a link through google.com/url?q=...: the address it leads to."""
    parsed = urlparse(link or "")
    if parsed.netloc.endswith("google.com") and parsed.path == "/url":
        return (parse_qs(parsed.query).get("q") or parse_qs(parsed.query).get("url") or [""])[0]
    return link or ""


def verification_link_ok(link: str, text: str, site_url: str) -> bool:
    """Whether an emailed link is one the agent may open: https, on the same site as the account it verifies (never a
    tracking redirect or anyone else's address), and saying it verifies or activates the account -- never a
    password reset."""
    link = unwrap(link)
    parsed = urlparse(link)
    if parsed.scheme != "https" or not parsed.netloc:
        return False
    site_host = urlparse(site_url or "").netloc.lower()
    if _site_of(parsed.netloc) != _site_of(site_host):
        return False
    # A vendor that hosts many employers (ciena.wd5.myworkdayjobs.com): the link must be this employer's -- the same
    # host, or one naming the same tenant -- not another employer's verification on the same vendor.
    import job_sources
    if parsed.netloc.lower() != site_host and any(v in site_host for v in job_sources.ATS_VENDORS)             and site_host.split(".")[0] not in link.lower():
        return False
    words = f"{text or ''} {parsed.path} {parsed.query}"
    return bool(_VERIFY_WORDS.search(words)) and not _RESET_WORDS.search(f"{text or ''} {parsed.path}")
