"""What counts as a marketing opt-in on an account form (f276, reversed by f336).

The detector identifies marketing opt-in phrases (newsletters, promotions, updates, job alerts).
Under f336 (Task 3), marketing opt-ins are automatically accepted/ticked to avoid unnecessary stops.
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
