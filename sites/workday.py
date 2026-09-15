"""
Workday (*.myworkdayjobs.com).

Workday's markup is addressed almost entirely through data-automation-id
attributes. The selectors live here so the reusable form logic doesn't carry
them; the multi-step wizard helpers in browser_automation.py still use them
directly and are the next thing to move.
"""

from __future__ import annotations

from .base import SiteAdapter

# data-automation-id values used by the wizard helpers.
SIGN_IN_SUBMIT = "button[data-automation-id='signInSubmitButton']"
DELETE_FILE = "button[data-automation-id='delete-file']"
PROMPT_OPTION = "[data-automation-id='promptOption']"
ACTIVE_STEP = "[data-automation-id='progressBarActiveStep']"
EMAIL_INPUT = "input[data-automation-id='email']"
# Repeated Work Experience / Education entries ("workExperience-3--jobTitle").
ENTRY_ID_MARKERS = ("workexperience-", "education-", "languages-", "certification-")


class WorkdayAdapter(SiteAdapter):
    name = "workday"
    hosts = ("myworkdayjobs.com", "myworkday.com", "wd1.myworkdayjobs", "wd103.myworkdayjobs")
    confirmation_phrases = ("your application has been submitted", "application submitted")
    portal_list_patterns = ("/candidate_home", "/applications")

    def attachment_is_empty(self, page, kind: str):
        """Workday lists an attached file with a delete button beside it."""
        if kind != "resume":
            return None
        try:
            return page.locator(DELETE_FILE).count() == 0
        except Exception:
            return None
