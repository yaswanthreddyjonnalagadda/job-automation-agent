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

    def find_application_frame(self, page):
        """Locates and returns the iCIMS content iframe to prevent outer corporate
        header/search inputs from hijacking application automation."""
        try:
            frames = getattr(page, "frames", ()) or ()
            # 1. Exact name match
            for frame in frames:
                if frame.name == "icims_content_iframe":
                    return frame
            # 2. Frame URL match
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
        except Exception:
            pass
        return None

