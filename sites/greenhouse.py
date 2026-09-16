"""
Greenhouse job boards (boards.greenhouse.io, job-boards.greenhouse.io, and
employer pages that embed them).

Greenhouse forms are plain HTML with labelled inputs, so the generic form
handling covers them. What is Greenhouse-specific: where its application form
lives, its file inputs, and its confirmation wording.
"""

from __future__ import annotations

from .base import SiteAdapter


class GreenhouseAdapter(SiteAdapter):
    name = "greenhouse"
    hosts = ("greenhouse.io", "boards.greenhouse.io", "job-boards.greenhouse.io")
    confirmation_phrases = ("thank you for applying", "your application has been submitted")
    portal_list_patterns = ()

    def attachment_is_empty(self, page, kind: str):
        """Greenhouse shows the chosen file's name beside the upload button."""
        words = "cover_letter" if kind == "cover_letter" else "resume"
        try:
            block = page.locator(f"#{words}_fieldset, [id*='{words}' i]").first
            if not block.count():
                return None
            return "attached" not in (block.inner_text() or "").lower() and not block.locator(
                "[class*=filename i], .attached-file"
            ).count()
        except Exception:
            return None
