"""
Eightfold career sites (jobs.cbts.com and others, usually on the employer's
own domain).

Learned from a live CBTS application on 2026-09-15:
  * after submitting, the site shows no thank-you page -- it jumps to the
    candidate's applications list, where the job appears with "Applied on"
  * a first sign-in (including "Sign in using Google") is followed by a
    "Create your profile" window that must be saved before the form appears
  * a "Data Privacy Agreement" pop-up covers the form until it is accepted
"""

from __future__ import annotations

from .base import SiteAdapter


class EightfoldAdapter(SiteAdapter):
    name = "eightfold"
    hosts = ("eightfold.ai",)
    confirmation_phrases = ("application received", "we have received your application")
    portal_list_patterns = ("/careers/applications", "/applications", "/dashboard")

    @classmethod
    def matches(cls, url: str) -> bool:
        low = (url or "").lower()
        # Employers host it on their own domain; the path is the giveaway.
        return super().matches(url) or "/careers/job/" in low or "/careers/apply" in low
