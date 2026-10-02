"""Where an application has got to, what the agent was in the middle of, and whether a reopened page is it.

Architecture review, 1 October. Resume used to trust two things it should check:
  * the page it reopened -- any visible input counted as "the application", and a page that could not be read
    was carried on with (careers home pages and unrelated forms looked like the application);
  * that nothing was half-done -- a run cut off between pressing Submit and seeing the result would, on Resume,
    simply go on, and could send twice.

A checkpoint is one small JSON file per application in data/checkpoints/ (local, git-ignored):
    application  key, job url, title, company, the ATS host
    verified     the last page the agent saw and understood: url, step ("3 of 7"), when, which code version
    pending      an action started and not yet seen through: {"action": "submit", "started": ..., "code": ...}
    history      the last few actions and how each ended

`begin` writes a pending action before it is taken and `finish` clears it once its result is seen. A pending
action found when a run starts is an outcome nobody saw: the run stops and says what to check, and never takes
the action again by itself. `reconcile` decides whether a reopened page is this application.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

FOLDER = Path("data/checkpoints")
# Actions whose result must be seen before anything else happens: taking them twice does harm.
ONCE_ONLY = ("submit",)
SAME_APPLICATION, POSTING, DIFFERENT, UNREADABLE = "same_application", "posting", "different", "unreadable"


@lru_cache(maxsize=1)
def code_version() -> str:
    """The commit the running code came from, with '+changes' when the working copy differs from it."""
    root = Path(__file__).resolve().parent
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True,
                             timeout=5).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root,
                               capture_output=True, text=True, timeout=5).stdout.strip()
        return (sha or "unknown") + ("+changes" if dirty else "")
    except Exception:
        return "unknown"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(key: str) -> Path:
    return FOLDER / f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', key or 'unknown')[:120]}.json"


def load(key: str) -> dict:
    try:
        data = json.loads(_path(key).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(key: str, data: dict) -> None:
    try:
        FOLDER.mkdir(parents=True, exist_ok=True)
        tmp = _path(key).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(_path(key))
    except OSError as exc:                          # a checkpoint that cannot be written never stops a run
        logger.info("Could not save the checkpoint: %s", exc)


def note_application(key: str, job) -> None:
    data = load(key)
    url = str(getattr(job, "url", "") or "")
    # The hosts the application was already worked on (verified) are kept: a new run used to overwrite them, and
    # the next resume called the employer's Workday site "not where this application was" (Waystar, 2 October).
    hosts = list((data.get("application") or {}).get("hosts") or [])
    data["application"] = {"key": key, "url": url, "title": str(getattr(job, "title", "") or ""),
                           "company": str(getattr(job, "company", "") or ""),
                           "host": urlparse(url).netloc.lower(), "hosts": hosts}
    _save(key, data)


def verified(key: str, url: str, step: str = "") -> None:
    """The agent saw and understood this page of the application."""
    data = load(key)
    data["verified"] = {"url": url, "step": step, "at": _now(), "code": code_version()}
    data.setdefault("application", {}).setdefault("hosts", [])
    host = urlparse(url or "").netloc.lower()
    if host and host not in data["application"]["hosts"]:
        data["application"]["hosts"].append(host)
    _save(key, data)


def begin(key: str, action: str, detail: str = "") -> None:
    """About to take an action whose result must be seen (written before the click)."""
    data = load(key)
    data["pending"] = {"action": action, "detail": detail[:200], "started": _now(), "code": code_version()}
    _save(key, data)


def finish(key: str, action: str, outcome: str) -> None:
    """The action's result was seen: it is no longer pending."""
    data = load(key)
    pending = data.pop("pending", None) or {}
    history = data.get("history") or []
    history.append({"action": action, "outcome": outcome[:200], "started": pending.get("started", ""),
                    "ended": _now(), "code": code_version()})
    data["history"] = history[-20:]
    _save(key, data)


def unresolved(key: str) -> Optional[dict]:
    """A once-only action a previous run started and never saw through, or None."""
    pending = load(key).get("pending") or {}
    return pending if pending.get("action") in ONCE_ONLY else None


def owner_checked(key: str) -> None:
    """The owner looked at what happened (pressed Continue / Resume): the pending action is closed."""
    pending = load(key).get("pending")
    if pending:
        finish(key, str(pending.get("action") or ""), "checked by the owner")


def what_to_check(pending: dict, job) -> str:
    action = pending.get("action")
    when = str(pending.get("started") or "")[:16].replace("T", " ")
    if action == "submit":
        return (f"Submit was pressed for {getattr(job, 'title', 'this job')} at {getattr(job, 'company', '')} "
                f"(UTC {when}) and the run ended before the result was seen. Check your email and the employer's "
                f"portal for a confirmation before doing anything else; the agent will not press Submit again. "
                f"Press Continue once you have checked.")
    return f"'{action}' was started at {when} UTC and its result was not seen: check it, then press Continue."


@dataclass(frozen=True)
class Reconciled:
    verdict: str
    why: str

    @property
    def is_the_application(self) -> bool:
        return self.verdict in (SAME_APPLICATION, POSTING)


_STEP = re.compile(r"\b(?:step|page)\s*\d+\s*(?:of|/)\s*\d+\b|application progress", re.IGNORECASE)


def reconcile(key: str, page, on_posting) -> Reconciled:
    """Whether a reopened page is this application, judged by the page and the checkpoint together.

    The application's own host (or a host it was verified on) is required; then the job posting itself, or the
    job's title on the page, or an application step counter, or -- on the very page last verified -- a visible
    form. A page that cannot be read is UNREADABLE, never taken for the application."""
    app = load(key).get("application") or {}
    try:
        url = page.url or ""
        host = urlparse(url).netloc.lower()
        hosts = {h for h in [app.get("host", ""), *(app.get("hosts") or [])] if h}
        if hosts and host not in hosts:
            return Reconciled(DIFFERENT, f"{host} is not where this application was ({', '.join(sorted(hosts))})")
        if on_posting(page):
            return Reconciled(POSTING, "the job posting")
        page.wait_for_timeout(2_000)              # Workday draws the form after the page has loaded
        text = page.inner_text("body", timeout=5_000) or ""
        title = str(app.get("title") or "").strip()
        if title and _title_words(title) and all(w in text.lower() for w in _title_words(title)):
            return Reconciled(SAME_APPLICATION, f"the page names the job ({title[:60]})")
        if _STEP.search(text):
            return Reconciled(SAME_APPLICATION, "an application step counter")
        last = (load(key).get("verified") or {}).get("url", "")
        if last and url.split("#")[0] == last.split("#")[0] and _visible_form(page):
            return Reconciled(SAME_APPLICATION, "the page last verified, still showing its form")
        return Reconciled(DIFFERENT, "nothing on the page shows this application")
    except Exception as exc:
        return Reconciled(UNREADABLE, f"the page could not be read ({str(exc).splitlines()[0][:80]})")


def _title_words(title: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 2][:4]


def _visible_form(page) -> bool:
    for frame in page.frames:
        if frame.evaluate("""() => [...document.querySelectorAll(
                'input:not([type=hidden]):not([type=search]):not([type=submit]):not([type=button]), '
                + 'textarea, select, [role=combobox], [role=radio], [role=checkbox]')]
            .some(e => !!(e.offsetParent || e.getClientRects().length))"""):
            return True
    return False
