"""How often the agent may try an employer's sign-in, account creation and password reset -- kept
across runs, so a restart or a Continue does not spend another attempt on the same account.

What Workday does with an external candidate's account (researched 25 September 2026):
  * Accounts belong to one employer's site (each *.myworkdayjobs.com tenant); the same email is a
    separate account on every tenant.
  * A wrong password counts against the account: NVIDIA's support says 5 wrong passwords lock it for
    30 minutes (other tenants set 3), and a locked account is reset by an emailed link.
  * The sign-in page says the same thing for a wrong password, an account that does not exist, one
    that is not verified yet and one that is locked: "You may have entered the wrong email address or
    password or your account might be locked." A rejection therefore says nothing about which.
  * "Forgot your password" mails a link (not a code) that lasts 2 hours, and is limited to 5 requests
    in 24 hours ("Failed to initiate password reset, please contact administrator").
  * Whether a new account must have its email verified before it can sign in is the employer's setting.

So the agent stops after a rejection and after it creates an account, and the owner says when to try
again (Continue). At most MAX_FAILED_SIGN_INS rejections are spent per account in 24 hours, well short
of a lock. Nothing here decides who may sign in where: safety.password_allowed still does that.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LOGIN_ATTEMPTS_FILE = Path("data/_login_attempts.json")
WINDOW = timedelta(hours=24)
MAX_FAILED_SIGN_INS = 2      # per account in 24 hours; Workday locks at 3 to 5 in a row
MAX_ACCOUNT_CREATIONS = 2
MAX_RESET_REQUESTS = 1       # Workday allows 5 in 24 hours, and the owner may need some
MAX_CODE_READS = 6           # one-time codes read from the owner's mail for one account in 24 hours


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


def _recent(stamps, now: datetime) -> list[datetime]:
    out = []
    for stamp in stamps or []:
        try:
            when = datetime.fromisoformat(stamp)
        except (TypeError, ValueError):
            continue
        if now - when < WINDOW:
            out.append(when)
    return out


def _entry(data: dict, host: str, email: str) -> dict:
    entry = data.get(_key(host, email))
    return entry if isinstance(entry, dict) else {}


def _stamp() -> str:
    return _now().isoformat(timespec="seconds")


# -- may the agent ...? (None when it may, else why not, in words for the owner) ------------------
def may_sign_in(host: str, email: str) -> Optional[str]:
    entry = _entry(_load(), host, email)
    if entry.get("hold"):
        return str(entry.get("hold_reason") or "the last attempt needs you first")
    now = _now()
    failed = _recent(entry.get("failed_sign_ins"), now)
    if len(failed) >= MAX_FAILED_SIGN_INS:
        until = (min(failed) + WINDOW).astimezone().strftime("%d %b %H:%M")
        return (f"{len(failed)} sign-ins to {host} were rejected in the last 24 hours, and a few more wrong "
                f"passwords lock the account (Workday: 5, for 30 minutes). The agent will not try again "
                f"before {until}.")
    return None


def may_create_account(host: str, email: str) -> Optional[str]:
    entry = _entry(_load(), host, email)
    made = _recent(entry.get("creations"), _now())
    if len(made) >= MAX_ACCOUNT_CREATIONS:
        return f"an account was already tried {len(made)} times on {host} in the last 24 hours"
    return None


def may_read_code(host: str, email: str) -> Optional[str]:
    entry = _entry(_load(), host, email)
    read = _recent(entry.get("code_reads"), _now())
    if len(read) >= MAX_CODE_READS:
        return (f"{len(read)} one-time codes were already read from your mail for {host} in the last 24 hours: "
                f"a site that keeps refusing them needs you")
    return None


def may_request_reset(host: str, email: str) -> Optional[str]:
    entry = _entry(_load(), host, email)
    asked = _recent(entry.get("reset_requests"), _now())
    if len(asked) >= MAX_RESET_REQUESTS:
        return (f"a password reset was already requested for {host} in the last 24 hours (a site allows only "
                f"a few, and its emailed link lasts about 2 hours)")
    return None


# -- what happened ------------------------------------------------------------------------------------
def record_sign_in(host: str, email: str, ok: bool) -> None:
    """A sign-in worked (nothing is held, the count starts again) or was not accepted (it counts, and
    the agent waits for the owner before trying again)."""
    data = _load()
    entry = _entry(data, host, email)
    if ok:
        entry.pop("failed_sign_ins", None)
        entry.pop("hold", None)
        entry.pop("hold_reason", None)
    else:
        entry["failed_sign_ins"] = [*(entry.get("failed_sign_ins") or []), _stamp()][-10:]
        entry["hold"] = "rejected"
        entry["hold_reason"] = (f"{host} did not accept the sign-in. Its message is the same for a wrong password, "
                                f"an account that does not exist, one not yet verified and one that is locked, "
                                f"and every wrong password counts towards a lock. Check the account (verify its "
                                f"email, or reset the password yourself), then press Continue.")
    data[_key(host, email)] = entry
    _save(data)


def record_account_attempt(host: str, email: str) -> None:
    data = _load()
    entry = _entry(data, host, email)
    entry["creations"] = [*(entry.get("creations") or []), _stamp()][-10:]
    data[_key(host, email)] = entry
    _save(data)


def hold_for_verification(host: str, email: str) -> None:
    """An account was created but the site did not show it signed in: it may need its email verified,
    and a sign-in now would only spend an attempt."""
    data = _load()
    entry = _entry(data, host, email)
    entry["hold"] = "new_account"
    entry["hold_reason"] = (f"an account was just created on {host} and the site did not sign it in, so it may "
                            f"need its email verified: open the verification email it sent you, click the link, "
                            f"then press Continue.")
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
    """The owner pressed Continue: whatever held the agent back at this site has been looked at, so it
    may try again (the count of rejections in 24 hours still applies)."""
    host = (host or "").strip().lower()
    if not host:
        return
    data = _load()
    changed = False
    for key, entry in data.items():
        if key.split("|", 1)[0] == host and isinstance(entry, dict) and entry.get("hold"):
            entry.pop("hold", None)
            entry.pop("hold_reason", None)
            changed = True
    if changed:
        _save(data)
