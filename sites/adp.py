"""ADP career-site DOM rules; application submission is still guarded."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, parse_qs
from .base import SiteAdapter
import logging

logger = logging.getLogger('browser_automation')

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

    def form_loading(self, snapshot: str) -> bool:
        current = re.search(r'- button "Step Status current ([^"\n]+)"', snapshot or '')
        if not current or not re.search(r'- button "Next"[^\n]*\[disabled\]', snapshot):
            return False
        # ADP renders an empty current-step region and disabled Next before
        # fetching the actual fields. Its surrounding navigation is ready.
        return bool(re.search(r'^\s*- generic "' + re.escape(current.group(1))
                              + r'"(?: \[[^\]]*\])*\s*$', snapshot, re.MULTILINE))

    def expand_form_sections(self, assistant, page) -> bool:
        headers = page.get_by_role('button', name=re.compile(
            r'^(?:Employer|Education)\s+[1-9]\d*\s+Show content$', re.I))
        return bool(headers.count() and assistant._click_resiliently(headers.first, timeout_ms=3_000))

    def entry_map(self, snapshot: str):
        import repeated_entries
        pattern = r'(?m)^(\s*)- (?:button "(Employer|Education) ([1-9]\d*) (?:Show|Hide) content"|region "(Employer|Education) ([1-9]\d*)")([^\n]*)$'
        if not re.search(pattern, snapshot):
            return None
        def heading(match):
            kind = 'Work Experience' if (match[2] or match[4]) == 'Employer' else 'Education'
            return f'{match[1]}- group "{kind} {match[3] or match[5]}"{match[6]}'
        return repeated_entries.entry_map(re.sub(pattern, heading, snapshot))

    def overlay_selector(self) -> str:
        """ADP floating modal panes and privacy dialogs."""
        return "sdf-focus-pane:visible, cx-privacy-policy:visible"

    def upload_attachment(self, assistant, page, kind: str, file_path: Path):
        label = 'Resume' if kind == 'resume' else 'Cover Letter' if kind == 'cover_letter' else None
        if label is None:
            return None
        trigger = page.get_by_role('button', name=re.compile(r'^upload\s+' + re.escape(label) + r'$', re.I))
        if trigger.count() != 1 or not trigger.evaluate("el => el.matches('sdf-button')"):
            return None
        try:
            if not assistant._click_resiliently(trigger, timeout_ms=4_000):
                return False
            uploader = page.locator('sdf-file-uploader')
            uploader.first.wait_for(state='attached', timeout=5_000)
            if uploader.count() != 1:
                return False
            inputs = uploader.locator('input[type="file"]')
            if inputs.count() != 1:
                return False
            inputs.set_input_files(str(file_path), timeout=5_000)
            save = page.get_by_role('button', name='Save', exact=True)
            save.wait_for(state='visible', timeout=5_000)
            if not assistant._click_resiliently(save, timeout_ms=4_000):
                return False
            save.wait_for(state='hidden', timeout=8_000)
            return True  # The caller must still verify the attachment receipt.
        except Exception as exc:
            logger.debug('ADP document dialog upload failed: %s', type(exc).__name__)
            return False

    def choose_dropdown(self, page, control, value: str, *, pick, click):
        """ADP's shadow select searches through its own async filter input."""
        handle = None
        try:
            handle = control.evaluate_handle("""el => {
                const host = el.getRootNode().host;
                return host && host.matches('sdf-select-simple') ? host : null;
            }""")
            host = handle.as_element()
            if host is None:
                return None
            if control.get_attribute('aria-expanded') != 'true' and not click(control, timeout_ms=4_000):
                return False
            search = host.query_selector('input[part="filter-input"]')
            filtered = False
            deadline = time.monotonic() + 6.0
            while time.monotonic() < deadline:
                rows = [row for row in host.query_selector_all('[role="option"]') if row.is_visible()]
                labels = [' '.join(row.inner_text().split()) for row in rows]
                index = pick(labels) if labels else None
                if index is not None:
                    if not click(rows[index], timeout_ms=4_000):
                        return False
                    page.wait_for_timeout(250)
                    return pick([' '.join(control.inner_text(timeout=1_500).split())]) == 0
                # Match already offered aliases (Mobile -> Cell) before a
                # literal text filter could remove them. Async lists with no
                # rows need their own input to fetch the offered choices.
                if not filtered and search is not None and search.is_visible():
                    search.fill(value, timeout=2_000)
                    filtered = True
                page.wait_for_timeout(250)
            return False
        except Exception as exc:
            logger.debug('ADP dropdown could not commit: %s', type(exc).__name__)
            return False if handle is not None and handle.as_element() is not None else None
        finally:
            if handle is not None:
                handle.dispose()

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
        except Exception as _swallowed:
            logger.debug("open_picker: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)
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
        except Exception as _swallowed:
            logger.debug("select_option: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)
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
        except Exception as _swallowed:
            logger.debug("commit_card: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)
        return False

