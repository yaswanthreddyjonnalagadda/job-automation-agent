"""
SAP SuccessFactors career sites (jobs.igt.com and many other employers).

Everything here was learned from a live IGT application on 2026-09-15:
  * attachments upload through a "+" icon that opens a dialog, not a file input
  * dropdowns are "rcmpaginatedselect" pickers whose options load from the
    server only once the arrow button opens them, and whose option list is
    named by aria-owns rather than aria-controls
  * dates are SAP UI5 web components whose input lives in a shadow root
  * the final button is labelled "Apply", not "Submit"
  * Create Account also requires a profile-visibility choice
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from .base import SiteAdapter

logger = logging.getLogger(__name__)


class SuccessFactorsAdapter(SiteAdapter):
    name = "successfactors"
    hosts = ("successfactors.eu", "successfactors.com", "sapsf.eu", "sapsf.com", "jobs2web.com")
    confirmation_phrases = ("application received", "thanks for applying", "your journey with")
    portal_list_patterns = ("/portalcareer", "/applications", "/careers/applications")

    _ATTACHMENT_FIELD = ".attachmentField"
    _ATTACH_ICON = "[id$='_attachIcon'], .addAttachments"
    _ATTACHED_LABEL = "[id$='_attachDownloadLabel']"

    @staticmethod
    def _kind_words(kind: str) -> str:
        return r"cover letter" if kind == "cover_letter" else r"resume|\bcv\b"

    def _fields(self, page, kind: str):
        return page.locator(self._ATTACHMENT_FIELD).filter(
            has=page.locator("label", has_text=re.compile(self._kind_words(kind), re.IGNORECASE))
        )

    def attachment_is_empty(self, page, kind: str) -> Optional[bool]:
        fields = self._fields(page, kind)
        for i in range(min(fields.count(), 3)):
            label = fields.nth(i).locator(self._ATTACHED_LABEL).first
            try:
                if label.count():
                    return not label.is_visible()
            except Exception:
                continue
        return None

    def upload_attachment(self, assistant, page, kind: str, file_path: Path) -> Optional[bool]:
        """Click the field's "+" icon and answer whatever it opens: the system
        file picker, or a dialog with its own file input or Browse button."""
        fields = self._fields(page, kind)
        for i in range(min(fields.count(), 3)):
            icon = fields.nth(i).locator(self._ATTACH_ICON).first
            if not icon.count():
                continue
            try:
                icon.scroll_into_view_if_needed(timeout=3_000)
                try:
                    with page.expect_file_chooser(timeout=5_000) as chooser:
                        icon.click(timeout=5_000)
                    chooser.value.set_files(str(file_path))
                except Exception:
                    if not assistant.upload_in_dialog(page, file_path):
                        continue
                page.wait_for_timeout(5_000)
                # Verify the attachment actually shows as present (Phase 0-B4 closure) --
                # a non-throwing chooser/dialog completion is not, by itself, evidence it
                # landed. Reuses the existing attachment_is_empty() evidence this adapter
                # already exposes as a pre-check, now also as a post-check, rather than
                # inventing a new one.
                if self.attachment_is_empty(page, kind) is False:
                    logger.info("Uploaded %s through the %s attachment icon", Path(file_path).name, kind)
                    return True
                logger.warning("SuccessFactors attachment does not show as attached after the attempt (kind=%s)", kind)
            except Exception as exc:
                logger.warning("SuccessFactors attachment upload failed: %s", str(exc).splitlines()[0][:120])
        return None

    def open_picker(self, page, control: dict) -> bool:
        """These pickers fetch their options when the arrow button opens them."""
        control_id = control.get("id") or ""
        if not control_id.endswith("_input"):
            return False
        arrow = page.locator(f"[id={control_id.replace('_input', '_selectButton')!r}]".replace("'", '"'))
        if not arrow.count():
            return False
        try:
            arrow.first.scroll_into_view_if_needed(timeout=2_000)
            arrow.first.click(timeout=4_000)
            listbox = control.get("listbox") or ""
            if listbox:
                page.locator(f'[id="{listbox}"] li').first.wait_for(state="visible", timeout=6_000)
            return True
        except Exception:
            return False

    def choose_location(self, page, field, wanted: str, same) -> bool:
        """Commit a location row through SAP's own picker, never by typing alone."""
        control = {'id': field.get_attribute('id'),
                   'listbox': field.get_attribute('aria-owns') or ''}
        if not control['listbox'] or not self.open_picker(page, control):
            return False
        rows = page.locator(f'[id="{control["listbox"]}"] li:visible')
        try:
            labels = rows.all_inner_texts()
            matches = [i for i, label in enumerate(labels) if same(label.strip(), wanted)]
            if len(matches) != 1:
                page.keyboard.press('Escape')
                return False
            rows.nth(matches[0]).click(timeout=4_000)
            page.wait_for_timeout(300)
            return same(field.input_value(), wanted)
        except Exception:
            return False

    def set_date(self, assistant, page, label_pattern: str, value: str) -> Optional[bool]:
        """SAP UI5 date picker: the real input is inside the component."""
        pickers = page.locator("[ui5-date-picker], ui5-date-picker, [data-testid=datePicker]")
        for i in range(min(pickers.count(), 10)):
            picker = pickers.nth(i)
            try:
                name = picker.get_attribute("accessible-name") or picker.get_attribute("title") or ""
                if not re.search(label_pattern, name, re.IGNORECASE):
                    continue
                inner = picker.locator("input").first
                inner.click(timeout=5_000)
                inner.fill("")
                inner.press_sequentially(value, delay=60)
                inner.press("Tab")  # never Enter: it can trigger a form's default button
                picker.evaluate(
                    "(e, v) => { e.value = v; for (const t of ['input', 'change'])"
                    " e.dispatchEvent(new CustomEvent(t, {bubbles: true, composed: true,"
                    " detail: {value: v, valid: true}})); }",
                    value,
                )
                # Verify the widget's own input actually committed something (Phase 0-B4
                # closure) -- the fill+dispatch sequence not throwing is not, by itself,
                # evidence it stuck. The widget may reformat the typed value, so this
                # checks for digit overlap (day/month/year) rather than an exact string
                # match, and treats a verification read failure as NOT verified.
                try:
                    shown = (inner.input_value(timeout=1_500) or "").strip()
                except Exception:
                    return False
                if not shown:
                    return False
                digits_expected, digits_shown = re.sub(r"\D", "", value), re.sub(r"\D", "", shown)
                if digits_expected and digits_shown and digits_expected not in digits_shown \
                        and digits_shown not in digits_expected:
                    return False
                return True
            except Exception as exc:
                logger.warning("Could not set the date widget: %s", str(exc).splitlines()[0][:100])
                return False
        return None

    def create_account_extras(self, assistant, page) -> None:
        """"Make My Profile Visible to" is required; the widest choice keeps the
        account considered for every job."""
        radios = page.locator("input[type=radio]")
        for i in range(min(radios.count(), 20)):
            radio = radios.nth(i)
            try:
                if not radio.is_visible():
                    continue
                label = (radio.evaluate(
                    "e => (e.id && document.querySelector(`label[for=\"${CSS.escape(e.id)}\"]`)?.innerText)"
                    " || e.closest('label')?.innerText || e.parentElement?.innerText || ''"
                ) or "").lower()
                group = radio.get_attribute("name") or ""
                chosen = group and page.locator(f'input[type=radio][name="{group}"]:checked').count()
                if not chosen and re.search(r"all recruiters|all jobs", label):
                    radio.check(force=True, timeout=3_000)
                    logger.info("ACCOUNT: profile visible to %r", label.strip()[:60])
            except Exception:
                continue

    def overlay_selector(self) -> str:
        return ".sfoverlaycon"
