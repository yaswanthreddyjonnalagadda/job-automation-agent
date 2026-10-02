"""Every screenshot, page copy and page text the agent keeps or sends -- captured one way, with secrets removed.

Architecture review, 1 October: the page text was masked (perception.hide_secrets), but the forensic dumper, the
review package and the stop pages also wrote full screenshots and the raw HTML -- a password typed into a box shows
in a screenshot, and raw HTML carries hidden form tokens, session data in scripts and CSRF values. One screenshot
went to the AI to read a page. Each route had its own capture code, so a fix to one never reached the others.

    screenshot(page, path)      a PNG with every secret box (password, passcode, one-time code, PIN, card number)
                                painted over; `path=None` returns the bytes (for the AI)
    html(page)                  the page's HTML with scripts emptied, hidden inputs and secret boxes' values removed,
                                and token-like meta tags blanked
    page_text(page)             the accessibility text, secret boxes' values hidden
    capture(page, folder, stem) all three side by side; returns their paths
    prune(days)                 deletes evidence older than `days` (EVIDENCE_KEEP_DAYS, default 30)

Nothing here sends anything anywhere; the files stay on this machine (output/, runs/, logs/ are git-ignored).
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Optional

from perception import hide_secrets

logger = logging.getLogger(__name__)

# Boxes whose contents are never kept: by type, by autocomplete purpose, and by the words in their name or label.
SECRET_BOXES = ", ".join([
    "input[type=password]",
    "input[autocomplete=one-time-code]", "input[autocomplete^=cc-]", "input[autocomplete=new-password]",
    "input[autocomplete=current-password]",
    *(f'input[{attr}*="{word}" i]' for attr in ("name", "id", "aria-label", "placeholder")
      for word in ("password", "passcode", "otp", "one-time", "verification", "security code", "pin", "ssn",
                   "social security", "card number", "cvv")),
])

_SANITIZE = """(selector) => {
  const doc = document.documentElement.cloneNode(true);
  doc.querySelectorAll('script, noscript, template').forEach(e => { e.textContent = ''; });
  doc.querySelectorAll('input[type=hidden]').forEach(e => e.removeAttribute('value'));
  const secret = new Set(document.querySelectorAll(selector));
  // A secret box keeps no value in the copy, whatever its attribute says.
  const live = [...document.querySelectorAll('input, textarea')];
  const copies = [...doc.querySelectorAll('input, textarea')];
  live.forEach((e, i) => {
    const c = copies[i]; if (!c) return;
    if (secret.has(e)) { c.removeAttribute('value'); c.textContent = ''; c.setAttribute('data-hidden', 'secret'); }
  });
  doc.querySelectorAll('meta[name*=csrf i], meta[name*=token i], meta[name*=session i]')
     .forEach(e => e.setAttribute('content', ''));
  return '<!doctype html>\\n' + doc.outerHTML;
}"""


def _secret_locators(page) -> list:
    try:
        boxes = page.locator(SECRET_BOXES)
        return [boxes] if boxes.count() else []
    except Exception:
        return []


def screenshot(page, path: Optional[Path] = None, full_page: bool = True, timeout: float = 15_000):
    """A screenshot with every secret box painted over. Returns the bytes when `path` is None."""
    kwargs = {"full_page": full_page, "timeout": timeout, "mask": _secret_locators(page)}
    if path is not None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        kwargs["path"] = str(path)
    return page.screenshot(**kwargs)


def html(page) -> str:
    """The page's HTML with scripts, hidden-input values, secret boxes' values and token meta tags removed."""
    try:
        return page.evaluate(_SANITIZE, SECRET_BOXES) or ""
    except Exception as exc:
        logger.info("Could not read the page's HTML safely (%s): none kept", str(exc).splitlines()[0][:80])
        return ""


def page_text(page) -> str:
    try:
        return hide_secrets(page.locator("body").aria_snapshot(mode="ai"))
    except Exception:
        return ""


def capture(page, folder: Path, stem: str, *, shot: bool = True, page_html: bool = True,
            text: bool = True) -> dict[str, str]:
    """Screenshot, HTML and page text of the page, safely, under folder/stem.*; the paths written."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    if shot:
        try:
            screenshot(page, folder / f"{stem}.png")
            written["screenshot"] = str(folder / f"{stem}.png")
        except Exception as exc:
            logger.info("Could not take the screenshot: %s", str(exc).splitlines()[0][:100])
    if page_html:
        content = html(page)
        if content:
            (folder / f"{stem}.html").write_text(content, encoding="utf-8")
            written["html"] = str(folder / f"{stem}.html")
    if text:
        content = page_text(page)
        if content:
            try:
                url = page.url
            except Exception:
                url = ""
            (folder / f"{stem}.txt").write_text(f"{url}\n\n{content}", encoding="utf-8")
            written["text"] = str(folder / f"{stem}.txt")
    return written


# What pruning removes, by folder, relative to the project: the forensic dumps and account-failure captures whole,
# and in output/ only screenshots and page copies -- output/<job>/pages/*.txt is the replay guard's record of real
# pages (replay_guard.py), and the documents are the applications' own. data/ and the tracker are never touched.
PRUNE = {"runs": (".png", ".html", ".txt", ".json"), "logs/account_failures": (".png", ".html", ".txt"),
         "logs/account_steps": (".png", ".txt"),
         "output": (".png", ".html")}


def keep_days() -> int:
    try:
        return max(1, int(os.getenv("EVIDENCE_KEEP_DAYS", "30")))
    except ValueError:
        return 30


def prune(days: Optional[int] = None, root: Optional[Path] = None) -> int:
    """Deletes evidence older than `days`; returns how many files went. Documents (PDFs) are never pruned."""
    days = days or keep_days()
    root = Path(root) if root else Path(__file__).resolve().parent
    cutoff = time.time() - days * 86_400
    removed = 0
    for name, suffixes in PRUNE.items():
        base = root / name
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            try:
                if path.is_file() and path.suffix.lower() in suffixes and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        if name == "runs":                          # a forensic dump is one folder: remove it once empty
            for folder in sorted((p for p in base.iterdir() if p.is_dir()), reverse=True):
                if not any(folder.iterdir()):
                    shutil.rmtree(folder, ignore_errors=True)
    if removed:
        logger.info("EVIDENCE: removed %d file(s) older than %d days", removed, days)
    return removed
