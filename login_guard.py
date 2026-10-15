"""Diagnostic history of employer authentication actions.

The owner removed cross-run authentication quotas and rejection holds on 12 October
2026. Stored history and legacy holds never block sign-in, creation, reset or email
verification. Live portal outcomes, credential policy, CAPTCHA and durable uncertain
account-creation reconciliation remain the caller's responsibility.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LOGIN_ATTEMPTS_FILE = Path("data/_login_attempts.json")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _key(host: str, email: str) -> str:
    return f"{(host or '').strip().lower()}|{(email or '').strip().lower()}"


def _load() -> dict:
    try:
        path = Path(LOGIN_ATTEMPTS_FILE)
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    try:
        path = Path(LOGIN_ATTEMPTS_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        scratch = path.with_name(path.name + ".tmp")
        scratch.write_text(json.dumps(data, indent=1), encoding="utf-8")
        scratch.replace(path)
    except Exception as exc:
        logger.warning("Could not record the sign-in attempt: %s", str(exc).splitlines()[0][:100])



def _entry(data: dict, host: str, email: str) -> dict:
    entry = data.get(_key(host, email))
    return entry if isinstance(entry, dict) else {}


def _stamp() -> str:
    return _now().isoformat(timespec="seconds")


# -- may the agent ...? (None when it may, else why not, in words for the owner) ------------------
def may_sign_in(host: str, email: str) -> Optional[str]:
    """Attempt history is diagnostic only; it never imposes a sign-in cooldown."""
    return None


def refused_before(host: str, email: str) -> bool:
    """Legacy persisted rejections do not require an owner Continue action."""
    return False


def may_create_account(host: str, email: str) -> Optional[str]:
    return None


def may_read_code(host: str, email: str) -> Optional[str]:
    return None


def may_request_reset(host: str, email: str) -> Optional[str]:
    return None


# -- what happened ------------------------------------------------------------------------------------
def record_sign_in(host: str, email: str, ok: bool) -> None:
    """Record the outcome without creating a persistent rejection hold."""
    data = _load()
    entry = _entry(data, host, email)
    entry.pop("hold", None)
    entry.pop("hold_reason", None)
    if ok:
        entry.pop("failed_sign_ins", None)
    else:
        entry["failed_sign_ins"] = [*(entry.get("failed_sign_ins") or []), _stamp()][-10:]
    data[_key(host, email)] = entry
    _save(data)


def record_account_attempt(host: str, email: str) -> None:
    data = _load()
    entry = _entry(data, host, email)
    entry["creations"] = [*(entry.get("creations") or []), _stamp()][-10:]
    data[_key(host, email)] = entry
    _save(data)


def record_code_read(host: str, email: str) -> None:
    data = _load()
    entry = _entry(data, host, email)
    entry["code_reads"] = [*(entry.get("code_reads") or []), _stamp()][-20:]
    data[_key(host, email)] = entry
    _save(data)


def record_reset_request(host: str, email: str) -> None:
    data = _load()
    entry = _entry(data, host, email)
    entry["reset_requests"] = [*(entry.get("reset_requests") or []), _stamp()][-10:]
    data[_key(host, email)] = entry
    _save(data)


def clear_hold(host: str, email: str) -> None:
    data = _load()
    entry = _entry(data, host, email)
    if entry.pop("hold", None) is not None:
        entry.pop("hold_reason", None)
        data[_key(host, email)] = entry
        _save(data)


def owner_resumed(host: str) -> None:
    """Clean up legacy holds; attempt counts have no gating effect."""
    host = (host or "").strip().lower()
    if not host:
        return
    data = _load()
    changed = False
    for key, entry in data.items():
        if key.split("|", 1)[0] == host and isinstance(entry, dict):
            changed = entry.pop("hold", None) is not None or changed
            changed = entry.pop("hold_reason", None) is not None or changed
    if changed:
        _save(data)
