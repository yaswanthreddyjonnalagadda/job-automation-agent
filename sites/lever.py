"""
Lever-hosted applications (jobs.lever.co).

Learned from a live Quantum Health application on 2026-09-15:
  * the posting page needs JavaScript, so the description comes from Lever's
    public API (see job_sources.fetch_lever_job)
  * the form itself is ordinary HTML handled by the generic logic
  * Lever pages carry a reCAPTCHA that can sit over the Submit button; the
    agent reports it and waits for the user rather than touching it
"""

from __future__ import annotations

from .base import SiteAdapter


class LeverAdapter(SiteAdapter):
    name = "lever"
    hosts = ("lever.co",)
    confirmation_phrases = ("thank you for applying", "application received")
    portal_list_patterns = ()
