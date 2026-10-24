"""
iCIMS portal adapter (icims.com and employer-embedded iCIMS portals).

iCIMS applications are almost always hosted inside an iframe with the id
`#icims_content_iframe` embedded inside the employer's corporate site wrapper.
The outer site often has its own search bars, navigation links, and menus;
all candidate interaction must be scoped to the iCIMS iframe.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from .base import SiteAdapter

logger = logging.getLogger("sites.icims")


class IcimsAdapter(SiteAdapter):
    name = "icims"
    hosts = ("icims.com", "icims.eu")
    confirmation_phrases = (
        "thank you for submitting your application",
        "your application has been submitted",
        "your profile has been successfully submitted",
        "application submitted",
        "thank you for your interest",
    )
    portal_list_patterns = ("/jobs/my-profile", "/jobs/dashboard", "/dashboard")

    @classmethod
    def matches(cls, url: str) -> bool:
        lower = (url or "").lower()
        return any(h in lower for h in cls.hosts) or "icims_content_iframe" in lower

    def _main_document_is_form(self, page) -> bool:
        """True when the top document itself holds the application form.

        open_embedded_form() treats a document with more than three visible
        inputs as the form and stays on it; this mirrors that rule so a
        stray same-host frame (a reloaded login frame, a tracking iframe)
        cannot pull work out of a form that is already the page.
        """
        try:
            count = page.locator(
                "input:not([type=hidden]):not([type=search]):visible, "
                "select:visible, textarea:visible").count()
            return count > 3
        except Exception:
            return False

    def find_application_frame(self, page):
        """Locates and returns the iCIMS content iframe to prevent outer corporate
        header/search inputs from hijacking application automation.

        The named content iframe is always authoritative. A same-host frame
        matched by URL alone is not returned when the main document already
        holds the application form -- in that case the page itself is the form
        and descending into the frame is a regression (test_finding_the_way:
        a page that is already the form stays put).
        """
        try:
            frames = getattr(page, "frames", ()) or ()
            # 1. Exact name match
            for frame in frames:
                if frame.name == "icims_content_iframe":
                    return frame
            # 2. Frame URL match -- but not when the main document is the form
            if not self._main_document_is_form(page):
                for frame in frames:
                    frame_url = (getattr(frame, "url", "") or "").lower()
                    if "icims.com" in frame_url and frame != getattr(page, "main_frame", None):
                        return frame
            # 3. Element locator handle check
            handle = page.query_selector("iframe#icims_content_iframe, iframe[name='icims_content_iframe']")
            if handle:
                cf = handle.content_frame()
                if cf:
                    return cf
        except Exception as exc:
            logger.debug("iCIMS frame lookup error: %s", exc)
        return None

    def _scope(self, page):
        frame = self.find_application_frame(page)
        return frame if frame is not None else page

    def attachment_is_empty(self, page, kind: str) -> Optional[bool]:
        """Detect whether a resume has been uploaded inside the iCIMS form."""
        scope = self._scope(page)
        try:
            # iCIMS shows uploaded document name or 'Remove' link once attached
            uploaded = scope.locator(".iCIMS_Job_Attachment_Name, .iCIMS_attachment_name, a:has-text('Remove')")
            if uploaded.count() > 0:
                return False
        except Exception as _swallowed:
            logger.debug("attachment_is_empty: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)
        return None

    def candidate_account_state(self, page) -> str:
        """Classifies iCIMS Candidate Profile / account screens."""
        scope = self._scope(page)
        try:
            body = scope.locator("body").inner_text(timeout=3_000) or ""
            password_count = scope.locator("input[type=password]:visible").count()
        except Exception:
            return ""
        if password_count >= 2 or (password_count >= 1 and re.search(r"\b(create|build|submit)\s+(your\s+)?profile\b", body, re.IGNORECASE)):
            return "registration"
        if password_count == 1 and re.search(r"\b(log\s*in|returning candidate|sign\s*in)\b", body, re.IGNORECASE):
            return "sign_in"
        return ""

    def create_account_extras(self, assistant, page) -> None:
        """Fills iCIMS Login box if not already filled by email typing."""
        scope = self._scope(page)
        try:
            profile = getattr(assistant, "_profile", None)
            email = getattr(profile, "email", "")
            if email:
                login_box = scope.locator("input#login, input[name='login'], input[id*='login' i], input[name*='login' i]").first
                if login_box.count() and login_box.is_visible() and not (login_box.input_value() or "").strip():
                    from interaction import type_text
                    type_text(login_box, email)
        except Exception as _swallowed:
            logger.debug("create_account_extras: %s", str(_swallowed).splitlines()[0][:100] if str(_swallowed) else type(_swallowed).__name__)


