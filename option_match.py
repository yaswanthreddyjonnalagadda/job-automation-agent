"""Which of a list's options IS the answer -- one place for every list the agent fills.

The rules, from the portal research (reference/ats_fields/README.md):

1. The same words (case, spacing and punctuation aside) win.
2. The same place in another spelling: 'United States' is 'United States of America (+1)' and 'United States +1'
   (a dial code after a country is not part of its name) -- through geo_reference.py, never a place list here.
3. The same answer in another portal's words: 'Masters of Science' is 'Master', 'Fluent' is 'Advanced'
   (reference/answer_equivalents.json).
4. An option that starts with the whole answer and then only adds to it after punctuation: 'No' is
   'No, I do not have a disability'.
Never an option that merely contains the answer: 'Virginia' is not 'West Virginia', 'Male' is not 'Female'. When
two options are equally good, there is no answer -- the agent does not guess.
"""
from __future__ import annotations

import functools
import json
import re
from pathlib import Path
from typing import Optional

import geo_reference

EQUIVALENTS_FILE = Path(__file__).resolve().parent / "reference" / "answer_equivalents.json"
_DIAL_CODE = re.compile(r"\s*[(\[]?\s*\+\s*\d{1,4}(?:[-\s]\d{1,4})?\s*[)\]]?\s*$")
# What a list shows before anything is picked ('–Select–', 'Select One', 'Choose an option', 'Search...').
_PLACEHOLDER = re.compile(r"^\s*(?:[-–—]+\s*)?(?:no selection|select(?: one| an option| from list)?|please select|"
                          r"choose(?: one| an option)?|pick one|none selected|search|make a selection)?\s*"
                          r"(?:\.\.\.|…)?\s*"
                          r"(?:[-–—]+)?\s*\.*$", re.IGNORECASE)


def plain(text: str) -> str:
    """Lower case, one space, no punctuation but the apostrophe folded away ('Master's' -> 'masters')."""
    text = (text or "").lower().replace("’", "'").replace("'", "")
    text = re.sub(r"[^\w\s+]", " ", text)
    return " ".join(text.split())


def is_placeholder(option: str) -> bool:
    return bool(_PLACEHOLDER.fullmatch(option or ""))


@functools.lru_cache(maxsize=1)
def _groups() -> tuple[tuple[str, ...], ...]:
    try:
        data = json.loads(EQUIVALENTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    groups = []
    for name, members in data.items():
        if name.startswith("_") or not isinstance(members, list):
            continue
        for group in members:
            groups.append(tuple(plain(m) for m in group))
    return tuple(groups)


@functools.lru_cache(maxsize=1)
def _broader() -> dict[str, str]:
    try:
        data = json.loads(EQUIVALENTS_FILE.read_text(encoding="utf-8")).get("broader") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    return {plain(k): v for k, v in data.items() if isinstance(v, str)}


def _group_of(answer: str) -> Optional[tuple[str, ...]]:
    want = plain(answer)
    return next((g for g in _groups() if want in g), None)


def _without_dial_code(text: str) -> str:
    return _DIAL_CODE.sub("", text or "").strip()


def _only_one(hits: list[int]) -> Optional[int]:
    unique = list(dict.fromkeys(hits))
    return unique[0] if len(unique) == 1 else None


def best_option(options: list[str], answer: str) -> Optional[int]:
    """The index of the option that is the answer, or None when none is -- or when two are equally good. An answer the
    list does not offer is looked for once more as its broader term ("South Asian" as "Asian"), never narrower."""
    hit = _best_option(options, answer)
    if hit is None:
        broader = _broader().get(plain(answer))
        if broader:
            hit = _best_option(options, broader)
    return hit


def _best_option(options: list[str], answer: str) -> Optional[int]:
    want = plain(answer)
    if not want:
        return None
    real = [(i, o) for i, o in enumerate(options) if o and not is_placeholder(o)]

    # A locality contains a region, but is not interchangeable with that region
    # or another town in it. Resolve it before country/state alias matching.
    if geo_reference.same_locality(answer, answer):
        return _only_one([i for i, o in real if geo_reference.same_locality(answer, o)])

    # 1. the same words
    hit = _only_one([i for i, o in real if plain(o) == want])
    if hit is not None:
        return hit
    # 2. the same place, however it is spelled, with or without a dial code
    if geo_reference.country_code(answer) or geo_reference.us_state_code(answer):
        hit = _only_one([i for i, o in real
                         if geo_reference.same_place(answer, _without_dial_code(o))])
        if hit is not None:
            return hit
    # 3. the same answer in another portal's words
    group = _group_of(answer)
    if group:
        hit = _only_one([i for i, o in real if plain(o) in group])
        if hit is not None:
            return hit
        hit = _only_one([i for i, o in real
                         if any(plain(o).startswith(member + " ") for member in group if len(member) > 3)])
        if hit is not None:
            return hit
    # 4. the whole answer first, then only more after punctuation ('No, I do not ...', 'United States +1')
    starts = []
    for i, o in real:
        text = (o or "").strip()
        if text.lower().startswith((answer or "").strip().lower()):
            rest = text[len((answer or "").strip()):]
            if not rest or re.match(r"\s*[,(\[+\-–—:;/]", rest):
                starts.append(i)
    hit = _only_one(starts)
    if hit is not None:
        return hit
    # 5. a plain No where the choices say it in words: the one choice that is a no ("Never been a contractor or an
    #    employee" among "Currently a contractor", "Previously an employee" ... -- Lucid, 29 September). Two such
    #    choices, or none, and there is no answer. A Yes is never read this way: several choices can mean yes.
    if want in ("no", "none", "never"):
        return _only_one([i for i, o in real if _SAYS_NO.match(plain(o)) and not _DECLINES.search(plain(o))])
    return None


# "I don't wish to answer" opens like a no, and is not one.
_DECLINES = re.compile(r"\b(?:wish|want|prefer|choose|like)\s+(?:not\s+)?to\s+(?:answer|disclose|say|respond|"
                       r"self|identify|provide|share)|\bdecline|\brather\s+not")


_SAYS_NO = re.compile(r"^(?:no|not|never|none|neither|i\s+(?:have|had|do|did|am|was|will)\s+not|"
                      r"i\s+(?:havent|hadnt|dont|didnt|wasnt|wont|am\s+not))\b")
