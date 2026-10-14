"""A phone alert (ntfy) when the agent needs the owner.

Off unless NTFY_TOPIC is set in .env (a long private topic name; install the ntfy app and subscribe to it).
NTFY_SERVER names a self-hosted server; the default is https://ntfy.sh. Best effort: a missing network never stops
an application. The message goes through that server, so it carries only what is needed -- the company, the job and
which kind of help -- never an answer the owner typed, a password, a code or an email address.
"""
from __future__ import annotations

import logging
import os
import re
import urllib.request
from typing import Iterable

import diagnostics

logger = logging.getLogger(__name__)

MESSAGE_LIMIT = 300
_QUOTED_VALUE = re.compile(r"""\s*=\s*(?:'[^']*'|"[^"]*")""")


def configured() -> bool:
    return bool(os.environ.get("NTFY_TOPIC", "").strip())


def _ascii(text: str) -> str:
    return (text or "").encode("ascii", "replace").decode("ascii").replace("?", "").strip() or "Job agent"


def _safe_message(text: str) -> str:
    text = _QUOTED_VALUE.sub("", text or "")          # "Notice period = '2 weeks'" -> "Notice period"
    return " ".join(diagnostics.sanitize_text(text).split())[:MESSAGE_LIMIT]


def send(title: str, message: str, *, priority: int = 3, tags: Iterable[str] = (), click_url: str = "") -> bool:
    """One message to the owner's phone. True when the server took it."""
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return False
    server = (os.environ.get("NTFY_SERVER", "") or "https://ntfy.sh").strip().rstrip("/")
    headers = {"Title": _ascii(title)[:120], "Priority": str(max(1, min(5, int(priority))))}
    if tags:
        headers["Tags"] = ",".join(tags)
    if click_url:
        headers["Click"] = click_url
    request = urllib.request.Request(f"{server}/{topic}", data=_safe_message(message).encode("utf-8"),
                                     headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as reply:
            return 200 <= getattr(reply, "status", 200) < 300
    except Exception as exc:                                  # best effort: never stop an application
        logger.info("PHONE_ALERT: not sent (%s)", type(exc).__name__)
        return False


def describe_need(company: str, title: str, kind: str, reasons: Iterable[str]) -> tuple[str, str, int, tuple]:
    """(title, message, priority, tags) for a run that stopped and needs the owner."""
    reasons = [r for r in reasons if r]
    job = f"{company} - {title}".strip(" -")
    if kind == "captcha":
        head, priority, tags = "CAPTCHA to solve", 5, ("rotating_light",)
    elif any("still blank" in r for r in reasons):
        head, priority, tags = "Questions the agent can't answer", 4, ("question",)
    else:
        head, priority, tags = "Needs you", 4, ("warning",)
    detail = "; ".join(reasons[:3])
    return f"{head}: {company}".strip(": "), f"{job}. {detail}".strip(), priority, tags
