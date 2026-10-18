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

    @classmethod
    def matches(cls, url: str) -> bool:
        lower = (url or "").lower()
        return any(h in lower for h in cls.hosts) or "gh_jid=" in lower

    @staticmethod
    def _scope(page):
        try:
            for frame in getattr(page, "frames", ()) or ():
                if "greenhouse.io" in (getattr(frame, "url", "") or "").lower():
                    return frame
        except Exception:
            pass
        return page

    def attachment_is_empty(self, page, kind: str):
        """Greenhouse shows the chosen file's name beside the upload button."""
        words = "cover_letter" if kind == "cover_letter" else "resume"
        scope = self._scope(page)
        try:
            block = scope.locator(f"#{words}_fieldset, [id*='{words}' i]").first
            if not block.count():
                return None
            return "attached" not in (block.inner_text() or "").lower() and not block.locator(
                "[class*=filename i], .attached-file"
            ).count()
        except Exception:
            return None

