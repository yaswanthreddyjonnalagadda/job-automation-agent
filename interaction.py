"""
Interaction primitives for Playwright browser automation.

Provides core physical action dispatchers:
- fill_and_dispatch: fills inputs and dispatches bubbling synthetic events (input, change, blur)
- resolve_ant_dropdown: handles Ant Design rc-select elements via selector mousedown and detached portal matching
- commit_draft_cards: pre-navigation sweep for uncommitted resume experience/education draft cards
- click_resiliently: resilient multi-strategy click handler
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Optional

from playwright.sync_api import Locator, Page

import safety

logger = logging.getLogger(__name__)

# Patterns for internal card save buttons (case-insensitive)
CARD_COMMIT_TEXT_PATTERN = re.compile(
    r"^\s*(save entry|update|save|done|apply changes)\s*$",
    re.IGNORECASE,
)


def fill_and_dispatch(locator: Any, value: str, timeout: Optional[float] = None, **kwargs: Any) -> None:
    """Fills an input using Playwright's native .fill() and immediately dispatches
    three bubbling synthetic events: 'input', 'change', and 'blur'.

    This ensures reactive/virtual DOM frameworks (React, Vue, Angular, Ant Design)
    register controlled component state and do not silently drop field values on form submission.
    """
    fill_kwargs = dict(kwargs)
    if timeout is not None:
        fill_kwargs["timeout"] = timeout

    locator.fill(value, **fill_kwargs)
    try:
        locator.evaluate("""el => {
            el.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
            el.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true, cancelable: true }));
        }""")
    except Exception as exc:
        logger.debug("Synthetic event dispatch on locator failed: %s", exc)


def click_resiliently(locator: Any, timeout_ms: int = 4_000) -> bool:
    """Clicks an element resiliently, trying normal click, forced click, and JS dispatch."""
    try:
        locator.click(timeout=timeout_ms)
        return True
    except Exception:
        try:
            locator.click(force=True, timeout=min(timeout_ms, 2_000))
            return True
        except Exception:
            try:
                locator.evaluate("el => el.click()")
                return True
            except Exception as exc:
                logger.debug("click_resiliently failed: %s", exc)
                return False


def resolve_ant_dropdown(
    page_or_tab: Any,
    locator: Any,
    target_value: str,
    candidates: Optional[list[str]] = None,
    timeout_ms: int = 5_000,
) -> bool:
    """Selects an option from an Ant Design / rc-select dropdown component:
    1. Dispatches mousedown event on the parent .ant-select-selector wrapper instead of direct click on obscured <input>.
    2. Waits for detached portal container .ant-select-dropdown:not(.ant-select-dropdown-hidden) to appear at root of document.body.
    3. Executes a case-insensitive text match on the target option.
    4. Clicks the matched option.
    5. Verifies the dropdown hides.
    """
    page = getattr(page_or_tab, "page", page_or_tab)

    # 1. Dispatch mousedown event on parent .ant-select-selector wrapper
    opened = False
    try:
        opened = bool(locator.evaluate("""el => {
            const wrapper = el.closest('.ant-select-selector, [class*=select-selector], .ant-select, [role=combobox]') || el.parentElement || el;
            wrapper.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
            wrapper.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
            wrapper.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
            const inp = wrapper.querySelector('input') || el;
            if (inp && inp.focus) inp.focus();
            return true;
        }"""))
    except Exception as exc:
        logger.debug("Could not dispatch mousedown on ant-select-selector wrapper: %s", exc)
        opened = False

    if not opened:
        try:
            wrapper = locator.locator(
                "xpath=ancestor-or-self::*[contains(@class, 'ant-select-selector') or contains(@class, 'ant-select')][1]"
            )
            if wrapper.count():
                wrapper.first.click(timeout=2_000)
            else:
                locator.click(timeout=2_000)
        except Exception:
            pass

    # 2. Wait for detached portal container .ant-select-dropdown:not(.ant-select-dropdown-hidden)
    portal = page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden)")
    try:
        portal.wait_for(state="visible", timeout=timeout_ms)
    except Exception:
        # Retry with ArrowDown if portal not yet visible
        try:
            locator.press("ArrowDown")
            portal.wait_for(state="visible", timeout=2_000)
        except Exception:
            logger.debug("Ant dropdown portal did not appear within timeout")
            return False

    # 3. Query options and execute case-insensitive text match
    search_targets = [target_value]
    if candidates:
        for c in candidates:
            if c not in search_targets:
                search_targets.append(c)

    option_locators = portal.locator(
        "[class*='ant-select-item-option'], [role='option'], .ant-select-dropdown-menu-item"
    )
    count = option_locators.count()
    if count == 0:
        logger.debug("No options found inside Ant dropdown portal")
        return False

    texts: list[str] = []
    for i in range(count):
        try:
            t = (option_locators.nth(i).inner_text(timeout=500) or "").strip()
            texts.append(t)
        except Exception:
            texts.append("")

    # Look for exact case-insensitive match first
    matched_idx: Optional[int] = None
    for target in search_targets:
        target_lower = target.strip().lower()
        for idx, text in enumerate(texts):
            if text.lower() == target_lower:
                matched_idx = idx
                break
        if matched_idx is not None:
            break

    # If no exact match, look for contains match
    if matched_idx is None:
        for target in search_targets:
            target_lower = target.strip().lower()
            if not target_lower:
                continue
            for idx, text in enumerate(texts):
                t_lower = text.lower()
                if target_lower in t_lower or t_lower in target_lower:
                    matched_idx = idx
                    break
            if matched_idx is not None:
                break

    if matched_idx is None:
        logger.info("Ant dropdown: target %r not found among options: %s", target_value, texts[:10])
        return False

    # 4. Click the matched option
    chosen = option_locators.nth(matched_idx)
    click_success = click_resiliently(chosen, timeout_ms=3_000)
    if not click_success:
        return False

    # 5. Verify the dropdown hides
    try:
        portal.wait_for(state="hidden", timeout=3_000)
        return True
    except Exception:
        # Check if detached or class updated
        try:
            is_hidden = portal.evaluate("""el => {
                return !el || el.classList.contains('ant-select-dropdown-hidden') ||
                       window.getComputedStyle(el).display === 'none' ||
                       window.getComputedStyle(el).visibility === 'hidden';
            }""")
            return bool(is_hidden)
        except Exception:
            return True


def commit_draft_cards(page: Page, timeout_ms: int = 4_000) -> int:
    """Pre-navigation sweep: scans DOM for inline resume-parsed experience or education
    cards stuck in active 'Edit' or 'Draft' modes. Autonomously locates and clicks all
    internal card buttons matching 'Update', 'Save Entry', 'Done', etc., and waits
    for network and DOM settlement before evaluating page progression.

    Returns the count of cards committed.
    """
    committed = 0

    target_selectors = [
        "button:has-text('Save Entry')",
        "button:has-text('Update')",
        "button:has-text('Done')",
        "button:has-text('Save')",
        "button:has-text('Apply Changes')",
    ]

    for selector in target_selectors:
        buttons = page.locator(selector)
        count = min(buttons.count(), 10)
        for i in range(count):
            btn = buttons.nth(i)
            try:
                if not btn.is_visible() or not btn.is_enabled():
                    continue
                label = (btn.inner_text(timeout=1_000) or "").strip()
                if not CARD_COMMIT_TEXT_PATTERN.match(label):
                    continue
                # CRITICAL safety check: never click anything that reads as a final submit
                if safety.is_submit_label(label):
                    continue

                # Ensure button is internal to a card or form section with inputs
                is_internal = btn.evaluate("""el => {
                    const card = el.closest(
                        '[data-automation-id*=\"experience\" i], [data-automation-id*=\"education\" i], ' +
                        '[class*=\"card\" i], [class*=\"entry\" i], [class*=\"item\" i], ' +
                        '[class*=\"form-section\" i], fieldset, form, div[role=\"group\"], ' +
                        '.ant-modal-content, [role=\"dialog\"]'
                    );
                    const scope = card || el.parentElement;
                    const hasInputs = scope && scope.querySelectorAll('input, select, textarea').length > 0;
                    return Boolean(hasInputs);
                }""")

                if not is_internal:
                    continue

                logger.info("Committing active draft card with button %r", label)
                click_resiliently(btn, timeout_ms=timeout_ms)
                committed += 1

                # Wait for network settlement
                try:
                    page.wait_for_load_state("networkidle", timeout=3_000)
                except Exception:
                    pass
                page.wait_for_timeout(800)
            except Exception as exc:
                logger.debug("Failed while inspecting/clicking card button: %s", exc)

    return committed
