"""How an application's history, answers and auto-submit decision are shown. Display only: nothing here changes a record."""
from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Iterable, Optional

from markupsafe import Markup

EVENT_LABELS = {
    "auto_submit": "Auto-submit check",
    "handoff_started": "Stopped for you",
    "handoff_resolved": "You handled it",
    "note": "Note",
    "resume_attached": "Resume attached",
    "status_change": "Status changed",
    "step": "Step finished",
    "action_outcome_unknown": "Outcome not confirmed",
    "invalid_authorization": "Authorization refused",
}


def event_label(kind) -> str:
    kind = str(kind or "").strip()
    return EVENT_LABELS.get(kind) or (kind.replace("_", " ").capitalize() if kind else "Event")


def group_events(events: Iterable) -> list[dict]:
    """Consecutive events with the same kind and message become one row with a count (newest first stays newest first)."""
    grouped: list[dict] = []
    for event in events:
        get = event.get if isinstance(event, dict) else lambda key, default=None, _e=event: getattr(_e, key, default)
        kind, message = get("kind"), (get("message") or "")
        if grouped and grouped[-1]["kind"] == kind and grouped[-1]["message"] == message:
            row = grouped[-1]
            row["count"] += 1
            row["since"] = get("created_at")
            row["screenshot_path"] = row["screenshot_path"] or get("screenshot_path")
            row["html_path"] = row["html_path"] or get("html_path")
            continue
        grouped.append({"kind": kind, "label": event_label(kind), "message": message, "count": 1,
                        "created_at": get("created_at"), "since": None,
                        "screenshot_path": get("screenshot_path"), "html_path": get("html_path")})
    return grouped


_ICONS = {
    "profile": '<path d="M6 3h9l3 3v15H6z M9 11h6 M9 15h6" />',
    "you": '<circle cx="12" cy="8" r="3.5" /><path d="M5 20c0-4 3-6 7-6s7 2 7 6" />',
    "ai": '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" />',
    "site": '<circle cx="12" cy="12" r="8" /><path d="M4 12h16 M12 4c3 3 3 13 0 16 M12 4c-3 3-3 13 0 16" />',
    "agent": '<rect x="5" y="7" width="14" height="11" rx="3" /><path d="M12 7V4 M9 12h.01 M15 12h.01" />',
}
_BY = {
    "profile": ("profile", "From profile"), "resume": ("profile", "From resume"),
    "deterministic_rule": ("profile", "From a saved rule"), "verified_agent": ("agent", "Agent (verified)"),
    "agent": ("agent", "Agent"), "user": ("you", "You"), "user_override": ("you", "You"),
    "claude": ("ai", "AI"), "gemini": ("ai", "AI"), "ai_inference": ("ai", "AI"), "site_default": ("site", "Site default"),
}


def answered_by(value) -> Markup:
    """An icon and plain words for who gave an answer; an unknown source is shown as it was recorded."""
    raw = str(value or "").strip()
    key, text = _BY.get(raw.lower(), ("agent", raw or "Agent"))
    svg = (f'<svg class="by-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" '
           f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{_ICONS[key]}</svg>')
    return Markup(f'<span class="by by-{key}">{svg}<span>{html.escape(text)}</span></span>')


def _when(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def decision_view(decision: dict, status_group: str) -> dict:
    """What the auto-submit decision means for this application, in words."""
    eligible = bool(decision.get("eligible"))
    reasons = [str(r) for r in (decision.get("reasons") or [])]
    sent = status_group == "done"
    if eligible:
        note = "Auto-submit was allowed for this application." if sent else "Auto-submit was allowed; it has not sent this yet."
    elif sent:
        note = "You submitted this yourself; auto-submit did not send it."
    else:
        note = "Auto-submit did not send this; it is waiting for you."
    return {"verdict": "Eligible" if eligible else "Not eligible", "eligible": eligible,
            "when": _when(decision.get("decided_at")), "reasons": reasons, "note": note,
            "no_reasons": "" if reasons or eligible else "No reasons were stored for this decision."}
