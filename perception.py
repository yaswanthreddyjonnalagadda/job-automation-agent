"""
Perception primitives for Playwright browser automation.

Provides read-only page inspection routines along a strict perception seam:
- is_ant_dropdown: detects Ant Design / rc-select components
- find_active_draft_cards: scans DOM for uncommitted experience/education cards in edit/draft modes
- is_field_active: checks if an input/control is visible, enabled, and editable
- read_control_attributes: extracts role, aria attributes, label, and current value
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from playwright.sync_api import Locator, Page

logger = logging.getLogger(__name__)

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


def find_active_draft_cards(page: Page) -> list[dict[str, Any]]:
    """Scans the DOM for inline resume-parsed experience or education cards
    that are currently in active 'Edit' or 'Draft' modes.
    
    Returns structured descriptors for any detected active draft cards
    without performing any mutations.
    """
    try:
        cards_data = page.evaluate("""() => {
            const results = [];
            const saveBtnPattern = /^(save entry|update|save|done|apply changes)$/i;
            
            // Query potential card containers
            const candidates = document.querySelectorAll(
                '[data-automation-id*="experience" i], [data-automation-id*="education" i], ' +
                '[class*="experience" i], [class*="education" i], [class*="card" i], ' +
                '[class*="entry" i], fieldset, div[role="group"]'
            );
            
            const seen = new Set();
            for (const container of candidates) {
                // Must have inputs to be in edit/draft mode
                const inputs = container.querySelectorAll('input, select, textarea');
                if (inputs.length === 0) continue;
                
                // Find commit buttons inside this container
                const buttons = container.querySelectorAll('button, [role="button"]');
                const internalButtons = [];
                for (const btn of buttons) {
                    const text = (btn.innerText || btn.textContent || '').trim();
                    if (saveBtnPattern.test(text) && btn.offsetParent !== null) {
                        internalButtons.push(text);
                    }
                }
                
                if (internalButtons.length > 0 && !seen.has(container)) {
                    seen.add(container);
                    results.push({
                        has_active_inputs: inputs.length,
                        commit_buttons: internalButtons,
                    });
                }
            }
            return results;
        }""")
        return cards_data
    except Exception as exc:
        logger.debug("Failed while finding active draft cards: %s", exc)
        return []


def is_field_active(locator: Any) -> bool:
    """Checks whether a form field locator is visible, enabled, editable, and not inert."""
    try:
        if not locator.is_visible(timeout=1_000):
            return False
        if not locator.is_enabled(timeout=1_000):
            return False
        is_inert = locator.evaluate("""el => {
            return Boolean(
                el.disabled ||
                el.readOnly ||
                el.getAttribute('aria-disabled') === 'true' ||
                el.closest('[disabled], [aria-disabled="true"], .ant-picker-disabled, [aria-readonly="true"]') ||
                window.getComputedStyle(el).pointerEvents === 'none' ||
                window.getComputedStyle(el).display === 'none' ||
                window.getComputedStyle(el).visibility === 'hidden'
            );
        }""")
        return not is_inert
    except Exception:
        return False


def read_control_attributes(locator: Any) -> dict[str, Any]:
    """Reads role, aria attributes, label, and current value of a locator."""
    try:
        return locator.evaluate("""el => {
            return {
                tag: el.tagName ? el.tagName.toLowerCase() : '',
                role: el.getAttribute('role') || '',
                aria_label: el.getAttribute('aria-label') || '',
                aria_expanded: el.getAttribute('aria-expanded') || '',
                placeholder: el.getAttribute('placeholder') || '',
                value: 'value' in el ? el.value : (el.innerText || ''),
                class_name: el.className || '',
            };
        }""")
    except Exception as exc:
        logger.debug("read_control_attributes failed: %s", exc)
        return {}
