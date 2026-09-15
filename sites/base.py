"""
Site adapters: everything that is true of ONE application platform.

browser_automation.py holds the reusable browser and form logic and knows
nothing about a particular employer. Whenever it needs a detail that differs
per platform -- what the final button is called, how an upload starts, how a
dropdown opens -- it asks the adapter returned by `sites.adapter_for(url)`.

A hook returning None means "not my platform's problem, use the generic path".
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


class SiteAdapter:
    """Generic behaviour. Platform adapters override only what differs."""

    name = "generic"
    # Hosts this adapter recognises (substring match on the URL's host).
    hosts: tuple[str, ...] = ()
    # Extra wording for a page that confirms an application was received.
    confirmation_phrases: tuple[str, ...] = ()
    # URL fragments of the candidate's own applications list on this platform.
    portal_list_patterns: tuple[str, ...] = ("/applications", "my-applications", "/dashboard")
    # Values a picker shows while it holds no answer.
    placeholder_values: tuple[str, ...] = ("no selection", "select", "select one", "please select", "choose one")

    @classmethod
    def matches(cls, url: str) -> bool:
        host = (url or "").lower()
        return any(h in host for h in cls.hosts)

    # -- uploads ----------------------------------------------------------
    def attachment_is_empty(self, page, kind: str) -> Optional[bool]:
        """True/False when this platform can say whether its Resume/Cover
        Letter slot is empty; None when it has no such concept."""
        return None

    def upload_attachment(self, assistant, page, kind: str, file_path: Path) -> Optional[bool]:
        """Platform-specific upload (a tile, an icon, a dialog). None = not handled."""
        return None

    # -- pickers and widgets ----------------------------------------------
    def open_picker(self, page, control: dict) -> bool:
        """Open a custom dropdown so its options render. False = not handled."""
        return False

    def set_date(self, assistant, page, label_pattern: str, value: str) -> Optional[bool]:
        """Fill a platform's date widget. None = not handled."""
        return None

    # -- account creation --------------------------------------------------
    def create_account_extras(self, assistant, page) -> None:
        """Platform-specific required bits of a Create Account form."""
        return None

    # -- misc ---------------------------------------------------------------
    def overlay_selector(self) -> str:
        """Extra selectors for this platform's modal/overlay containers."""
        return ""


def merge_placeholders(adapter: SiteAdapter) -> re.Pattern:
    """A regex matching any 'nothing chosen yet' value for this platform."""
    words = "|".join(re.escape(p) for p in adapter.placeholder_values)
    return re.compile(rf"^\s*(-+\s*)?({words})(\s*-+)?\s*\.*$", re.IGNORECASE)
