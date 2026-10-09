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

# P0-B2, 7 October 2026: ACCOUNT_CODE (page_agent.complete_account_code) matched a code box by
# words like "verification code"/"one-time code"/"otp" alone, with nothing excluding a code the
# page itself says was sent by SMS/text or must come from an authenticator app -- channels
# emailed_codes.why_not's own permission (the owner's mail) has nothing to do with. A page reading
# "Enter the one-time code we texted to your phone" matched ACCOUNT_CODE's wording and would have
# had passcode_from_gmail search an inbox the real code was never going to reach. Checked before
# any such read, in the one place that already governs whether the agent may read a code at all.
#
# A follow-up, also 7 October 2026, found the first version of this check unsafe in the opposite
# direction: it was given the WHOLE page snapshot, so an ordinary, unrelated "Would you like
# application updates via SMS?" consent checkbox anywhere on the same page as a genuinely
# email-delivered code blocked the read that should have been allowed. Every caller now passes
# only the text local to the matched code control (its own label/container plus the explanatory
# text immediately around it -- see page_agent._nearby_code_text()), never the full snapshot.
#
# Two vocabularies, not three: NON_EMAIL_CHANNEL_CORE is safe to match at ANY scope, including
# page-wide (account_state.py's own MFA_REQUIRED detection imports and reuses it directly, so the
# two paths cannot define this core vocabulary differently again) -- its words are specific and
# rare enough that an unrelated application question is not mistaken for them. The broader, more
# generic words below (bare "sms"/"text message"/"phone number") are NOT safe page-wide (confirmed
# by the P0-B2 saved-page audit: 39 real pages across seven employers used exactly this wording as
# an ordinary consent question, not a second factor) -- they are matched only once evidence is
# already scoped to a verification-code control's own local context, where that risk does not
# apply the same way.
#
# A further correction, 8 October 2026: "two-factor"/"2-step verification" were removed from this
# vocabulary entirely. They name an AUTHENTICATION MODE ("a second factor is required"), never a
# DELIVERY/COMPLETION MECHANISM ("how that factor reaches you") -- and a second factor can
# legitimately be delivered by email, which this project is authorized to read ("Two-factor
# authentication. We emailed your verification code." must read EMAIL, not NON_EMAIL). Only the
# actual mechanism -- an authenticator app, TOTP, Authy, Google Authenticator, a security key, a
# push approval, or SMS/phone delivery (the broader, local-only vocabulary below) -- determines
# whether the agent has an authorized way to complete the step; the mode name contributes nothing
# to that question and is never matched here again.
NON_EMAIL_CHANNEL_CORE = re.compile(
    r"\bauthenticator(?:\s+app)?\b|\bgoogle authenticator\b|\bauthy\b|\btotp\b|\bsecurity key\b|"
    r"\bhardware key\b|\bpush notification\b|"
    r"approve (?:the )?(?:sign.?in|request) (?:on|in|from) your",
    re.IGNORECASE)
_NON_EMAIL_CHANNEL_LOCAL_ONLY = re.compile(
    r"\btext message\b|\bsms\b|\btexted\b|\bphone number\b|\bmobile (?:phone|number)\b",
    re.IGNORECASE)
_EMAIL_CHANNEL = re.compile(r"\be-?mail\w*\b|\binbox\b", re.IGNORECASE)
# A masked or full email address shown beside the code ("j***@example.com", "jo***@x.com"):
# positive evidence the code is tied to an email address, without ever needing the real one.
_MASKED_EMAIL = re.compile(r"[\w.+\-*]{1,40}@[\w.\-]+\.\w{2,}")


def code_channel(context: str) -> str:
    """"EMAIL", "NON_EMAIL", or "UNKNOWN" -- a verification code's own delivery channel, decided
    only from evidence local to the matched code control (its own label, container, and any
    clearly related nearby text) -- never from unrelated wording elsewhere on the page.

    "NON_EMAIL": the local context names SMS/phone/text delivery, or says the code must come from
    an authenticator app, TOTP, Authy, Google Authenticator, a security key, or a push approval --
    channels the agent has no authorized way to read -- AND no email evidence also appears in that
    same local context.

    "EMAIL": the local context says the code was emailed, sent to the owner's inbox, or shows a
    (masked or full) email address, with no non-email channel also named.

    "UNKNOWN": either the local context names no channel at all, or it names both an email and a
    non-email channel at once (self-contradictory -- never guessed). code_channel_is_email() is
    the policy layer that decides what to do about "UNKNOWN"; this function only reports the
    evidence, honestly, without picking a side when the evidence does not support one."""
    text = context or ""
    non_email = bool(NON_EMAIL_CHANNEL_CORE.search(text)) or bool(_NON_EMAIL_CHANNEL_LOCAL_ONLY.search(text))
    email = bool(_EMAIL_CHANNEL.search(text)) or bool(_MASKED_EMAIL.search(text))
    if non_email and email:
        return "UNKNOWN"
    if non_email:
        return "NON_EMAIL"
    if email:
        return "EMAIL"
    return "UNKNOWN"


def code_channel_is_email(context: str) -> bool:
    """False when code_channel(context) is "NON_EMAIL", or when it is "UNKNOWN" because the local
    context names both an email and a non-email channel at once -- a self-contradictory context is
    never read, the same as a confirmed non-email one; CLAUDE.md's existing policy against
    guessing applies here too. True when code_channel(context) is "EMAIL", or when it is "UNKNOWN"
    because the local context names no channel at all -- this project's existing, deliberate
    default for an unlabeled code step (most real verification-code steps never state their
    channel, and the overwhelming majority of those are email), preserved here unchanged rather
    than treated as a new reason to refuse.

    The agent must never guess a verification code for a channel it cannot read -- never an SMS
    code, never a TOTP/authenticator code, never a security-key approval -- consistent with
    CLAUDE.md's existing policy against inventing credential-adjacent mechanisms."""
    channel = code_channel(context)
    if channel == "NON_EMAIL":
        return False
    if channel == "EMAIL":
        return True
    # UNKNOWN: distinguish "no channel named at all" (the preserved default -- allowed) from
    # "both an email and a non-email channel named at once" (contradictory -- never guessed),
    # since code_channel() deliberately collapses both into the same honest "no clear answer"
    # report and leaves the policy choice to this function.
    text = context or ""
    non_email = bool(NON_EMAIL_CHANNEL_CORE.search(text)) or bool(_NON_EMAIL_CHANNEL_LOCAL_ONLY.search(text))
    email = bool(_EMAIL_CHANNEL.search(text)) or bool(_MASKED_EMAIL.search(text))
    return not (non_email and email)


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
