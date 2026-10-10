"""One precise, structured human-handoff representation (Phase 0-B4).

Built entirely from signals that already exist -- `Outcome.kind`, the reason text
`account_state`/`page_agent` already produce, the job's employer and the page's own host --
never a new detection mechanism, and never a second policy authority. It composes a
context-rich message (task section 23: which employer, which portal, why automation stopped,
what the owner must do, how automation knows it can continue) from text that today reaches
the owner with none of that context woven in (confirmed in
docs/security/phase0-b4-discovery.md section 11-12). P0-B1's submission authority, P0-B2's
account/auth authority, and P0-B3's checkpoint/recovery authority are unchanged and are not
read or re-derived here beyond the plain text they already produced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

CAPTCHA = "CAPTCHA"
SMS_MFA = "SMS_MFA"
AUTHENTICATOR_MFA = "AUTHENTICATOR_MFA"
SECURITY_KEY = "SECURITY_KEY"
PUSH_APPROVAL = "PUSH_APPROVAL"
ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
ACCOUNT_CREATION_UNCERTAIN = "ACCOUNT_CREATION_UNCERTAIN"
FIELD_REQUIRED = "FIELD_REQUIRED"
UNSUPPORTED_CONTROL = "UNSUPPORTED_CONTROL"
VALIDATION_BLOCKER = "VALIDATION_BLOCKER"
ACTION_OUTCOME_UNKNOWN = "ACTION_OUTCOME_UNKNOWN"
APPLICATION_RECOVERY = "APPLICATION_RECOVERY"
OWNER_REVIEW = "OWNER_REVIEW"
OTHER = "OTHER"

_CATEGORIES = frozenset({
    CAPTCHA, SMS_MFA, AUTHENTICATOR_MFA, SECURITY_KEY, PUSH_APPROVAL, ACCOUNT_LOCKED,
    ACCOUNT_CREATION_UNCERTAIN, FIELD_REQUIRED, UNSUPPORTED_CONTROL, VALIDATION_BLOCKER,
    ACTION_OUTCOME_UNKNOWN, APPLICATION_RECOVERY, OWNER_REVIEW, OTHER,
})

# Same wording emailed_codes.NON_EMAIL_CHANNEL_CORE already uses to tell an authenticator app,
# a security key, or a push approval apart from an emailed/SMS code (P0-B2) -- reapplied here
# only to choose a DISPLAY category from text already produced, never to decide whether a
# code may be read (that decision stays emailed_codes.why_not()'s alone).
_AUTHENTICATOR_RE = re.compile(r"\bauthenticator(?:\s+app)?\b|\bgoogle authenticator\b|\bauthy\b|\btotp\b",
                               re.IGNORECASE)
_SECURITY_KEY_RE = re.compile(r"\bsecurity key\b|\bhardware key\b", re.IGNORECASE)
_PUSH_RE = re.compile(r"\bpush notification\b|approve (?:the )?(?:sign.?in|request) (?:on|in|from) your",
                      re.IGNORECASE)
_SECOND_FACTOR_RE = re.compile(r"second factor|authorized way to (?:complete|read)", re.IGNORECASE)
_LOCKED_RE = re.compile(r"unlock or reset it on the site|\baccount is locked\b|\bdisabled\b",
                        re.IGNORECASE)
_ACCOUNT_CREATION_UNCERTAIN_RE = re.compile(
    r"pressed create account.*result was confirmed|could not be safely recorded", re.IGNORECASE)
_FIELD_REQUIRED_RE = re.compile(r"required field is still blank|needs your answer|could not set:",
                                re.IGNORECASE)
_VALIDATION_RE = re.compile(r"the form will not go on|validation", re.IGNORECASE)
_STUCK_RE = re.compile(r"did not move on|keeps asking the same things", re.IGNORECASE)
_RECOVERY_RE = re.compile(r"\bDIFFERENT_APPLICATION\b|\bAPPLICATION_NOT_FOUND\b|\bUNSUPPORTED\b",
                          re.IGNORECASE)

_RESUME_CONDITIONS = {
    CAPTCHA: "the CAPTCHA is no longer visible on the page",
    SMS_MFA: "the account step no longer asks for an SMS/text code",
    AUTHENTICATOR_MFA: "the account step no longer asks for an authenticator-app code",
    SECURITY_KEY: "the account step no longer asks for the security key",
    PUSH_APPROVAL: "the account step no longer asks for the push approval",
    ACCOUNT_LOCKED: "the account is unlocked",
    ACCOUNT_CREATION_UNCERTAIN: "the next read confirms whether the account now exists",
    FIELD_REQUIRED: "the required field is filled in",
    UNSUPPORTED_CONTROL: "the control has been completed on the page",
    VALIDATION_BLOCKER: "the page's validation error clears or the stage advances",
    ACTION_OUTCOME_UNKNOWN: "live reconciliation resolves what actually happened",
    APPLICATION_RECOVERY: "live reconciliation finds the application again",
    OWNER_REVIEW: "the owner presses Continue",
    OTHER: "the owner presses Continue",
}


@dataclass(frozen=True)
class Handoff:
    application_key: str = ""
    employer: str = ""
    portal: str = ""
    stage: str = ""
    category: str = OTHER
    reason: str = ""
    required_action: str = ""
    work_completed: str = ""
    resume_condition: str = ""

    def __post_init__(self) -> None:
        if self.category not in _CATEGORIES:
            raise ValueError(f"Unknown handoff category: {self.category!r}")

    def compose_message(self) -> str:
        """The owner-facing text: names the employer/portal when known, up front, before the
        reason -- answering task section 23's "which employer/portal" without the owner
        having to infer it from context elsewhere on the dashboard."""
        where = " / ".join(p for p in (self.employer, self.portal) if p)
        lead = f"{where}: " if where else ""
        body = self.reason or self.required_action or "the owner is needed"
        if self.required_action and self.required_action not in body:
            body = f"{body} -- {self.required_action}"
        if self.work_completed:
            body = f"{body} (already done: {self.work_completed})"
        return lead + body

    def to_checkpoint_fields(self) -> dict:
        """Fields to pass into `checkpoint.merge_update()` -- reuses P0-B3's existing
        `employer`/`handoff_reason` fields rather than duplicating them, and adds the three
        new, narrowly-scoped fields this phase introduces (task section 26: integrate with
        the existing checkpoint, do not build a separate persistence store)."""
        return {
            "employer": self.employer,
            "handoff_reason": self.compose_message(),
            "handoff_category": self.category,
            "handoff_required_action": self.required_action,
            "handoff_resume_condition": self.resume_condition,
        }


def classify(*, outcome_kind: str = "", reason_text: str = "") -> str:
    """Best-effort category from text that already exists -- never a new detection
    mechanism, never a reason to read the live page again. Falls back to OTHER rather than
    guessing; a wrong category only affects a label shown to the owner, never any behavior."""
    text = reason_text or ""
    if outcome_kind == "captcha":
        return CAPTCHA
    if _RECOVERY_RE.search(text):
        return APPLICATION_RECOVERY
    if _LOCKED_RE.search(text):
        return ACCOUNT_LOCKED
    if _ACCOUNT_CREATION_UNCERTAIN_RE.search(text):
        return ACCOUNT_CREATION_UNCERTAIN
    if _SECOND_FACTOR_RE.search(text):
        if _AUTHENTICATOR_RE.search(text):
            return AUTHENTICATOR_MFA
        if _SECURITY_KEY_RE.search(text):
            return SECURITY_KEY
        if _PUSH_RE.search(text):
            return PUSH_APPROVAL
        return SMS_MFA
    if _FIELD_REQUIRED_RE.search(text):
        return FIELD_REQUIRED
    if _VALIDATION_RE.search(text):
        return VALIDATION_BLOCKER
    if _STUCK_RE.search(text):
        return ACTION_OUTCOME_UNKNOWN
    if outcome_kind == "owner_needed":
        return OWNER_REVIEW
    return OTHER


def resume_condition_for(category: str) -> str:
    return _RESUME_CONDITIONS.get(category, _RESUME_CONDITIONS[OTHER])


def build(
    *, application_key: str = "", employer: str = "", portal: str = "", stage: str = "",
    outcome_kind: str = "", reason_text: str = "", required_action: str = "",
    work_completed: str = "", category: Optional[str] = None,
) -> Handoff:
    """Builds a Handoff, classifying a category from `outcome_kind`/`reason_text` unless the
    caller already knows a more precise one (e.g. P0-B3's recovery.reconcile() outcome)."""
    resolved_category = category if category in _CATEGORIES else classify(
        outcome_kind=outcome_kind, reason_text=reason_text)
    h = Handoff(
        application_key=application_key, employer=employer, portal=portal, stage=stage,
        category=resolved_category, reason=reason_text, required_action=required_action,
        work_completed=work_completed, resume_condition=resume_condition_for(resolved_category),
    )
    set_active_handoff(h)
    return h


# ----------------------------------------------------------------------
# Active handoff management & telemetry integration
# ----------------------------------------------------------------------
import threading
import time

_ACTIVE_HANDOFF_LOCK = threading.RLock()
_ACTIVE_HANDOFF: Optional[dict] = None


def set_active_handoff(h: Handoff, handoff_id: Optional[str] = None) -> dict:
    """Registers an active handoff and emits a structured telemetry event."""
    global _ACTIVE_HANDOFF
    import secrets
    from runtime_events import RuntimeEvent, EventName, ReasonCode

    hid = handoff_id or secrets.token_urlsafe(16)
    data = {
        "handoff_id": hid,
        "application_key": h.application_key,
        "employer": h.employer,
        "portal": h.portal,
        "stage": h.stage,
        "category": h.category,
        "reason": h.reason,
        "required_action": h.required_action,
        "work_completed": h.work_completed,
        "resume_condition": h.resume_condition,
        "created_at": time.time(),
        "status": "PENDING",
    }
    with _ACTIVE_HANDOFF_LOCK:
        _ACTIVE_HANDOFF = data

    # Map category to telemetry reason code
    rc = ReasonCode.AUTH_REQUIRED
    if h.category == CAPTCHA:
        rc = ReasonCode.CAPTCHA_REQUIRED
        ev_name = EventName.CAPTCHA_DETECTED
    elif h.category in (SMS_MFA, AUTHENTICATOR_MFA, SECURITY_KEY, PUSH_APPROVAL):
        rc = ReasonCode.MFA_REQUIRED
        ev_name = EventName.MFA_DETECTED
    else:
        ev_name = EventName.HANDOFF_CREATED

    RuntimeEvent.emit(
        event_name=ev_name,
        component="handoff",
        stage=h.stage or "handoff",
        display_message=h.compose_message(),
        reason_code=rc,
        application_key=h.application_key,
        safe_metadata={
            "handoff_id": hid,
            "category": h.category,
            "employer": h.employer,
            "portal": h.portal,
            "resume_condition": h.resume_condition,
            "required_action": h.required_action,
            "work_completed": h.work_completed,
        },
    )
    return data


def get_active_handoff(application_key: Optional[str] = None) -> Optional[dict]:
    """Returns the current active handoff session, optionally matching application_key."""
    with _ACTIVE_HANDOFF_LOCK:
        if _ACTIVE_HANDOFF is None:
            return None
        if application_key and _ACTIVE_HANDOFF.get("application_key") != application_key:
            return None
        return dict(_ACTIVE_HANDOFF)


def clear_active_handoff(handoff_id: Optional[str] = None) -> None:
    """Clears the active handoff session."""
    global _ACTIVE_HANDOFF
    with _ACTIVE_HANDOFF_LOCK:
        if handoff_id and _ACTIVE_HANDOFF and _ACTIVE_HANDOFF.get("handoff_id") != handoff_id:
            return
        _ACTIVE_HANDOFF = None


def resolve_active_handoff(
    application_key: Optional[str] = None,
    live_verification_fn: Optional[callable] = None,
) -> tuple[bool, str]:
    """Attempts to resolve the active handoff.

    Requires positive verification evidence when live_verification_fn is provided
    (B3 / B4 invariant: clicking does NOT equal verification).
    """
    global _ACTIVE_HANDOFF
    from runtime_events import RuntimeEvent, EventName

    with _ACTIVE_HANDOFF_LOCK:
        if _ACTIVE_HANDOFF is None:
            return True, "No active handoff to resolve"
        if application_key and _ACTIVE_HANDOFF.get("application_key") != application_key:
            return False, "Active handoff does not match application"

        if live_verification_fn is not None:
            ok, msg = live_verification_fn()
            if not ok:
                return False, f"Condition not verified on live page: {msg}"

        hid = _ACTIVE_HANDOFF.get("handoff_id")
        app_key = _ACTIVE_HANDOFF.get("application_key")
        _ACTIVE_HANDOFF = None

    RuntimeEvent.emit(
        event_name=EventName.HANDOFF_RESOLVED,
        component="handoff",
        stage="resolution",
        display_message="Human handoff resolved and verified",
        is_verified=True,
        application_key=app_key,
        safe_metadata={"handoff_id": hid},
    )
    return True, "Handoff resolved successfully"

