"""Durable application checkpoint (Phase 0-B3).

A checkpoint is evidence about where a run was, never authority over where it is now.
It is read back only as a candidate for recovery.reconcile() to compare against live,
deterministic browser/tracker evidence -- exactly as a checkpointed account state never
substitutes for account_state.read_state() (P0-B2), and a checkpointed submission-effect
state never substitutes for SubmissionGuardV0 (P0-B1). See
docs/security/phase0-b3-checkpoint-recovery.md.

No secret ever belongs in this object: no password, OTP, magic-link token, session
cookie, or private browser storage. Everything here is either already shown on the
page, or a non-secret identity/bookkeeping fact (a sha256 of an uploaded file, an
account-state *kind* name, a step label).
"""

from __future__ import annotations

import json
import logging
import subprocess
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

COMPATIBLE = "COMPATIBLE"
MIGRATION_REQUIRED = "MIGRATION_REQUIRED"
UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class ApplicationCheckpoint:
    application_key: str
    dedup_key: str = ""
    employer: str = ""
    ats: str = ""
    checkpoint_id: str = ""
    created_at: str = ""
    code_version: str = ""
    page_url: str = ""
    page_identity: dict = field(default_factory=dict)
    verified_stage: str = ""
    account_state: str = ""
    completed_controls: tuple = ()
    uploaded_documents: dict = field(default_factory=dict)
    last_verified_action: str = ""
    pending_action: str = ""
    uncertain_actions: tuple = ()
    handoff_reason: str = ""
    submission_effect_state: str = ""
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        data = asdict(self)
        data["completed_controls"] = list(self.completed_controls)
        data["uncertain_actions"] = list(self.uncertain_actions)
        return data


def new_checkpoint_id() -> str:
    return uuid.uuid4().hex


_CODE_VERSION: Optional[str] = None


def code_version() -> str:
    """A short, best-effort code-version marker for diagnostics only -- a checkpoint from a
    different code version is never rejected on that basis alone; see recovery.reconcile()."""
    global _CODE_VERSION
    if _CODE_VERSION is None:
        _CODE_VERSION = _git_short_sha()
    return _CODE_VERSION


def _git_short_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
            timeout=2, cwd=Path(__file__).resolve().parent,
        )
        if out.returncode == 0:
            sha = out.stdout.strip()
            if sha:
                return sha
    except Exception:
        pass
    return "unknown"


def build(
    *, application_key: str, dedup_key: str = "", employer: str = "", ats: str = "",
    page_url: str = "", page_identity: Optional[dict] = None, verified_stage: str = "",
    account_state: str = "", completed_controls: tuple = (),
    uploaded_documents: Optional[dict] = None, last_verified_action: str = "",
    pending_action: str = "", uncertain_actions: tuple = (), handoff_reason: str = "",
    submission_effect_state: str = "",
) -> ApplicationCheckpoint:
    """Builds a fresh checkpoint with its own id/timestamp/code-version filled in, so call
    sites only ever state what changed."""
    return ApplicationCheckpoint(
        application_key=application_key, dedup_key=dedup_key, employer=employer, ats=ats,
        checkpoint_id=new_checkpoint_id(), created_at=datetime.now(timezone.utc).isoformat(),
        code_version=code_version(), page_url=page_url, page_identity=dict(page_identity or {}),
        verified_stage=verified_stage, account_state=account_state,
        completed_controls=tuple(completed_controls), uploaded_documents=dict(uploaded_documents or {}),
        last_verified_action=last_verified_action, pending_action=pending_action,
        uncertain_actions=tuple(uncertain_actions), handoff_reason=handoff_reason,
        submission_effect_state=submission_effect_state, schema_version=SCHEMA_VERSION,
    )


def parse(payload: Any) -> tuple[str, Optional[ApplicationCheckpoint]]:
    """Classifies a durably-stored payload as COMPATIBLE / MIGRATION_REQUIRED / UNSUPPORTED.

    Never raises: a malformed payload, or one from a schema version this code does not
    understand, is simply evidence that could not be used -- not a reason to crash the
    workflow. The caller falls back to inspecting the live browser/application state fresh,
    exactly as it already does when there is no checkpoint at all (pre-B3 applications)."""
    if not isinstance(payload, dict):
        return UNSUPPORTED, None
    try:
        version = int(payload.get("schema_version", 0))
    except (TypeError, ValueError):
        return UNSUPPORTED, None
    if version > SCHEMA_VERSION:
        return UNSUPPORTED, None
    status = COMPATIBLE if version == SCHEMA_VERSION else MIGRATION_REQUIRED
    if version < SCHEMA_VERSION:
        migrated = _migrate(dict(payload), version)
        if migrated is None:
            return UNSUPPORTED, None
        payload = migrated
    try:
        checkpoint = ApplicationCheckpoint(
            application_key=str(payload.get("application_key") or ""),
            dedup_key=str(payload.get("dedup_key") or ""),
            employer=str(payload.get("employer") or ""),
            ats=str(payload.get("ats") or ""),
            checkpoint_id=str(payload.get("checkpoint_id") or ""),
            created_at=str(payload.get("created_at") or ""),
            code_version=str(payload.get("code_version") or ""),
            page_url=str(payload.get("page_url") or ""),
            page_identity=dict(payload.get("page_identity") or {}),
            verified_stage=str(payload.get("verified_stage") or ""),
            account_state=str(payload.get("account_state") or ""),
            completed_controls=tuple(payload.get("completed_controls") or ()),
            uploaded_documents=dict(payload.get("uploaded_documents") or {}),
            last_verified_action=str(payload.get("last_verified_action") or ""),
            pending_action=str(payload.get("pending_action") or ""),
            uncertain_actions=tuple(payload.get("uncertain_actions") or ()),
            handoff_reason=str(payload.get("handoff_reason") or ""),
            submission_effect_state=str(payload.get("submission_effect_state") or ""),
            schema_version=SCHEMA_VERSION,
        )
    except Exception:
        return UNSUPPORTED, None
    if not checkpoint.application_key:
        return UNSUPPORTED, None
    return status, checkpoint


# Fields that must never silently revert to their default just because a generic/partial
# update did not mention them. An unresolved consequential action (Phase 0-B3 closure
# correction) is evidence that must survive an unrelated progress checkpoint; clearing one is
# only ever done by naming it explicitly in a merge_update() call, never a side effect of
# updating something else. (merge_update()'s own replace()-based design already gives every
# field this property -- this tuple exists to say in code which fields it is load-bearing
# for, and is asserted by test_checkpoint_state.py.)
STICKY_FIELDS = ("pending_action", "uncertain_actions")


def merge_update(existing: Optional[dict], **changes: Any) -> dict:
    """The one safe way to update a durable checkpoint. Starts from `existing` (a previously
    stored payload, or None for a brand-new application) and applies only the fields named in
    `changes` -- anything not named carries over from `existing` unchanged. A generic
    progress update (a new page URL, a new stage, a handoff reason) must never silently blank
    out an unresolved `pending_action`/`uncertain_actions` it was never told about; to
    actually clear one, name it explicitly: `merge_update(existing, pending_action="")`.

    `existing` is read defensively through `parse()`: a payload from an unsupported/future
    schema, or one that is simply corrupt, is treated the same as no existing checkpoint --
    this never raises, and never requires a caller to hand-reconstruct the other fields to
    avoid losing them."""
    base = None
    if existing is not None:
        status, parsed = parse(existing)
        if status != UNSUPPORTED:
            base = parsed
    if base is None:
        seed_key = str(changes.get("application_key") or (existing or {}).get("application_key") or "")
        base = build(application_key=seed_key)
    if "completed_controls" in changes:
        changes["completed_controls"] = tuple(changes["completed_controls"] or ())
    if "uncertain_actions" in changes:
        changes["uncertain_actions"] = tuple(changes["uncertain_actions"] or ())
    if "page_identity" in changes:
        changes["page_identity"] = dict(changes["page_identity"] or {})
    if "uploaded_documents" in changes:
        changes["uploaded_documents"] = dict(changes["uploaded_documents"] or {})
    changes.setdefault("checkpoint_id", new_checkpoint_id())
    changes.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    changes["code_version"] = code_version()
    changes["schema_version"] = SCHEMA_VERSION
    updated = replace(base, **changes)
    return updated.to_dict()


def _migrate(payload: dict, from_version: int) -> Optional[dict]:
    """No schema older than the first one (1) exists yet, so there is nothing to migrate
    from today -- any version below 1 is unrecognised, not a real migration case. Kept as
    its own function, rather than inlined into parse(), so the first real migration has one
    obvious place to grow instead of a rewrite of parse() itself."""
    return None
