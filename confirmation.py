"""Whether a page says the application has arrived: one list of words, used everywhere.

Premier Health (Eightfold), 1 October: the owner pressed Submit, the site confirmed it, and the run went on
waiting and checking Gmail -- the status stayed "ready to submit". Three lists decided "is this a confirmation?":
browser_automation's (the wait after the hand-over), page_agent's (the reading agent), and each site adapter's
confirmation_phrases, which the wait never read. Eightfold's own words were only in the last. Now they are one
decision: the general wording here, plus the site's own phrases, minus anything that says there is still more
to do.
"""
from __future__ import annotations

import re
from typing import Iterable

# The general wording across job sites (Workday, Greenhouse, Lever, Ashby, SuccessFactors, iCIMS, Eightfold, ...).
CONFIRMATION_TEXT = re.compile(
    r"thank(?:s| you) for (?:applying|your application|submitting)|"
    r"application (?:has been |was )?(?:received|submitted|sent)|"
    r"your application has been sent|(?:successfully|now) (?:submitted|sent|applied)|"
    r"we(?:'ve| have) received your application|your application is complete|submission received|"
    r"(?<!if )(?<!jobs )(?<!positions )(?:you've|you have) (?:successfully |already )?applied(?! before|\?|\s+to check)",
    re.IGNORECASE)
# The words that say, beyond doubt, the application has already arrived.
RECEIVED_TEXT = re.compile(
    r"we('ve| have) received your application|your application (has been|was) (received|submitted|sent)|"
    r"application (was |has been )?successfully submitted|successfully submitted your application", re.IGNORECASE)
# ... and the ones that say it has not: something is left to do before it counts.
STILL_TO_DO_TEXT = re.compile(
    r"\b(to|and|then) (complete|finish|continue|submit) (your|the|this) application\b|"
    r"\bbefore (your|the) application (is|can be) (complete|submitted|considered)", re.IGNORECASE)


def says_received(text: str, site_phrases: Iterable[str] = ()) -> bool:
    """The page's text says the application has arrived, in general words or the site's own -- and nothing on
    it says there is more to do first."""
    text = " ".join((text or "").split())
    if not text or STILL_TO_DO_TEXT.search(text):
        return False
    lowered = text.lower()
    return bool(CONFIRMATION_TEXT.search(text)) or any(p and p.lower() in lowered for p in site_phrases)
