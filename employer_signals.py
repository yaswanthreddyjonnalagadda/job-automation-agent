"""What the employer's emails say, in the tracker's own words (f321).

Every submit already peeks at the owner's Gmail for the confirmation email;
the other emails in the same search (rejections, interviews, offers,
assessments, verifications) were logged and forgotten. Row subjects and
snippets are now classified with deterministic patterns and the first signal
per check is recorded on the application as an event, so the tracker shows
what the employer said without new statuses or a mailbox crawler.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

CONFIRMATION_MAIL = re.compile(
    r"thank(s| you) for (applying|your application|your interest)|application (received|submitted|confirmation)"
    r"|(we('ve| have)|has been) received|successfully (applied|submitted)|your application (for|to|has|was)",
    re.IGNORECASE,
)

_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("rejection", re.compile(
        r"not (be )?moving forward|unfortunately|other candidates|decided to (move forward with other|pursue other)"
        r"|no longer (being )?consider|position has been filled|we regret|could not offer you",
        re.IGNORECASE)),
    ("offer", re.compile(r"offer letter|pleased to offer|extend an offer|your offer|offer of employment",
                         re.IGNORECASE)),
    ("interview", re.compile(r"\binterview\b|schedul(e|ing) (a |some )?(call|conversation|chat|meeting|time)"
                             r"|speak with (our|the) team|next steps?.{0,40}(call|conversation|meeting)",
                             re.IGNORECASE)),
    ("assessment", re.compile(r"\bassessment\b|take-home|take home|hackerrank|codesignal|codility"
                              r"|technical (exercise|challenge|screen)|online test",
                              re.IGNORECASE)),
    ("verification", re.compile(r"\bverif(y|ication code)|confirm your email|one-time (pass)?code|\bOTP\b"
                                r"|verify your (account|identity|email)",
                                re.IGNORECASE)),
    ("recruiter", re.compile(r"\breached out\b|(came across|found) your profile|opportunity (that|which) (may|might|could)"
                             r"|talent (partner|acquisition)|would love to (chat|speak|connect)",
                             re.IGNORECASE)),
    ("reminder", re.compile(r"\breminder\b|complete your application|finish your application|action required"
                            r"|your application is (incomplete|waiting)",
                            re.IGNORECASE)),
)


def classify(text: str) -> Optional[str]:
    """The employer signal a subject/snippet carries, or None. Rejections are checked before thanks: a rejection
    thanks the applicant too."""
    for kind, pattern in _PATTERNS:
        if pattern.search(text or ""):
            return kind
    if CONFIRMATION_MAIL.search(text or ""):
        return "confirmation"
    return None


LABELS = {
    "rejection": "Rejection", "offer": "Offer", "interview": "Interview",
    "assessment": "Assessment", "verification": "Verification",
    "recruiter": "Recruiter note", "reminder": "Reminder", "confirmation": "Confirmation",
}


def label(kind: str) -> str:
    """The signal word in plain language for display."""
    return LABELS.get(kind or "", "Employer email")


def _names_job(text: str, company: str, job_title: str) -> bool:
    low = (text or "").lower()
    if job_title:
        return job_title.lower() in low
    return bool(company) and company.lower() in low


def scan_rows(texts, company: str, job_title: str = "") -> dict[str, Any]:
    """The confirmation row (as before) plus the first other employer signal in one Gmail search's rows."""
    confirmation, signal = None, None
    for text in texts or []:
        if not _names_job(text, company, job_title):
            continue
        if confirmation is None and CONFIRMATION_MAIL.search(text or ""):
            confirmation = text[:300]
        if signal is None:
            kind = classify(text)
            if kind and kind != "confirmation":
                signal = (kind, text[:300])
    return {"confirmation": confirmation, "signal": signal}


def record_employer_signal(tracker: Any, key: str, assistant: Any) -> bool:
    """Records the signal the last mail check stashed on the assistant, if any; never raises."""
    try:
        signal = getattr(assistant, "_last_employer_signal", None)
        if not signal or not hasattr(tracker, "record_event"):
            return False
        kind = signal[0]
        tracker.record_event(key, "employer_email", payload={"signal": kind})
        return True
    except Exception as exc:
        logger.debug("Could not record an employer signal: %s", exc)
        return False
