"""The shared outcome representation for an important browser action (Phase 0-B4).

An action is not successful merely because the automation attempted it. This module gives
the handful of call sites where that distinction materially affects correctness or recovery
(not every keystroke) one small, consistent way to say: what was intended, what was actually
attempted, and whether the browser/site afterward positively verifies it.

This reports what happened. It is not a second policy authority: it does not decide whether
to submit (P0-B1's `SubmissionGuardV0`/`begin_submission_dispatch` alone do), whether an
account/sign-in state should be acted on (P0-B2's `account_state.read_state()` alone does), or
whether a resumed run may trust a checkpoint (P0-B3's `recovery.reconcile()` alone does). It
has no authority over any of those; it only classifies the result of an ordinary field write,
upload, navigation press, or repeated-entry mutation, for observability and for feeding P0-B3's
`last_verified_action`/`uncertain_actions` checkpoint fields proportionally -- not after every
character typed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Outcomes. Default toward not claiming success: an ambiguous result is NO_CHANGE or
# OUTCOME_UNKNOWN, never VERIFIED (task section 36).
NOT_ATTEMPTED = "NOT_ATTEMPTED"
VERIFIED = "VERIFIED"
REFUSED = "REFUSED"
VALIDATION_FAILED = "VALIDATION_FAILED"
NO_CHANGE = "NO_CHANGE"
OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
OWNER_REQUIRED = "OWNER_REQUIRED"

_OUTCOMES = frozenset({
    NOT_ATTEMPTED, VERIFIED, REFUSED, VALIDATION_FAILED, NO_CHANGE, OUTCOME_UNKNOWN,
    OWNER_REQUIRED,
})

# Evidence kinds: what concretely backs the outcome, for the "evidence_summary" to point at
# rather than restate. Open vocabulary (a string, not an enum) -- callers name the evidence
# that actually exists for their control type (task section 4): "input_value", "is_checked",
# "selected_option", "committed_tag", "attachment_filename", "step_indicator", "entry_count",
# "validation_text", "page_text_diff", or "" when there is none.


@dataclass(frozen=True)
class ActionResult:
    action: str                       # e.g. "fill", "choose", "upload_resume", "press_next"
    target: str = ""                  # the question/control name, or a short label -- never a value
    attempted: bool = False
    outcome: str = NOT_ATTEMPTED
    evidence_kind: str = ""
    evidence_summary: str = ""        # short, secret-safe: "value committed" not the value itself
    reason: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in _OUTCOMES:
            raise ValueError(f"Unknown ActionResult outcome: {self.outcome!r}")

    @property
    def verified(self) -> bool:
        return self.outcome == VERIFIED


def not_attempted(action: str, target: str = "", reason: str = "") -> ActionResult:
    return ActionResult(action=action, target=target, attempted=False, outcome=NOT_ATTEMPTED,
                        reason=reason)


def verified(action: str, target: str = "", evidence_kind: str = "",
             evidence_summary: str = "") -> ActionResult:
    return ActionResult(action=action, target=target, attempted=True, outcome=VERIFIED,
                        evidence_kind=evidence_kind, evidence_summary=evidence_summary)


def no_change(action: str, target: str = "", evidence_kind: str = "",
              reason: str = "") -> ActionResult:
    return ActionResult(action=action, target=target, attempted=True, outcome=NO_CHANGE,
                        evidence_kind=evidence_kind, reason=reason)


def validation_failed(action: str, target: str = "", reason: str = "") -> ActionResult:
    return ActionResult(action=action, target=target, attempted=True, outcome=VALIDATION_FAILED,
                        evidence_kind="validation_text", reason=reason)


def outcome_unknown(action: str, target: str = "", reason: str = "") -> ActionResult:
    return ActionResult(action=action, target=target, attempted=True, outcome=OUTCOME_UNKNOWN,
                        reason=reason)


def owner_required(action: str, target: str = "", reason: str = "") -> ActionResult:
    return ActionResult(action=action, target=target, attempted=True, outcome=OWNER_REQUIRED,
                        reason=reason)
