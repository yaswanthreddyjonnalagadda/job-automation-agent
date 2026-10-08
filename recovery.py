"""Resume reconciliation (Phase 0-B3): decides whether a durable checkpoint may be trusted to
continue from, by comparing it against live, deterministic evidence -- never the reverse.

Current browser/account/submission evidence always outranks a checkpoint. This module gains
no authority P0-B1 (submission) or P0-B2 (account/auth) did not already have: it only reads
their state (via the caller) and classifies the relationship between "what the checkpoint
says" and "what is actually true right now." See docs/security/phase0-b3-checkpoint-recovery.md.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from typing import Optional
from urllib.parse import urlparse

import checkpoint as checkpoint_mod
import state_machine

logger = logging.getLogger(__name__)

# Reconciliation outcomes (task section 11). Exact names may differ from any other project's;
# what matters is the distinctions, not the spelling.
MATCH = "MATCH"
AHEAD = "AHEAD"
BEHIND = "BEHIND"
AUTH_REQUIRED = "AUTH_REQUIRED"
APPLICATION_NOT_FOUND = "APPLICATION_NOT_FOUND"
DIFFERENT_APPLICATION = "DIFFERENT_APPLICATION"
OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
SUBMITTED = "SUBMITTED"
UNSUPPORTED = checkpoint_mod.UNSUPPORTED
NO_CHECKPOINT = "NO_CHECKPOINT"

# Account-state kinds that mean "the account step is not blocking progress" -- anything else
# (CODE_ENTRY, MFA_REQUIRED, CREATE_FORM, SIGN_IN_FORM, ...) means the account step still has
# unfinished business, and the *existing* account_state/login_guard machinery (P0-B2), not this
# module, is what resolves it.
_PAST_ACCOUNT_STEP = frozenset({"SIGNED_IN", "NONE", ""})


@dataclass(frozen=True)
class PageIdentity:
    """A coarse, restart-safe 'which stage is this' signal -- deliberately not a full-DOM
    hash (task: 'avoid brittle full-DOM hashing'). No secret or personal field value is ever
    part of this: a tenant hostname and a wizard/progress-bar's own step label are the only
    inputs, both already visible on the page to anyone looking at it."""
    host: str = ""
    step_indicator: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def compute_page_identity(page) -> PageIdentity:
    """Live evidence only. `state_machine.step_indicator_text` is the same selector list the
    loop-detection circuit breaker uses -- one definition, reused, rather than a second copy
    that could drift from it."""
    url = getattr(page, "url", "") or ""
    host = urlparse(url).netloc
    step = state_machine.step_indicator_text(page)
    return PageIdentity(host=host, step_indicator=step)


_STEP_ORDINAL = re.compile(r"(?:step\s*)?(\d+)\s*(?:of|/)\s*(\d+)", re.IGNORECASE)


def parse_step_ordinal(text: str) -> Optional[tuple[int, int]]:
    """('Step 2 of 5', 'Step 2 of 5') -> (2, 5); anything not shaped like 'N of/‑ M' -> None.
    Deliberately narrow: a step label this cannot parse is not guessed at, it falls through to
    OUTCOME_UNKNOWN in `reconcile`, per the task's 'default ambiguous identity to UNKNOWN.'"""
    match = _STEP_ORDINAL.search(text or "")
    if not match:
        return None
    try:
        return int(match.group(1)), int(match.group(2))
    except ValueError:
        return None


@dataclass(frozen=True)
class ReconciliationResult:
    outcome: str
    why: str = ""
    checkpoint: Optional[checkpoint_mod.ApplicationCheckpoint] = None


def reconcile(
    *, stored_payload: Optional[dict], live_identity: PageIdentity, live_account_state_kind: str,
    live_submission_effect_state: Optional[str] = None, application_present: bool = True,
) -> ReconciliationResult:
    """The one place that decides what a resumed run may safely do, by comparing a durable
    checkpoint (evidence about where a run WAS) against live, deterministic evidence about
    where the page/account/submission state IS NOW. Current evidence always outranks the
    checkpoint, never the reverse (task sections 10, 12-16, 38-39).

    All evidence-gathering (reading the live page, calling `account_state.read_state()`,
    `SubmissionProbe.inspect()`, a confirmation-text check) is the caller's job; this function
    takes already-computed values so it can be tested without a browser, the same shape as
    `account_state.read_state()` taking a snapshot string rather than a page object.
    """
    # Already confirmed submitted wins over everything else, checkpoint or not: never
    # resubmit, never re-enter the account/application flow for a done application.
    if live_submission_effect_state == "CONFIRMED":
        return ReconciliationResult(SUBMITTED, "the submission is already confirmed")

    if stored_payload is None:
        return ReconciliationResult(NO_CHECKPOINT, "no checkpoint exists for this application yet")

    status, parsed = checkpoint_mod.parse(stored_payload)
    if status == checkpoint_mod.UNSUPPORTED or parsed is None:
        return ReconciliationResult(UNSUPPORTED, "the stored checkpoint could not be read")

    if not application_present:
        return ReconciliationResult(
            APPLICATION_NOT_FOUND, "the expected application/form is not on the reopened page",
            checkpoint=parsed)

    if live_account_state_kind not in _PAST_ACCOUNT_STEP:
        return ReconciliationResult(
            AUTH_REQUIRED, f"the account step is not past {live_account_state_kind}", checkpoint=parsed)

    checkpoint_identity = parsed.page_identity or {}
    checkpoint_host = checkpoint_identity.get("host") or ""
    if checkpoint_host and live_identity.host and checkpoint_host != live_identity.host:
        return ReconciliationResult(
            DIFFERENT_APPLICATION,
            f"checkpoint host {checkpoint_host!r} does not match live host {live_identity.host!r}",
            checkpoint=parsed)

    checkpoint_step = checkpoint_identity.get("step_indicator") or ""
    live_step = live_identity.step_indicator
    if checkpoint_step == live_step:
        return ReconciliationResult(MATCH, "the checkpoint and the live page agree", checkpoint=parsed)

    checkpoint_ordinal = parse_step_ordinal(checkpoint_step)
    live_ordinal = parse_step_ordinal(live_step)
    if checkpoint_ordinal and live_ordinal and checkpoint_ordinal[1] == live_ordinal[1]:
        if live_ordinal[0] > checkpoint_ordinal[0]:
            return ReconciliationResult(AHEAD, f"{checkpoint_step!r} -> {live_step!r}", checkpoint=parsed)
        if live_ordinal[0] < checkpoint_ordinal[0]:
            return ReconciliationResult(BEHIND, f"{checkpoint_step!r} -> {live_step!r}", checkpoint=parsed)
        return ReconciliationResult(MATCH, "same step number", checkpoint=parsed)

    return ReconciliationResult(
        OUTCOME_UNKNOWN,
        f"checkpoint step {checkpoint_step!r} and live step {live_step!r} are not comparable",
        checkpoint=parsed)
