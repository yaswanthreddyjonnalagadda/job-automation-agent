"""ADP career-site DOM rules; application submission is still guarded."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, parse_qs
from .base import SiteAdapter

# Evaluated at click time, not cached from a page-plan label. ADP's posting
# opener is outside a form and opens the account/application workflow.
POSTING_ENTRY_JS = r"""element => {
  if (location.hostname !== 'myjobs.adp.com'
      || !/^\/[^/]+(?:\/cx)+\/job-details\/?$/.test(location.pathname)
      || !new URL(location.href).searchParams.get('reqId')) return false;
  if (!element.matches('sdf-button[role="button"][type="button"]')
      || !element.closest('cx-job-details-page .cx-apply-row')
      || element.closest('form') || element.hasAttribute('form')) return false;
  const labels = [element.getAttribute('aria-label'), element.textContent]
    .map(value => (value || '').trim()).filter(Boolean);
  if (!labels.length || !labels.every(value => /^apply$/i.test(value))) return false;
  const visible = element => element.getClientRects().length > 0;
  return !Array.from(document.querySelectorAll(
    'form, [role="dialog"], input:not([type="hidden"]), textarea, select'
  )).some(visible);
}"""


def is_adp_applied_posting(page_or_tab: Any) -> bool:
    """An exact live Applied control for the current ADP requisition.

    Cached snapshots/body prose cannot establish a terminal outcome. Traverse
    open shadow roots because ADP uses web components for the posting action.
    """
    page = getattr(page_or_tab, "page", page_or_tab)
    try:
        url = urlsplit(getattr(page, "url", "") or "")
        if (url.scheme != "https" or url.hostname != "myjobs.adp.com"
                or not re.fullmatch(r"/[^/]+(?:/cx)+/job-details/?", url.path)
                or not parse_qs(url.query).get("reqId")):
            return False
        return page.evaluate(r"""() => {
            if (location.hostname !== 'myjobs.adp.com'
                || !/^\/[^/]+(?:\/cx)+\/job-details\/?$/.test(location.pathname)
                || !new URL(location.href).searchParams.get('reqId')) return false;
            const all = [];
            const walk = root => {
                for (const el of root.querySelectorAll('*')) {
                    all.push(el);
                    if (el.shadowRoot) walk(el.shadowRoot);
                }
            };
            walk(document);
            const parent = el => el.parentElement || el.getRootNode().host;
            const visible = el => {
                if (!el.getClientRects().length) return false;
                for (let node = el; node; node = parent(node)) {
                    const style = getComputedStyle(node);
                    if (node.hidden || style.display === 'none'
                        || ['hidden', 'collapse'].includes(style.visibility)
                        || style.opacity === '0') return false;
                }
                return true;
            };
            const inActionRow = el => {
                for (let node = el; node; node = parent(node)) {
                    if (node.matches('.cx-apply-row')) return true;
                }
                return false;
            };
            if (all.some(el => visible(el) && el.matches(
                'input:not([type="hidden"]), textarea, select, form, [role="dialog"], [role="textbox"], [role="combobox"]'
            ))) return false;
            return all.some(el => {
                if (!visible(el) || !inActionRow(el)
                    || !el.matches('sdf-button, button, [role="button"]')) return false;
                const labels = [el.getAttribute('aria-label'), el.textContent]
                    .map(value => (value || '').trim()).filter(Boolean);
                return labels.length > 0 && labels.every(value => /^applied$/i.test(value));
            });
        }""") is True
    except Exception:
        return False


class AdpAdapter(SiteAdapter):
    """Adapter for ADP WorkforceNow / NextGen Career Experience (myjobs.adp.com)."""

    name = "adp"
    hosts = ("myjobs.adp.com", "adp.com")
    confirmation_phrases = (
        "thank you for applying",
        "application submitted",
    )

    @classmethod
    def matches(cls, url: str) -> bool:
        try:
            host = (urlsplit(url).hostname or "").lower()
            return host == "adp.com" or host.endswith(".adp.com")
        except ValueError:
            return False

    def submission_confirmed(self, page) -> bool:
        return is_adp_applied_posting(page)

    def overlay_selector(self) -> str:
        """ADP floating modal panes and privacy dialogs."""
        return "sdf-focus-pane:visible, cx-privacy-policy:visible"

    def open_picker(self, page, control: dict) -> bool:
        """Opens an ADP <sdf-select-simple> or custom dropdown."""
        try:
            ref = control.get("ref", "")
            loc = page.locator(f"[aria-ref='{ref}']") if ref else None
            if not loc or not loc.count():
                loc = page.locator(control.get("selector", "sdf-select-simple:visible")).first
            if loc.count():
                loc.scroll_into_view_if_needed(timeout=2000)
                loc.click(timeout=2000)
                return True
        except Exception:
            pass
        return False

    def select_option(self, page, selector: str, value: str) -> bool:
        """Selects a matching <sdf-select-option> inside ADP custom select."""
        try:
            options = page.locator("sdf-select-option:visible")
            for i in range(options.count()):
                opt = options.nth(i)
                text = (opt.inner_text(timeout=500) or "").strip()
                if text.lower() == value.strip().lower() or value.strip().lower() in text.lower():
                    opt.click(timeout=2000)
                    return True
        except Exception:
            pass
        return False

    def commit_card(self, page, card_loc) -> bool:
        """Clicks Save or Done on an editable <sdf-card> inline card."""
        try:
            btn = card_loc.locator("sdf-button:visible, button:visible").filter(
                has_text=re.compile(r"^\s*(?:save|done|apply|confirm)\s*$", re.I)
            ).first
            if btn.count():
                btn.click(timeout=3000)
                return True
        except Exception:
            pass
        return False

