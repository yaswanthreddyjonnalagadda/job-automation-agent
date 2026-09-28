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

