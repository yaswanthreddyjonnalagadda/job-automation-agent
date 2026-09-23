"""
core/interceptors.py - MutationObserver and overlay detector for automatic modal and cookie banner dismissal.
Detects backdrop overlays, consent prompts, and floating dialogs and clears viewport blockages.
"""

from __future__ import annotations

import logging
from playwright.async_api import Page

logger = logging.getLogger("agent_v2.interceptors")

OVERLAY_DISMISSAL_SCRIPT = """
(() => {
    if (window.__overlay_observer_installed) return;
    window.__overlay_observer_installed = true;

    const DISMISS_BUTTON_TEXTS = [
        'accept', 'accept all', 'i agree', 'agree', 'got it', 
        'close', 'dismiss', 'continue without an account', 'ok',
        'allow all', 'allow selection', 'acknowledge'
    ];

    function tryDismissOverlay(node) {
        if (!node || node.nodeType !== Node.ELEMENT_NODE) return;

        // Check if element is a modal, banner, or dialog
        const isDialog = node.getAttribute('role') === 'dialog' || 
                         node.getAttribute('aria-modal') === 'true' ||
                         (node.className && typeof node.className === 'string' && 
                          node.className.match(/cookie|consent|modal|overlay|banner|popup/i));

        if (!isDialog && !node.querySelector('[role="dialog"], [class*="cookie" i], [class*="modal" i]')) {
            return;
        }

        // Search for dismiss/accept buttons within this scope
        const buttons = node.querySelectorAll('button, a, [role="button"]');
        for (const btn of buttons) {
            const txt = (btn.innerText || btn.getAttribute('aria-label') || '').trim().toLowerCase();
            if (DISMISS_BUTTON_TEXTS.includes(txt)) {
                try {
                    btn.click();
                    console.log('[agent_v2] Automatically dismissed overlay button:', txt);
                    return true;
                } catch (e) {}
            }
        }
    }

    // Run once on existing body
    tryDismissOverlay(document.body);

    // Watch for dynamically appended dialogs or backdrops
    const observer = new MutationObserver((mutations) => {
        for (const m of mutations) {
            for (const added of m.addedNodes) {
                tryDismissOverlay(added);
            }
        }
    });

    observer.observe(document.body, { childList: true, subtree: true });
})();
"""


class OverlayInterceptor:
    """Detects and clears viewport-blocking modal dialogs and cookie banners."""

    COMMON_CONSENT_SELECTORS = [
        "button:has-text('Accept')",
        "button:has-text('Accept All')",
        "button:has-text('I Agree')",
        "button:has-text('Got it')",
        "button:has-text('Close')",
        "button:has-text('Dismiss')",
        "button:has-text('Allow All')",
        "[aria-label='Close']",
        ".cookie-banner button",
        ".onetrust-close-btn-handler",
    ]

    @classmethod
    async def inject_observer(cls, page: Page) -> None:
        """Inject in-page MutationObserver to dismiss overlays as soon as they appear."""
        try:
            await page.evaluate(OVERLAY_DISMISSAL_SCRIPT)
        except Exception as exc:
            logger.debug("Failed to inject overlay MutationObserver: %s", exc)

    @classmethod
    async def dismiss_active_overlays(cls, page: Page) -> bool:
        """Explicit pass scanning for and clicking visible dismiss buttons."""
        dismissed_any = False
        for selector in cls.COMMON_CONSENT_SELECTORS:
            try:
                locator = page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    await locator.click(timeout=1_500)
                    logger.info("Dismissed overlay via selector: %s", selector)
                    dismissed_any = True
                    break
            except Exception:
                continue
        return dismissed_any

