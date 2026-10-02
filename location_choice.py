"""Which places to pick when a form asks where the owner would like to work or apply.

"Please select one or more locations where you'd like to apply." -- Eagle Mountain, UT / El Paso, TX / Lebanon, IN /
Loudoun County, VA / Papillion, NE. The owner's rule (30 September 2026): a place in the state the owner lives in
(or one the profile prefers) first; after that, anywhere is fine. So a tick-all-that-apply list takes the owner's
own state's places and every other place too when the profile is open to relocation; a pick-one list takes the first
place in the owner's state, else (open to relocation) the first place listed.

Everything about the owner comes from the profile (state, locations, open_to_relocation); places are read through
geo_reference -- no place is written here.
"""
from __future__ import annotations

import re
from typing import Optional

import geo_reference

_ASKS = re.compile(r"\b(?:locations?|offices?|sites?|cities|city|work\s*places?)\b", re.IGNORECASE)
_WANTS = re.compile(r"\b(?:apply|applying|work|working|interested|prefer\w*|consider\w*|like|want|willing|desired?)\b",
                    re.IGNORECASE)
# Where the owner lives, was born, or a yes/no about moving: other questions.
_NOT_THIS = re.compile(r"\b(?:current(?:ly)?|reside|residing|live|living|home|born|birth|relocat\w*|commut\w*)\b",
                       re.IGNORECASE)
_REMOTE = re.compile(r"^\s*(?:remote|virtual|work from home|anywhere)\b", re.IGNORECASE)


def asks(question: str) -> bool:
    """Whether a question asks which of its places the owner would like (not where the owner lives)."""
    q = question or ""
    return bool(_ASKS.search(q) and _WANTS.search(q) and not _NOT_THIS.search(q))


def _state_of(option: str) -> Optional[str]:
    """The US state an option names, read from its last part ('Lebanon, IN' is Indiana, not the country)."""
    last = (option or "").split(",")[-1].strip()
    return geo_reference.us_state_code(last) or geo_reference.us_state_code(option or "")


def is_place(option: str) -> bool:
    return bool(_state_of(option) or geo_reference.country_code(option or "") or _REMOTE.match(option or ""))


def _home_states(profile) -> set[str]:
    states = {geo_reference.us_state_code(str(getattr(profile, "state", "") or ""))}
    for place in getattr(profile, "locations", ()) or ():
        states.add(_state_of(str(place)))
    return {s for s in states if s}


def choices(question: str, options: list[str], profile, several: bool) -> Optional[list[int]]:
    """The indexes of the options to pick, in order; None when this is not such a question or the profile says
    nothing that decides it (the question is then left as before)."""
    if not asks(question):
        return None
    real = [(i, o) for i, o in enumerate(options) if o and not geo_reference.is_placeholder(o)]
    places = [(i, o) for i, o in real if is_place(o)]
    if len(places) < 2 or len(places) < 0.6 * len(real):
        return None
    home = _home_states(profile)
    first = [i for i, o in places if _state_of(o) in home]
    rest = [i for i, o in places if i not in first]
    anywhere = bool(getattr(profile, "open_to_relocation", False))
    if several:
        picked = first + (rest if anywhere else [])
        return picked or None
    if first:
        return [first[0]]
    return [places[0][0]] if anywhere else None
