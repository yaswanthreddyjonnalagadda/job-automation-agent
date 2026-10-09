"""
The rules the assistant must not break, in one place.

Every fill/click path in browser_automation.py asks this module before acting,
so a new form handler cannot quietly acquire the ability to sign an attestation,
overwrite something the user typed, or type a Google password.

The rules (from the user's specification, 2026-09-15):
  * never accept legal attestations or electronic signatures automatically --
    unless the owner allows it (profile.sign_attestations; the owner's decision
    of 2026-09-17), and then only on a page whose answers all came from the
    profile (page_agent.PageAgent.sign)
  * never guess an answer -- only values the profile or resume actually support
  * never overwrite an answer the user entered
  * never enter Google account passwords
  * never work around a CAPTCHA
  * never claim resume or profile information that isn't supported
  * never submit -- and never click a control that submits an application
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

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
    r"\bsign(?:ing|ed)? electronically\b",
    r"\be-?signature\b",
    r"\btyped signature\b",
    r"\bsignature\b",
    r"\bi (have )?read and consent to the (terms|conditions)\b",
    r"\bi agree to the (terms|statement) (of|and) (agreement|conditions)\b",
    r"\bi willingly accept (the )?(terms|conditions)\b",
    r"\bi (agree|accept|acknowledge).{0,80}\b(terms|conditions)\b",
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
        r"\bcertify|perjury|true and (correct|complete)|signature|by typing my name|sign(?:ing|ed)? electronically\b", text, re.IGNORECASE
    ):
        return False
    return True


_LEGAL_STATUS_RE = re.compile(
    "(?<![A-Za-z0-9])("
    "j-?1|h-?1-?b|l-?1|o-?1|f-?1|opt|ead|"
    "i-?140|i-?485|i-?9|green card|permanent resident|lawful(?:ly)? admitted|"
    "refugee|asylum|asylee|visa status|immigration status|"
    "home residency requirement|export control|"
    "non-?compet[a-z]*|non-?solicit[a-z]*|restrictive covenant|"
    "government (?:employee|official)|public official|politically exposed|"
    "convict[a-z]*|felony|criminal|arrest[a-z]*|"
    "physically located in|lived or were physically"
    ")(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def is_legal_status_question(text: str) -> bool:
    """True for a question about the candidate's legal, immigration or
    contractual status.

    These are answers only the candidate holds -- whether they once held J-1
    status, whether an I-140 was approved, whether a non-compete binds them.
    A drafted guess here is a false statement on a real application and, worse,
    one that reads as authoritative afterwards, so the agent never writes one:
    either the profile states it outright or the question is left for the user.
    """
    return bool(_LEGAL_STATUS_RE.search(" ".join((text or "").split())))


def is_privacy_consent(text: str) -> bool:
    """True for a privacy/data-protection consent the agent may accept."""
    text = " ".join((text or "").split())
    return bool(text and _PRIVACY_ONLY_RE.search(text) and not is_attestation(text))


# Consent to creating the very account the owner asked the agent to create ("I agree to creating
# this account to allow me to apply for positions with X"). It states nothing about the candidate
# and is the act the owner authorized, so the agent may tick it -- owner's approval, 25 September
# 2026. Anything that goes further is not this: terms and conditions, a declaration or signature,
# a marketing opt-in, sharing data with anyone.
_ACCOUNT_CREATION_RE = re.compile(
    r"\b(agree|consent|acknowledge|accept)\b[^.]{0,40}\b(creat(e|ing|ion of)|open(ing)?|set(ting)? up|register(ing)?)\b"
    r"[^.]{0,30}\baccount\b",
    re.IGNORECASE,
)
_BEYOND_ACCOUNT_CREATION_RE = re.compile(
    r"\bterms?\b|\bconditions?\b|\bcertif|\bperjury\b|\btrue\b|\baccurate\b|\bsignature\b|\bsign\b|"
    r"\breceiv|\balerts?\b|\bnewsletters?\b|\bmarketing\b|\bpromotion|\bhear more\b|\bcontact me\b|"
    r"\bshare\b|\bsell\b|\bthird[- ]part(y|ies)\b",
    re.IGNORECASE,
)


def is_account_creation_consent(text: str) -> bool:
    """True for a box that only consents to creating the account the owner asked for."""
    text = " ".join((text or "").split())
    if not text or not _ACCOUNT_CREATION_RE.search(text):
        return False
    return not (is_attestation(text) or _BEYOND_ACCOUNT_CREATION_RE.search(text))


# --------------------------------------------------------------------------
# Controls that submit an application: never clicked by the agent
# --------------------------------------------------------------------------
SUBMIT_LABEL_RE = re.compile(
    r"^\s*(submit( my| your| this)?( application| profile| candidacy)?|apply( now| for this job)?|"
    r"send application|save and submit|finish( and submit)?|complete application|"
    r"confirm( and| &)? send|review (and|&) send|finaliz(e|ation)|"
    r"save (and|&) finish|proceed|postuler|bewerben|enviar( solicitud)?)\s*$",
    re.IGNORECASE,
)
# "Complete" and "Done" alone are deliberately NOT here: both are also common labels for
# committing a single inline card/entry (interaction.commit_draft_cards()'s own "Done" target,
# a "Mark Complete" step) -- a completely different action from a final application submit, and
# too ambiguous a single word to ever be a safe universal synonym for either meaning. A genuine
# final use of either word is instead caught by the structural default-deny rule in
# submission_guard.py (SAFE_UTILITY_LABEL_RE / soleRemainingAction), which does not depend on the
# word at all.

# Words that mean a control manages the FORM (adds/removes/edits/pauses/navigates backward)
# rather than sending it anywhere -- the closed, small vocabulary side of the default-deny rule
# below: an unrecognized label on an otherwise form-submitting-looking control is treated as a
# possible final submit, UNLESS it matches something from this list (the adversarial review's
# Finding 1, 7 October 2026: a label need not be a known SUBMIT_LABEL_RE synonym to be final).
SAFE_UTILITY_LABEL_RE = re.compile(
    r"^\s*(add|remove|delete|clear|upload|choose file|browse|replace( file)?|edit|change|"
    r"show|hide|toggle|view|print|download|close|dismiss|expand|collapse|"
    r"cancel|back|previous|skip|finish later|apply later|continue later|resume later|"
    r"come back later|save( (for later|draft|and (continue|finish) later))?)\b",
    re.IGNORECASE,
)


def is_submit_label(text: str) -> bool:
    """True when a button's label means 'send this application to the employer'."""
    return bool(SUBMIT_LABEL_RE.match(" ".join((text or "").split())))


def is_safe_utility_label(text: str) -> bool:
    """True when a button's label is a known non-final, form-managing action (add/remove/edit/
    cancel/back/save-for-later/...), not a possible final submit."""
    return bool(SAFE_UTILITY_LABEL_RE.match(" ".join((text or "").split())))


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
CAPTCHA_URL_RE = re.compile(
    r"captcha|challenges\.cloudflare\.com|turnstile|arkoselabs|funcaptcha", re.IGNORECASE)


def is_captcha_frame(url: str) -> bool:
    """True for the address of a CAPTCHA's own frame: nothing in it is ever clicked."""
    return bool(CAPTCHA_URL_RE.search(url or ""))


CAPTCHA_TEXT_RE = re.compile(
    r"verify (that )?you are (a )?human|i'm not a robot|complete the (captcha|security check)|"
    r"security check|prove you('| a)re human|click the object that does not fit",
    re.IGNORECASE,
)


def captcha_visible(page) -> bool:
    """True when a CAPTCHA challenge is on screen. The agent then stops and
    hands over -- it never attempts to solve or bypass one."""
    top = getattr(page, "top", None)  # an application inside a frame: check the tab too
    if top is not None and captcha_visible(top):
        return True
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
    """Remembers every value the agent itself put on a page, and decides
    whether a value already on the form may be replaced.

    A value the agent wrote may always be rewritten (correcting its own
    earlier answer). A value the owner entered never is. A value the site
    put there -- a resume parser's guess, the first entry of a list -- may
    be corrected from the profile only when provenance.py observed that no
    person touched it and the owner's pre-fill policy allows it. With no
    observer on the page there is no way to tell the site from the owner,
    and the value is left alone, exactly as before.
    """

    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        # ref -> (value, where the value came from), e.g. ("Springfield", "profile:city")
        self.records: dict[str, tuple] = {}

    @staticmethod
    def _key(page, ref: str) -> str:
        try:
            host = urlparse(page.url).netloc.lower()
        except Exception:
            host = ""
        return f"{host}|{ref}"

    def record(self, page, ref: str, value: str, source: str = "") -> None:
        value = (value or "").strip()
        self._values[self._key(page, ref)] = value
        self.records[ref.strip("[]").replace("id=", "").strip('"')] = (value, source or "agent")

    def is_ours(self, page, ref: str, current_value: str) -> bool:
        stored = self._values.get(self._key(page, ref))
        return stored is not None and stored == (current_value or "").strip()

    def wrote_value(self, page, value: str) -> bool:
        """True when the agent put this exact value on this page, whichever
        control it is read back through.

        Platform widgets are written through one handle and read back through
        another, so an answer the agent gave looked like the user's and was
        remembered as theirs.
        """
        value = (value or "").strip()
        if not value:
            return False
        try:
            host = urlparse(page.url).netloc.lower()
        except Exception:
            host = ""
        return any(stored == value for key, stored in self._values.items()
                   if key.startswith(f"{host}|"))

    def may_write(self, page, ref: str, current_value: str) -> bool:
        """True when the field is empty, or holds a value the agent wrote."""
        return not (current_value or "").strip() or self.is_ours(page, ref, current_value)

    def origin(self, page, ref: str, current_value: str) -> str:
        """Who put the current value there (provenance.EMPTY/AGENT/OWNER/SITE/UNKNOWN)."""
        import provenance

        return provenance.origin(page, ref, value=current_value,
                                 agent_wrote=self.is_ours(page, ref, current_value))

    def may_correct(self, page, ref: str, current_value: str) -> bool:
        """True when a value that contradicts the owner's profile may be put
        right from the profile (see may_overrule)."""
        from config import site_prefill_policy

        return may_overrule(self.origin(page, ref, current_value), site_prefill_policy())


def may_overrule(origin: str, policy: str) -> bool:
    """The one rule for a value already on the form that contradicts the
    owner's profile: may the agent put it right from the profile?

      EMPTY, AGENT -- yes: nothing to overrule, or the agent's own answer;
      OWNER        -- never: what the owner entered stands;
      SITE         -- only when the owner's policy is "correct"
                      (SITE_PREFILL_POLICY; "leave" keeps the site's value);
      UNKNOWN      -- never: with no observer on the page the site cannot be
                      told from the owner.

    `origin` comes from provenance.origin(). The caller decides what
    contradicts the profile; this is the only place that decides who may be
    overruled -- the rules engine, the page agent and the address sweep all
    ask it.
    """
    import provenance

    if origin in (provenance.EMPTY, provenance.AGENT):
        return True
    return origin == provenance.SITE and policy == "correct"


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
# --------------------------------------------------------------------------
# Sponsorship and work authorization: what the page says must be what the
# profile says, whoever put it there
# --------------------------------------------------------------------------
_SPONSORSHIP_Q = re.compile(r"\b(require|need)\w*\b.{0,80}\bsponsor|\bsponsor\w*\b.{0,40}\b(require|need)", re.IGNORECASE)
_AUTHORIZED_Q = re.compile(
    r"\b(authori[sz]ed|eligible|legally (permitted|able|allowed))\b.{0,30}\bto work\b", re.IGNORECASE)
_CONDITIONAL_Q = re.compile(r"^\W*if\b", re.IGNORECASE)
_NO_ANSWER = re.compile(r"^\W*(no|n)\b|\b(do not|don't|does not|will not|won't) (now or in the future )?(need|require)|"
                        r"\bnot (need|require)", re.IGNORECASE)
_YES_ANSWER = re.compile(r"^\W*(yes|y)\b|\b(will|do|would) (need|require)\b", re.IGNORECASE)


def sponsorship_free_work_answer(question: str, profile) -> Optional[tuple[str, str]]:
    """Resolve an authorization question explicitly requiring NO sponsorship.

    Needing sponsorship makes the compound proposition false even when work
    is currently authorized. Inclusive "with or without" wording is unchanged.
    None means this is not such a question; an empty answer means missing data.
    """
    question = " ".join((question or "").split())
    if profile is None or _CONDITIONAL_Q.search(question) or not _AUTHORIZED_Q.search(question) \
            or not re.search(_INCLUSIVE_WITHOUT + r"without\b.{0,80}\bsponsorship\b", question, re.I):
        return None
    if bool(getattr(profile, "requires_visa_sponsorship", False)):
        return "No", "profile.requires_visa_sponsorship"
    eligible = str(getattr(profile, "legally_eligible_to_work", "") or "").strip().lower()
    answer = "Yes" if eligible == "yes" else "No" if eligible == "no" else ""
    return answer, "profile.legally_eligible_to_work"


def legal_answer_conflicts(form_fields: list[dict], profile) -> list[str]:
    """Sponsorship and work-authorization answers on the page that contradict
    the profile, or that are left blank.

    Schwab's (iCIMS) questions page opened with "No" already chosen for "Do
    you now, or will you in the future, require sponsorship (e.g., H-1B
    visa...)?" -- carried over from an earlier Schwab application. The agent
    rightly didn't overwrite an answer it hadn't written, but the application
    was then submitted automatically with it, although the profile says
    sponsorship is required. Whoever filled it in, an answer here that
    contradicts the profile is never sent without the user.
    """
    if profile is None:
        return []
    needs_sponsorship = bool(getattr(profile, "requires_visa_sponsorship", False))
    authorized = str(getattr(profile, "legally_eligible_to_work", "") or "").strip().lower().startswith("y")
    conflicts: list[str] = []
    for field in form_fields or []:
        question = " ".join(str(field.get("label") or "").split())
        value = " ".join(str(field.get("value") or "").split())
        if not question or _CONDITIONAL_Q.search(question) or value.lower() == "checked":
            continue
        if re.search(r"placeholder|make a selection|^select\b|please select|^-+$", value, re.IGNORECASE):
            value = ""
        sponsorship_free = sponsorship_free_work_answer(question, profile)
        if sponsorship_free is not None:
            expected, _source = sponsorship_free
            said_yes, said_no = bool(_YES_ANSWER.search(value)), bool(_NO_ANSWER.search(value))
            matches = (expected == "Yes" and said_yes and not said_no) \
                or (expected == "No" and said_no and not said_yes)
            if not matches:
                conflicts.append(f"{question[:90]} -- authorization without sponsorship does not match your profile")
        elif _SPONSORSHIP_Q.search(question):
            said_yes, said_no = bool(_YES_ANSWER.search(value)), bool(_NO_ANSWER.search(value))
            if not value:
                conflicts.append(f"{question[:90]} -- not answered; your profile says you "
                                 f"{'need' if needs_sponsorship else 'do not need'} sponsorship")
            elif needs_sponsorship and (said_no or not said_yes):
                conflicts.append(f"{question[:90]} -- the form says {value[:30]!r}, but you need sponsorship")
            elif not needs_sponsorship and said_yes and not said_no:
                conflicts.append(f"{question[:90]} -- the form says {value[:30]!r}, but you do not need sponsorship")
        elif _AUTHORIZED_Q.search(question):
            said_no = bool(re.match(r"^\W*(no|n)\b", value, re.IGNORECASE))
            if not value:
                conflicts.append(f"{question[:90]} -- not answered")
            elif authorized and said_no:
                conflicts.append(f"{question[:90]} -- the form says {value[:30]!r}, but you are authorized to work")
    return conflicts


def handover_status(report: dict) -> tuple[str, str]:
    """(status, message) for a filled application, from its validation report.

    ready_to_submit   -- nothing outstanding; the user reviews and clicks Submit
    needs_user_review -- blanks, form errors, a CAPTCHA or an attestation remain
    """
    # The user's rule (2026-09-16): with every mandatory field filled, an
    # application does not need reviewing. So only what genuinely stops it
    # counts -- a required field still empty, an error the form itself is
    # showing, and the two things the agent must never do: sign an attestation
    # and answer a CAPTCHA. Anything else is said in the notes and does not
    # hold the application up.
    problems: list[str] = []
    for label, items in (
        ("required fields still blank", report.get("required_still_blank") or []),
        ("errors shown by the form", report.get("errors_shown") or []),
        ("attestations or signatures for you to complete", report.get("attestations_pending") or []),
        ("sponsorship or work-authorization answers that don't match your profile",
         report.get("legal_answer_conflicts") or []),
    ):
        if items:
            problems.append(f"{label}: " + "; ".join(str(i) for i in items[:5]))
    if report.get("captcha"):
        problems.append("a CAPTCHA is showing -- only you can complete it")

    if problems:
        return "needs_user_review", " | ".join(problems)

    # Worth saying, not worth stopping for.
    notes: list[str] = []
    for label, items in (
        ("left for you, with no answer in your profile", report.get("unanswered_questions") or []),
        ("answered as closely as the options allowed", report.get("ambiguous_choices") or []),
    ):
        if items:
            notes.append(f"{label}: " + "; ".join(str(i) for i in items[:3]))
    if report.get("wizard_stuck"):
        notes.append("the form did not move past this step on its own")

    # An empty page has no blanks and no errors, so "nothing outstanding" used
    # to mean "ready to submit" even where the agent never reached a form at
    # all. On a Google posting that needed a sign-in the agent will not make,
    # it filled nothing, attached nothing, and reported the application ready.
    if not report.get("form_reached", True):
        return ("needs_user_review",
                "The application form was never reached -- nothing has been filled in. "
                "Open it in the browser, sign in if it asks, and start the application again.")
    if not (report.get("fields_filled") or report.get("documents_attached")):
        return ("needs_user_review",
                "Nothing on this page was filled in or attached, so there is nothing to submit yet. "
                "Check that the application form is open and start it again.")

    message = "Every required field is filled and the form shows no errors."
    return "ready_to_submit", " | ".join([message] + notes)


# "with or without sponsorship" is a question or a welcome that includes candidates who need
# sponsorship, so the "without" inside it is not a refusal (owner-approved, 25 September 2026: Praxis's
# "Are you authorized to work in the United States (with or without sponsorship)?" disqualified a job).
_INCLUSIVE_WITHOUT = r"(?<!with or )(?<!with and/or )(?<!with/or )"

_NO_SPONSORSHIP = re.compile(
    r"[^.!?\n]*(?:"
    + _INCLUSIVE_WITHOUT +
    r"without (?:the )?(?:need (?:for|of) )?(?:current or future )?(?:employment[- ]based )?"
    r"(?:visa |immigration )?sponsorship"
    r"|(?:not|unable to|cannot|can't|will not|won't|does not|do not|are not able to|is not able to)"
    r" (?:currently )?(?:able to )?(?:offer|provide|support|sponsor|transfer|assume|take over)\w*[^.!?\n]{0,60}(?:sponsorship|visas?\b)"
    r"|unable to (?:sponsor|take over sponsorship|transfer|provide)[^.!?\n]{0,60}(?:sponsorship|visa)"
    r"|take over (?:sponsorship|visa)"
    r"|no (?:visa |employment[- ]based |immigration )?sponsorship"
    r"|sponsorship (?:is |will )?(?:not|n't) (?:be )?(?:available|offered|provided|considered)"
    r"|(?:u\.?s\.? citizens?|green card holders?|permanent residents?) only"
    r"|must be a u\.?s\.? citizen"
    r")[^.!?\n]*",
    re.IGNORECASE,
)


def no_sponsorship_statement(text: str) -> str:
    """The sentence in which a posting or form says it will not sponsor a visa,
    or is open only to citizens -- empty when it says nothing of the kind.

    Casey's said it only inside the application ("This position requires
    authorization to work in the U.S. without the need for employment-based
    visa sponsorship"), and the agent worked through nine pages of a job the
    user, on an H-1B, cannot take.
    """
    match = _NO_SPONSORSHIP.search(" ".join((text or "").split()))
    return match.group(0).strip()[:240] if match else ""


STATUS_DISQUALIFIED_POLICY_MISMATCH = "DISQUALIFIED_POLICY_MISMATCH"
STATUS_BLOCKED_VALIDATION_LOOP = "BLOCKED_VALIDATION_LOOP"


def check_visa_sponsorship_shield(
    page,
    profile,
    job_dir: Optional[Path] = None,
) -> tuple[bool, str, Optional[str]]:
    """Safety compliance guardrail: scans the active form for hard employer
    disqualification clauses (e.g. 'unable to sponsor or take over sponsorship of
    an employment visa at this time').

    If candidate profile specifies requires_visa_sponsorship = True and an explicit
    non-sponsorship declaration is found, executes a safety abort:
    captures a full-page forensic screenshot and returns (True, statement, screenshot_path).
    """
    if not getattr(profile, "requires_visa_sponsorship", False):
        return False, "", None

    try:
        body_text = page.locator("body").inner_text(timeout=3_000) or ""
    except Exception:
        try:
            body_text = page.content() or ""
        except Exception:
            body_text = ""

    clause = no_sponsorship_statement(body_text)
    if not clause:
        return False, "", None

    screenshot_path = None
    if job_dir is not None:
        try:
            import time
            out_dir = Path(job_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            shot = out_dir / f"forensic_disqualified_policy_mismatch_{int(time.time())}.png"
            import diagnostics
            if diagnostics.capture_safe_screenshot(page, shot):
                screenshot_path = str(shot)
        except Exception as exc:
            logging.getLogger(__name__).debug("Could not take forensic screenshot for visa policy mismatch: %s", exc)

    return True, clause, screenshot_path



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


# --------------------------------------------------------------------------
# Verified auto-submit
# --------------------------------------------------------------------------
# Off unless the user sets AUTO_SUBMIT_VERIFIED_ONLY. Even then, an application
# is submitted only when EVERY check below passes. Anything uncertain is a
# refusal rather than a judgement call: a wrong submission is a real
# application, in the user's name, that cannot be withdrawn.

@dataclass
class FieldComparison:
    """One field on the form, and whether it matches approved data."""

    label: str
    on_form: str
    approved: str
    source: str          # profile / resume / approved answer / site
    matches: bool
    required: bool = True

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class AutoSubmitDecision:
    """Why an application was, or was not, eligible for verified auto-submit."""

    eligible: bool
    reasons: list = field(default_factory=list)
    field_comparisons: list = field(default_factory=list)
    evidence_paths: dict = field(default_factory=dict)
    decided_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict:
        return {
            "eligible": self.eligible,
            "reasons": list(self.reasons),
            "field_comparisons": [c.as_dict() for c in self.field_comparisons],
            "evidence_paths": dict(self.evidence_paths),
            "decided_at": self.decided_at,
        }

    def summary(self) -> str:
        if self.eligible:
            return f"eligible: {len(self.field_comparisons)} field(s) matched approved data"
        return "not eligible: " + "; ".join(self.reasons[:6])


def normalise_answer(value: str) -> str:
    """Comparison form of an answer: case, spacing and trivial punctuation are
    not differences; anything else is."""
    text = " ".join((value or "").split()).strip().lower()
    text = text.replace(" ", " ").strip(" .:*")
    digits = re.sub(r"\D", "", text)
    if digits and len(digits) >= 7 and re.fullmatch(r"[\d\s()+\-.]+", text):
        return digits           # phone numbers: the digits are the value
    return text


def values_match(on_form: str, approved: str) -> bool:
    """True only when the value on the form IS the approved value.

    Deliberately strict: a form value that merely contains or resembles the
    approved one is not a match, because that is how a wrong answer (an old
    address, someone else's phone) slips through. The one allowance is a
    trailing qualifier the site adds itself, as in
    "Asian" -> "Asian (Not Hispanic or Latino)".
    """
    a, b = normalise_answer(on_form), normalise_answer(approved)
    if not a or not b:
        return False
    if a == b:
        return True
    without_qualifier = re.sub(r"\s*\([^)]*\)\s*$", "", a).strip()
    return bool(without_qualifier) and without_qualifier == b


def compare_fields(form_fields, approved: dict) -> list:
    """Field-by-field comparison of the form against approved data.

    `form_fields` are {label, value, required, source} read back from the page;
    `approved` maps a label to the value the profile, resume or an explicitly
    approved answer supports.
    """
    comparisons = []
    for item in form_fields:
        label = " ".join(str(item.get("label", "")).split())
        on_form = str(item.get("value", ""))
        expected = approved.get(label) or approved.get(label.lower()) or ""
        comparisons.append(
            FieldComparison(
                label=label[:160],
                on_form=on_form[:200],
                approved=str(expected)[:200],
                source=str(item.get("source", "site")),
                matches=values_match(on_form, expected) if expected else False,
                required=bool(item.get("required", True)),
            )
        )
    return comparisons


def canonical_url(url: str) -> str:
    """A posting URL without tracking parameters, session ids or fragments."""
    parsed = urlparse((url or "").strip().lower())
    keep = [
        (k, v) for k, v in parse_qsl(parsed.query)
        if not k.startswith(("utm_", "gh_src", "_s."))
        and k not in {"src", "source", "ref", "referrer", "trackingid", "sid"}
    ]
    return urlunparse(parsed._replace(query=urlencode(keep), path=parsed.path.rstrip("/"), fragment=""))


# Report keys that always block a submission, with how to say so.
BLOCKING_REPORT_KEYS = (
    ("required_still_blank", "required field(s) still blank"),
    ("errors_shown", "the form is showing error(s)"),
    ("warnings_shown", "the form is showing warning(s)"),
    ("unanswered_questions", "question(s) the agent could not answer from approved data"),
    ("ambiguous_choices", "dropdown choice(s) that are not an exact match"),
    ("unsupported_questions", "custom question(s) with no approved answer"),
    ("attestations_pending", "attestation/e-signature/consent left for you"),
    ("identity_checks", "an identity or verification step"),
)


def evaluate_auto_submit(
    *,
    enabled: bool,
    job: dict,
    tracked: dict,
    report: dict,
    form_fields,
    approved: dict,
    uploaded_documents,
    expected_documents,
    documents_verified: bool = False,
    evidence_paths: Optional[dict] = None,
) -> AutoSubmitDecision:
    """The single place that decides whether an application may be submitted.

    Every reason for refusal is recorded, so the audit trail explains the
    decision rather than merely stating it.
    """
    reasons: list = []
    comparisons = compare_fields(form_fields, approved)

    if not enabled:
        reasons.append("verified auto-submit is off (AUTO_SUBMIT_VERIFIED_ONLY)")

    # 1. the application in front of us is the one we tracked
    for label, left, right in (
        ("job title", job.get("title", ""), tracked.get("title", "")),
        ("company", job.get("company", ""), tracked.get("company", "")),
    ):
        if normalise_answer(left) != normalise_answer(right):
            reasons.append(f"{label} on the page ({left!r}) is not the tracked {label} ({right!r})")
    if canonical_url(job.get("url", "")) != canonical_url(tracked.get("url", "")):
        reasons.append("the application URL is not the tracked posting's URL")

    # 2. the attached documents are the ones generated for this application
    uploaded = {Path(d).name for d in uploaded_documents if d}
    expected = {Path(d).name for d in expected_documents if d}
    if expected - uploaded:
        reasons.append("expected document(s) not attached: " + ", ".join(sorted(expected - uploaded)))
    if uploaded - expected:
        reasons.append("document(s) on the form were not generated for this application: "
                       + ", ".join(sorted(uploaded - expected)))
    if expected and not documents_verified:
        # File names alone prove nothing; the caller confirms the local files
        # are byte-for-byte the documents stored for this application.
        reasons.append("the attached document(s) could not be verified against the stored versions")

    # 3. the form itself is clean
    for key, text in BLOCKING_REPORT_KEYS:
        items = report.get(key) or []
        if items:
            reasons.append(f"{text}: " + "; ".join(str(i) for i in items[:4]))
    if report.get("captcha"):
        reasons.append("a CAPTCHA is on the page")

    # 4. every required field matches approved data exactly
    for comparison in comparisons:
        if not comparison.required:
            continue
        if not comparison.approved:
            reasons.append(f"no approved value for required field {comparison.label!r}")
        elif not comparison.matches:
            reasons.append(
                f"{comparison.label!r} holds {comparison.on_form!r}; approved value is {comparison.approved!r}"
            )

    return AutoSubmitDecision(
        eligible=not reasons,
        reasons=reasons,
        field_comparisons=comparisons,
        evidence_paths=dict(evidence_paths or {}),
    )


# --------------------------------------------------------------------------
# Logs must never carry secrets or personal data
# --------------------------------------------------------------------------
_SECRET_RE = re.compile(
    r"(sk-ant-[A-Za-z0-9_\-]+)"
    r"|((?i:password|api[_\-]?key|token|secret)\s*[=:]\s*\S+)"
    r"|([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})"
    r"|(\+?\d[\d\s()\-.]{8,}\d)"
)


def redact(text: str) -> str:
    """Masks API keys, credentials, emails and phone numbers.

    Applied to everything the agent logs: a log file gets read, pasted and
    shared far more casually than the database it came from.
    """
    def mask(match: "re.Match") -> str:
        value = match.group(0)
        if value.lower().startswith("sk-ant-"):
            return "sk-ant-***"
        if re.match(r"(?i)\s*(password|api[_\-]?key|token|secret)\s*[=:]", value):
            key = re.split(r"[=:]", value, maxsplit=1)[0]
            return f"{key}=***"
        if "@" in value:
            name, _, domain = value.partition("@")
            return f"{name[:2]}***@{domain}"
        digits = re.sub(r"\D", "", value)
        return f"***-{digits[-4:]}" if len(digits) >= 4 else "***"

    return _SECRET_RE.sub(mask, text or "")


class RedactingFilter(logging.Filter):
    """Logging filter that runs every message through redact().

    The finished message is redacted, never the format string on its own.
    Redacting the template "Could not read ATS_PASSWORD: %s" turned it into
    "ATS_PASSWORD=***": the %s placeholder was gone but its argument was
    still attached, so formatting the record failed later. Normal runs
    printed a logging error instead of the line, and tests failed or passed
    depending on which test file happened to import apply_flow first.
    """

    def filter(self, record: "logging.LogRecord") -> bool:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        record.msg = redact(message)
        record.args = ()
        return True


def install_log_redaction(logger_name: str = "") -> None:
    """Adds the redacting filter to a logger and each of its handlers, once."""
    target = logging.getLogger(logger_name)
    if not any(isinstance(f, RedactingFilter) for f in target.filters):
        target.addFilter(RedactingFilter())
    for handler in target.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())


def profile_values(profile) -> dict:
    """Every plain value the user's profile asserts, keyed by field name.

    Verified auto-submit compares what is on the form against these, so this is
    the definition of "approved data" together with the explicitly approved
    answers file.
    """
    values = {}
    for name in dir(profile):
        if name.startswith("_"):
            continue
        try:
            value = getattr(profile, name)
        except Exception:
            continue
        if isinstance(value, str) and value.strip():
            values[name] = value.strip()
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            values[name] = str(value)
    if values.get("full_name"):
        parts = values["full_name"].split()
        values.setdefault("first_name", parts[0])
        values.setdefault("last_name", parts[-1])
    return values


def approved_values(fields, profile, approved_answers: dict, agent_records: dict) -> dict:
    """label -> the approved value that a field is allowed to hold.

    Three sources, in order:
      1. what the agent wrote and where it came from (profile/resume/approved),
      2. an explicitly approved answer for that label,
      3. a profile value that the form's existing content matches exactly
         (a site that pre-filled your email from your account, say).
    A field that matches none of these has no approved value, and verified
    auto-submit refuses rather than accepting it.
    """
    known = profile_values(profile)
    by_value = {normalise_answer(v): v for v in known.values()}
    approved_by_label = {" ".join(k.split()): v for k, v in (approved_answers or {}).items()}

    resolved = {}
    for item in fields:
        label = " ".join(str(item.get("label", "")).split())
        ref = str(item.get("ref", ""))
        on_form = str(item.get("value", ""))
        # A checked declaration can be supported by the owner's explicit
        # signing preference even when its control was rebuilt between steps.
        # This grants no authority to an unchecked or unrelated checkbox.
        if item.get("type") == "checkbox" and on_form == "checked":
            if (is_attestation(label) and getattr(profile, "sign_attestations", False)) or (
                    is_privacy_consent(label) and getattr(profile, "accept_application_privacy_prompts", False)):
                resolved[label] = "checked"
                continue
        recorded = (agent_records or {}).get(ref)
        if recorded:
            recorded_value, source = recorded[0], (recorded[1] if len(recorded) > 1 else "agent")
            key = source.split(":", 1)[1] if ":" in source else ""
            resolved[label] = known.get(key, recorded_value)
            continue
        if label in approved_by_label:
            resolved[label] = approved_by_label[label]
            continue
        match = by_value.get(normalise_answer(on_form))
        if match:
            resolved[label] = match
    return resolved
