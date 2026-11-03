"""Who may submit without the owner watching: review vs hybrid (f320).

The safety verdict (safety.evaluate_auto_submit) is never overruled here --
this layer only says when an eligible application still waits:

- SUBMIT_MODE unset: legacy. The verified-only switch rules exactly as before.
- review: every application waits for the owner, however clean.
- hybrid: eligible applications submit themselves until the daily cap
  (AUTO_SUBMIT_DAILY_CAP, default 10; 0 is a kill switch).

Anything else set as the mode waits too, loudly: an unknown mode must never
silently become an automatic one. Full-auto (the agent queueing suggestions
itself) is deliberately not here yet: it needs scheduled applies first.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_CAP = 10


def mode() -> Optional[str]:
    """The submit mode, or None when SUBMIT_MODE is unset (legacy behavior)."""
    return os.environ.get("SUBMIT_MODE", "").strip().lower() or None


def daily_cap() -> int:
    """Auto-submits allowed per day; 0 disables them. Garbage means the default."""
    try:
        return int(os.environ.get("AUTO_SUBMIT_DAILY_CAP", "") or DEFAULT_CAP)
    except ValueError:
        logger.warning("AUTO_SUBMIT_DAILY_CAP is not a number; using %d", DEFAULT_CAP)
        return DEFAULT_CAP


def submitted_today(tracker: Any, today: Optional[str] = None) -> int:
    """Applications confirmed submitted on this date (UTC, ISO)."""
    day = today or datetime.now(timezone.utc).date().isoformat()
    try:
        records = tracker.list_all() or []
    except Exception as exc:
        logger.debug("Could not count today's submits: %s", exc)
        return 0
    count = 0
    for record in records:
        if isinstance(record, dict):
            status, when = record.get("status"), record.get("updated_at")
        else:
            status, when = getattr(record, "status", None), getattr(record, "updated_at", None)
        if isinstance(when, datetime):
            when = when.date().isoformat()
        elif when is not None:
            when = str(when)[:10]
        if status == "submitted" and when == day:
            count += 1
    return count


def auto_submit_allowed(eligible: bool, tracker: Any) -> tuple[bool, str]:
    """Whether an application may submit itself right now, and the plain-words reason."""
    current = mode()
    if current is None:
        return True, "verified-only switch"
    if current == "review":
        return False, "Review mode is on: every application waits for you."
    if current != "hybrid":
        logger.warning("Unknown SUBMIT_MODE %r; waiting for the owner.", current)
        return False, f"Unknown submit mode {current!r}: waiting for you."
    cap = daily_cap()
    if cap <= 0:
        return False, "Auto-submit is off: the daily cap is 0."
    if not eligible:
        return False, "This one needs your review."
    done = submitted_today(tracker)
    if done >= cap:
        return False, f"Daily cap reached ({done} of {cap} submitted today)."
    return True, f"Hybrid auto-submit ({done} of {cap} submitted today)."
