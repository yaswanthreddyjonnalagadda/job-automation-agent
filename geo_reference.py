"""
Place names as data: which countries and US states a spelling refers to.

Forms spell places a dozen ways ("United States", "USA", "United States of
America (+1)", "Virginia (VA)", "VA - Virginia"). This module answers two
questions from reference/geo.json instead of from literals in the code:

  * which country or US state a spelling means, so one spelling can be
    matched to another; and
  * what a list of options holds -- countries, US states, or neither -- so
    the options can confirm what a question is asking.

Logic modules must not spell place names themselves (tests/test_no_place_literals.py
enforces it). Knowing places from data is what lets the agent correct any
wrong default, not only the few someone wrote into an if-statement.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

_DATA_PATH = Path(__file__).resolve().parent / "reference" / "geo.json"

COUNTRY = "COUNTRY"
US_STATE = "US_STATE"

# Entries that are prompts, not answers ("- Select -", "Please choose").
_PLACEHOLDER = re.compile(r"^\W*(select|choose|please (select|choose)|none|--+|pick)\b.*$|^\W*$", re.IGNORECASE)


def normalize(text: str) -> str:
    """Case, accents, punctuation and spacing are not differences."""
    folded = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    folded = re.sub(r"[^\w\s]", " ", folded.lower())
    return " ".join(folded.split())


def _variants(text: str) -> list[str]:
    """The spellings worth looking up for one option or answer.

    "Virginia (VA)" -> "virginia va", "virginia"
    "United States (+1)" / "United States +1" -> "united states"
    "VA - Virginia" -> "va", "virginia"

    A parenthetical is dropped, never used on its own: "Asian (United States
    of America)" is an ethnicity, not a country.
    """
    raw = str(text or "").strip()
    out = [raw]
    out.append(re.sub(r"\([^)]*\)", " ", raw))                 # drop a parenthetical
    out.append(re.sub(r"\+\s*\d[\d\s-]*", " ", raw))           # drop a dialling code
    for sep in (" - ", " – ", " — ", ", ", " | "):
        if sep in raw:
            out.extend(raw.split(sep))
    seen, result = set(), []
    for item in out:
        norm = normalize(item)
        if norm and norm not in seen:
            seen.add(norm)
            result.append(norm)
    return result


@lru_cache(maxsize=1)
def _data() -> dict:
    return json.loads(_DATA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _country_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for code, names in _data()["countries"].items():
        for name in names:
            index.setdefault(normalize(name), code)
    return index


@lru_cache(maxsize=1)
def _us_state_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for code, names in _data()["us_states"].items():
        for name in names:
            index.setdefault(normalize(name), code)
    return index


def _lookup(text: str, index: dict[str, str]) -> Optional[str]:
    for variant in _variants(text):
        if variant in index:
            return index[variant]
    return None


def country_code(text: str) -> Optional[str]:
    """ISO 3166-1 alpha-2 code for a country's spelling, or None."""
    return _lookup(text, _country_index())


def us_state_code(text: str) -> Optional[str]:
    """USPS code for a US state's (or territory's) spelling, or None."""
    return _lookup(text, _us_state_index())


def country_names(code: str) -> tuple[str, ...]:
    return tuple(_data()["countries"].get((code or "").upper(), ()))


def us_state_names(code: str) -> tuple[str, ...]:
    return tuple(_data()["us_states"].get((code or "").upper(), ()))


def us_state_map() -> dict[str, str]:
    """Lower-case state name -> lower-case USPS code, for older callers."""
    return {normalize(names[0]): code.lower() for code, names in _data()["us_states"].items()}


def same_country(a: str, b: str) -> bool:
    code = country_code(a)
    return code is not None and code == country_code(b)


def same_us_state(a: str, b: str) -> bool:
    code = us_state_code(a)
    return code is not None and code == us_state_code(b)


def same_place(a: str, b: str) -> bool:
    """True when two spellings name the same country or the same US state."""
    return same_country(a, b) or same_us_state(a, b)


def is_placeholder(option: str) -> bool:
    return bool(_PLACEHOLDER.match(str(option or "")))


def option_domain(options: Iterable[str], minimum: int = 5, share: float = 0.6) -> Optional[str]:
    """What a list of options holds: COUNTRY, US_STATE, or None.

    A list is a country list when most of its real entries are countries,
    whatever the question's label says ("Region of Residence" over a list
    of countries is a country question). Placeholders are not counted, and
    a short list is never classified: five entries are the least that can
    say what a list is.
    """
    real = [o for o in options or () if not is_placeholder(o)]
    if len(real) < minimum:
        return None
    countries = [c for c in (country_code(o) for o in real) if c]
    states = [s for s in (us_state_code(o) for o in real) if s]
    best, found = (COUNTRY, countries) if len(countries) >= len(states) else (US_STATE, states)
    # Many different places, not one place repeated in every option.
    if len(set(found)) < minimum:
        return None
    return best if len(found) / len(real) >= share else None


@lru_cache(maxsize=1)
def place_names() -> frozenset[str]:
    """Every normalised country and US-state spelling (two-letter codes left
    out, since they collide with ordinary words)."""
    names = set()
    for table in ("countries", "us_states"):
        for spellings in _data()[table].values():
            for name in spellings:
                norm = normalize(name)
                if len(norm) > 3:
                    names.add(norm)
    return frozenset(names)
