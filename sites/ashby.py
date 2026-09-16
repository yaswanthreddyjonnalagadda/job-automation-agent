"""
Ashby-hosted applications (jobs.ashbyhq.com).

Learned from a live Collective application on 2026-09-15:
  * field ids are UUIDs, so selectors must be attribute-based (handled
    generically now), and the name box is "_systemfield_name"
  * Yes/No questions are a row of aria-pressed buttons, not radios: clicking a
    pressed one clears it
  * the required-field asterisk is drawn with CSS, not text
  * the site's "Autofill from resume" box exists but filled nothing
"""

from __future__ import annotations

import logging
import re

from .base import SiteAdapter

logger = logging.getLogger("browser_automation")


class AshbyAdapter(SiteAdapter):
    name = "ashby"
    hosts = ("ashbyhq.com",)
    confirmation_phrases = ("application was successfully submitted", "thanks for applying")
    portal_list_patterns = ("/applications",)

    def answer_toggle(self, assistant, page, question: str, choice: str) -> bool:
        """Ashby's Yes/No pairs: click only when the wanted choice is not
        already pressed, because clicking a pressed button unselects it."""
        label = assistant._question_label(page, question)
        if label is None:
            return False
        button = label.locator(
            f"xpath=following::button[normalize-space(.)={choice!r}][1]".replace("'", '"')
        ).first
        if not button.count():
            return False
        if button.get_attribute("aria-pressed") == "true":
            return True
        return assistant._click_resiliently(button, timeout_ms=5_000)

    def required_marker_is_css(self) -> bool:
        return True
