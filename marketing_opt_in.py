"""What counts as a marketing opt-in on an account form (f276).

The owner's rule for creating an account is that marketing opt-ins are never the agent's to give. The code that fills a
create-account form left such a box alone, which assumed the site draws it unticked. Walmart's sign-up form draws
"Send me emails about new arrivals, hot items, daily savings and more." already ticked, and the agent would have pressed
Continue with it ticked. One definition of the wording, used to leave a box unticked and to untick one the site ticked.
"""
from __future__ import annotations

import re

_MARKETING = re.compile(
    r"hear more|newsletters?|campaigns?|marketing|job alerts?|promotions?|promotional|"
    r"new arrivals|daily savings|hot items|special offers?|exclusive offers?|great deals|\bdeals\b|"
    r"send me (?:emails?|offers|updates|news|texts?|messages)\b|"
    r"(?:emails?|texts?) (?:me )?about (?:new|our|the latest|special|exclusive|great|upcoming)",
    re.IGNORECASE)


def is_marketing_opt_in(label: str) -> bool:
    """Whether a checkbox's label asks to be sent marketing, offers or news."""
    return bool(_MARKETING.search(" ".join((label or "").split())))
