"""
The rules the assistant must not break, in one place.

Every fill/click path in browser_automation.py asks this module before acting,
so a new form handler cannot quietly acquire the ability to sign an attestation,
overwrite something the user typed, or type a Google password.

The rules (from the user's specification, 2026-09-15):
  * never accept legal attestations or electronic signatures automatically
  * never guess an answer -- only values the profile or resume actually support
  * never overwrite an answer the user entered
  * never enter Google account passwords
  * never work around a CAPTCHA
  * never claim resume or profile information that isn't supported
  * never submit -- and never click a control that submits an application
"""

from __future__ import annotations

import re
from typing import Iterable, Optional
from urllib.parse import urlparse

# --------------------------------------------------------------------------
# Attestations and signatures: always the user's to give
# --------------------------------------------------------------------------
# Wording that makes a control a legal declaration rather than a form answer.
ATTESTATION_PATTERNS = (
    r"\bi (hereby )?(certify|attest|declare|affirm|swear|consent to be bound)\b",
    r"\bunder penalt(y|ies) of perjury\b",
    r"\b(true|accurate) and (correct|complete)\b",
    r"\bby (typing|signing|entering) my name\b",
    r"\belectronic(ally)? sign(ature|ed|ing)?\b",
    r"\be-?signature\b",
    r"\btyped signature\b",
    r"\bsignature\b",
    r"\bi agree to the (terms|statement) (of|and) (agreement|conditions)\b",
    r"\bstatement of agreement\b",
    r"\bi acknowledge that .{0,60}(true|accurate|complete|grounds for)\b",
    r"\bgrounds for (dismissal|termination|disqualification)\b",
)
_ATTESTATION_RE = re.compile("|".join(ATTESTATION_PATTERNS), re.IGNORECASE)

# Consent to a privacy/data-protection notice is not an attestation: it grants
# no facts about the candidate, and employer sites gate account creation and
# the application form behind it.
_PRIVACY_ONLY_RE = re.compile(
    r"privacy (policy|notice|statement|agreement)|data (privacy|protection|processing)|"
    r"cookie|receiv(e|ing) (job|career|marketing) (recommendations|alerts|emails)|contact me about",
    re.IGNORECASE,
)


def is_attestation(text: str) -> bool:
    """True when a checkbox/field label is a legal declaration or a signature.

    A privacy notice on its own is not: accepting one states nothing about the
    candidate. Wording that does both is treated as an attestation.
    """
    text = " ".join((text or "").split())
    if not text or not _ATTESTATION_RE.search(text):
        return False
    if _PRIVACY_ONLY_RE.search(text) and not re.search(
        r"\bcertify|perjury|true and (correct|complete)|signature|by typing my name\b", text, re.IGNORECASE
    ):
        return False
    return True


def is_privacy_consent(text: str) -> bool:
    """True for a privacy/data-protection consent the agent may accept."""
    text = " ".join((text or "").split())
    return bool(text and _PRIVACY_ONLY_RE.search(text) and not is_attestation(text))


# --------------------------------------------------------------------------
# Controls that submit an application: never clicked by the agent
# --------------------------------------------------------------------------
SUBMIT_LABEL_RE = re.compile(
    r"^\s*(submit( my| your| this)?( application| profile| candidacy)?|apply( now| for this job)?|"
    r"send application|save and submit|finish( and submit)?|complete application)\s*$",
    re.IGNORECASE,
)


def is_submit_label(text: str) -> bool:
    """True when a button's label means 'send this application to the employer'."""
    return bool(SUBMIT_LABEL_RE.match(" ".join((text or "").split())))


# --------------------------------------------------------------------------
# Passwords: employer ATS accounts only
# --------------------------------------------------------------------------
# Sites whose accounts are never scripted: their terms forbid it (LinkedIn,
# Indeed, Dice) or the password is the user's whole identity (Google, Apple,
# Microsoft account sign-in).
BLOCKED_PASSWORD_DOMAINS = (
    "linkedin.com", "indeed.com", "dice.com",
    "accounts.google.com", "google.com", "appleid.apple.com", "login.microsoftonline.com",
    "login.live.com", "facebook.com", "github.com",
)


def password_allowed(url: str) -> bool:
    """False where the agent must never type a password (see the list above)."""
    host = urlparse(url or "").netloc.lower()
    return not any(blocked in host for blocked in BLOCKED_PASSWORD_DOMAINS)


# --------------------------------------------------------------------------
# CAPTCHA / human verification: detected, never solved or bypassed
# --------------------------------------------------------------------------
CAPTCHA_FRAME_SELECTOR = (
    "iframe[src*='recaptcha/api2/bframe'], iframe[src*='recaptcha/enterprise/bframe'], "
    "iframe[title*='challenge' i], iframe[src*='hcaptcha'], iframe[src*='challenges.cloudflare.com'], "
    "iframe[src*='funcaptcha'], iframe[src*='arkoselabs']"
)
CAPTCHA_TEXT_RE = re.compile(
    r"verify (that )?you are (a )?human|i'm not a robot|complete the (captcha|security check)|"
    r"security check|prove you('| a)re human|click the object that does not fit",
    re.IGNORECASE,
)


def captcha_visible(page) -> bool:
    """True when a CAPTCHA challenge is on screen. The agent then stops and
    hands over -- it never attempts to solve or bypass one."""
    try:
        frames = page.locator(CAPTCHA_FRAME_SELECTOR)
        for i in range(min(frames.count(), 8)):
            frame = frames.nth(i)
            box = frame.bounding_box()
            if frame.is_visible() and box and box["height"] > 60 and box["width"] > 60:
                return True
        body = page.locator("body").inner_text(timeout=5_000) or ""
        return bool(CAPTCHA_TEXT_RE.search(body))
    except Exception:
        return False


# --------------------------------------------------------------------------
# Never overwrite what the user typed
# --------------------------------------------------------------------------
class AgentValues:
    """Remembers every value the agent itself put on a page.

    A field that already holds something is only rewritten when the agent is
    the one that put it there (correcting its own earlier answer). Anything
    else -- typed by the user, or pre-filled by the site from the resume --
    is left exactly as it is.
    """

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    @staticmethod
    def _key(page, ref: str) -> str:
        try:
            host = urlparse(page.url).netloc.lower()
        except Exception:
            host = ""
        return f"{host}|{ref}"

    def record(self, page, ref: str, value: str) -> None:
        self._values[self._key(page, ref)] = (value or "").strip()

    def is_ours(self, page, ref: str, current_value: str) -> bool:
        stored = self._values.get(self._key(page, ref))
        return stored is not None and stored == (current_value or "").strip()

    def may_write(self, page, ref: str, current_value: str) -> bool:
        """True when the field is empty, or holds a value the agent wrote."""
        return not (current_value or "").strip() or self.is_ours(page, ref, current_value)


# --------------------------------------------------------------------------
# Never claim unsupported resume information
# --------------------------------------------------------------------------
_CLAIM_RE = re.compile(r"\b\d[\d,]*\.?\d*\s?%|\$\s?\d[\d,.]*\s?[KkMm]?\b|\b\d{4}\b|\b\d[\d,]{2,}\b")
_ACRONYM_RE = re.compile(r"\b[A-Z]{2,}(?:-[A-Z0-9]+)?\b")


def unsupported_claims(source_text: str, generated_text: str, extra_allowed: Iterable[str] = ()) -> list[str]:
    """Figures, dates and certification acronyms in generated text that do not
    appear in the candidate's real resume.

    Tailoring rewrites wording, which is fine; inventing a metric, a year or a
    certification is not. Anything returned here must not be sent to an
    employer without the user's say-so.
    """
    haystack = " ".join((source_text or "").split()).lower()
    allowed = {a.lower() for a in extra_allowed}
    # Common words that read as acronyms but assert nothing about the candidate.
    ignore = {
        "AWS", "GCP", "AI", "ML", "IT", "US", "USA", "PDF", "CV", "HR", "API", "SQL", "VPN", "LAN",
        "WAN", "DNS", "TCP", "IP", "SLA", "MTTR", "CI", "CD", "OK", "AM", "PM", "EOD", "JD", "EEO",
    }
    missing: list[str] = []
    for match in _CLAIM_RE.finditer(generated_text or ""):
        token = match.group(0).strip()
        plain = token.replace(",", "").replace("$", "").replace(" ", "").lower()
        if plain in {"", "0"} or len(plain) < 2:
            continue
        if plain in haystack.replace(",", "").replace("$", "") or token.lower() in haystack:
            continue
        if token.lower() in allowed:
            continue
        missing.append(token)
    for match in _ACRONYM_RE.finditer(generated_text or ""):
        token = match.group(0)
        if token in ignore or token.lower() in haystack or token.lower() in allowed or len(token) < 3:
            continue
        missing.append(token)
    # De-duplicate, keep order.
    seen, unique = set(), []
    for item in missing:
        if item.lower() not in seen:
            seen.add(item.lower())
            unique.append(item)
    return unique


# --------------------------------------------------------------------------
# Hand-over decision
# --------------------------------------------------------------------------
def handover_status(report: dict) -> tuple[str, str]:
    """(status, message) for a filled application, from its validation report.

    ready_to_submit   -- nothing outstanding; the user reviews and clicks Submit
    needs_user_review -- blanks, form errors, a CAPTCHA or an attestation remain
    """
    problems: list[str] = []
    for label, items in (
        ("required fields still blank", report.get("required_still_blank") or []),
        ("errors shown by the form", report.get("errors_shown") or []),
        ("questions the agent could not answer from your profile", report.get("unanswered_questions") or []),
        ("attestations or signatures for you to complete", report.get("attestations_pending") or []),
    ):
        if items:
            problems.append(f"{label}: " + "; ".join(str(i) for i in items[:5]))
    if report.get("captcha"):
        problems.append("a CAPTCHA is showing -- only you can complete it")

    if problems:
        return "needs_user_review", " | ".join(problems)
    return "ready_to_submit", "Every required field is filled and the form shows no errors."


def verification_status(evidence: Optional[str]) -> tuple[str, str]:
    """(status, note) after the user has submitted.

    Only actual evidence -- a confirmation page, the employer's applications
    list, or a confirmation email -- counts as submitted. Without it the
    application is flagged for the user rather than assumed successful.
    """
    if evidence:
        return "submitted", f"Submitted by you; {evidence}"
    return (
        "needs_user_review",
        "You left the application, but no confirmation page, portal entry or email was found -- "
        "check whether it went through.",
    )
