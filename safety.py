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
        # ref -> (value, where the value came from), e.g. ("Fairfax", "profile:city")
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
            key = re.split(r"[=:]", value, 1)[0]
            return f"{key}=***"
        if "@" in value:
            name, _, domain = value.partition("@")
            return f"{name[:2]}***@{domain}"
        digits = re.sub(r"\D", "", value)
        return f"***-{digits[-4:]}" if len(digits) >= 4 else "***"

    return _SECRET_RE.sub(mask, text or "")


class RedactingFilter(logging.Filter):
    """Logging filter that runs every message through redact()."""

    def filter(self, record: "logging.LogRecord") -> bool:
        try:
            record.msg = redact(str(record.msg))
            # Only text is redacted: turning numbers into strings would break
            # %d formatting in the message.
            if isinstance(record.args, tuple):
                record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: (redact(v) if isinstance(v, str) else v) for k, v in record.args.items()}
        except Exception:
            pass
        return True


def install_log_redaction(logger_name: str = "") -> None:
    """Adds the redacting filter to a logger and each of its handlers."""
    target = logging.getLogger(logger_name)
    flt = RedactingFilter()
    target.addFilter(flt)
    for handler in target.handlers:
        handler.addFilter(flt)


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
