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
