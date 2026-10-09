"""
Perception primitives for Playwright browser automation.

Provides read-only page inspection routines along a strict perception seam:
- is_ant_dropdown: detects Ant Design / rc-select components
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from playwright.sync_api import Page

logger = logging.getLogger(__name__)


def active_dialog(page):
    """The same foreground dialog for page reading and account inspection."""
    dialogs = page.locator('[role="dialog"]:visible, dialog[open]:visible')
    return dialogs.last if dialogs.count() else None

# Roles that accept answers or navigate
ANSWER_ROLES = frozenset({
    "textbox", "searchbox", "combobox", "listbox", "radio",
    "checkbox", "switch", "spinbutton", "slider",
})
PRESS_ROLES = frozenset({"button", "link", "menuitem", "tab"})
OPTION_ROLES = frozenset({
    "option", "menuitemradio", "menuitem", "treeitem", "listitem", "gridcell",
})

# A box that holds a secret: what is typed into it is never kept or sent on. The page text the agent
# reads carries every box's value, and that text is saved to disk and handed to the planner (the API
# or the session), so a password typed into a box was written to a file and, on API runs, sent with
# every page.
_SECRET_BOX_NAME = re.compile(
    r"\bpass ?words?\b|\bpasscodes?\b|\bpass ?phrases?\b|\bone[- ]time\b|\bverification code\b|"
    r"\bsecurity code\b|\bconfirmation code\b|\botp\b|\bpin\b|\bsecret\b",
    re.IGNORECASE)
_BOX_ROLES = ("textbox", "searchbox", "spinbutton")
_SECRET_LINE = re.compile(
    r'^(?P<lead>\s*- )(?P<quote>\')?(?P<role>textbox|searchbox|spinbutton) "(?P<name>(?:[^"\\]|\\.)*)"'
    r'(?P<attrs>(?: \[[^\]]*\])*)(?P=quote)?(?P<sep>:)?(?P<space> ?)(?P<value>.*)$')
HIDDEN = "[hidden]"


def hide_secrets(snapshot: str) -> str:
    """The snapshot with the value of every secret box (password, passcode, one-time code, PIN)
    replaced by [hidden]: an inline value, or the child text line a box with a placeholder draws.
    A hidden value still reads as an answer, so a filled box is still a filled box."""
    lines = (snapshot or "").split("\n")
    out: list[str] = []
    secret_indent = -1                      # indent of the secret box whose child lines are being read
    for line in lines:
        indent = len(line) - len(line.lstrip())
        if secret_indent >= 0:
            if indent > secret_indent:
                if re.match(r"^\s*- text:\s*\S", line):
                    line = re.sub(r"(- text:\s*).*$", rf"\g<1>{HIDDEN}", line)
                out.append(line)
                continue
            secret_indent = -1
        m = _SECRET_LINE.match(line)
        if m and _SECRET_BOX_NAME.search(m.group("name")):
            secret_indent = indent
            if m.group("sep") and m.group("value").strip():
                quote = "'" if m.group("quote") else ""
                line = (f'{m.group("lead")}{quote}{m.group("role")} "{m.group("name")}"{m.group("attrs")}{quote}'
                        f': {HIDDEN}')
        out.append(line)
    return "\n".join(out)


def is_secret_box(name: str) -> bool:
    """A box for a password, passcode, one-time code or PIN. Never answered by an AI: KBI's Workday
    'Password' box was sent to Gemini as a question to answer (28 September)."""
    return bool(_SECRET_BOX_NAME.search(name or ""))


# Anti-bot decoy boxes announce themselves in their own label. Anything typed into one tells the site a
# robot filled the form. Workday's: "Enter website. This input is for robots only, do not enter if
# you're human." The reading agent did not know this rule and asked the AI to answer it.
_HONEYPOT = re.compile(
    r"for robots only|do not (?:enter|fill)[^.]{0,30}if you(?:'|’| a)re (?:a )?human|"
    r"leave (?:this (?:field )?)?blank|honeypot", re.IGNORECASE)


def is_honeypot(label: str) -> bool:
    """A decoy box that a person never fills in; the one place that decides it."""
    return bool(_HONEYPOT.search(label or ""))


def hide_secrets_in_tree(node: Any) -> Any:
    """The same for an accessibility tree given as nested dicts (the forensic dump)."""
    if isinstance(node, dict):
        cleaned = {k: hide_secrets_in_tree(v) for k, v in node.items()}
        if str(cleaned.get("role", "")) in _BOX_ROLES and _SECRET_BOX_NAME.search(str(cleaned.get("name", ""))) \
                and cleaned.get("value"):
            cleaned["value"] = HIDDEN
        return cleaned
    if isinstance(node, list):
        return [hide_secrets_in_tree(item) for item in node]
    return node


def is_ant_dropdown(locator: Any) -> bool:
    """Checks if the locator or any of its ancestors represents an Ant Design / rc-select dropdown."""
    try:
        return bool(locator.evaluate("""el => {
            return Boolean(
                el.classList.contains('ant-select') ||
                el.classList.contains('ant-select-selector') ||
                el.classList.contains('ant-select-selection-search-input') ||
                el.closest('.ant-select') ||
                el.closest('.ant-select-selector') ||
                el.closest('[class*="rc-select"]')
            );
        }"""))
    except Exception:
        return False


def is_ant_single_select(locator: Any) -> bool:
    """An Ant Design select that takes exactly one of its own options and
    nothing else: not an AutoComplete (free text with suggestions) and not a
    multiple or tags select."""
    try:
        return bool(locator.evaluate("""el => {
            const root = el.closest('.ant-select');
            return Boolean(root) && !root.classList.contains('ant-select-auto-complete')
                && !root.classList.contains('ant-select-multiple')
                && !root.classList.contains('ant-select-customize-input');
        }"""))
    except Exception:
        return False


