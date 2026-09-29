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
    "1 item selected, United States of America (+1)" -> ..., "united states of america"

    A parenthetical is dropped, never used on its own: "Asian (United States
    of America)" is an ethnicity, not a country.
    """
    def trimmed(part: str) -> list[str]:
        return [part,
                re.sub(r"\([^)]*\)", " ", part),               # drop a parenthetical
                re.sub(r"\+\s*\d[\d\s-]*", " ", part)]         # drop a dialling code

    raw = str(text or "").strip()
    out = trimmed(raw)
    for sep in (" - ", " – ", " — ", ", ", " | "):
        if sep in raw:
            for part in raw.split(sep):
                out.extend(trimmed(part))
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


def _spellings(entry: dict) -> list[str]:
    return list(entry.get("names", [])) + list(entry.get("aliases", []))


@lru_cache(maxsize=2)
def _index(table: str) -> dict[str, str]:
    index: dict[str, str] = {}
    for code, entry in _data()[table].items():
        for name in _spellings(entry):
            index.setdefault(normalize(name), code)
    return index


def _country_index() -> dict[str, str]:
    return _index("countries")


def _us_state_index() -> dict[str, str]:
    return _index("us_states")


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
    """Every spelling of a country: ISO names first, then other forms' spellings."""
    return tuple(_spellings(_data()["countries"].get((code or "").upper(), {})))



def country_spellings(text: str) -> list[str]:
    """The given spelling first, then the country's ISO spellings, longest
    (most specific) first: "United States" -> "United States",
    "United States of America". For building option candidates -- the short
    aliases ("US") are left out because they match too much by prefix."""
    given = str(text or "").strip()
    code = country_code(given)
    formal = sorted(_data()["countries"].get(code or "", {}).get("names", []), key=len, reverse=True)
    out = [given] if given else []
    for name in formal:
        if name not in out:
            out.append(name)
    return out


def us_state_spellings(text: str) -> list[str]:
    """The given spelling, then the state's name, USPS code and "Name (CODE)"."""
    given = str(text or "").strip()
    code = us_state_code(given)
    out = [given] if given else []
    if code:
        name = _data()["us_states"][code]["names"][0]
        for spelling in (name, code, f"{name} ({code})"):
            if spelling not in out:
                out.append(spelling)
    return out


def us_state_map() -> dict[str, str]:
    """Lower-case state name -> lower-case USPS code, for older callers."""
    return {normalize(entry["names"][0]): code.lower() for code, entry in _data()["us_states"].items()}


def same_country(a: str, b: str) -> bool:
    code = country_code(a)
    return code is not None and code == country_code(b)


def same_us_state(a: str, b: str) -> bool:
    code = us_state_code(a)
    return code is not None and code == us_state_code(b)


def same_place(a: str, b: str) -> bool:
    """True when two spellings name the same country or the same US state."""
    return same_country(a, b) or same_us_state(a, b)


def _regions(parts: list[str]) -> tuple[set[str], set[str]]:
    """The US states and the countries a town's later parts name. A part that is a
    state's spelling is a state even where it is also a country's code ("VA" is
    Virginia here, not the Vatican; "IN" Indiana, not India)."""
    states: set[str] = set()
    countries: set[str] = set()
    for part in parts:
        state = us_state_code(part)
        if state:
            states.add(state)
            continue
        country = country_code(part)
        if country:
            countries.add(country)
    return states, countries


def same_locality(a: str, b: str) -> bool:
    """True when two spellings name the same town: "Springfield, IL" and
    "Springfield, Illinois, United States".

    Both give the town first and a region after it. The town's name is the same
    words; the state, in any spelling, is the same state on both sides (named on
    one side only is not enough: several states have a Springfield); a country
    left out of one side is not a difference, one named on both must agree.
    """
    first, second = str(a or "").split(","), str(b or "").split(",")
    if len(first) < 2 or len(second) < 2:
        return False
    town = normalize(first[0])
    if not town or town != normalize(second[0]):
        return False
    states_a, countries_a = _regions(first[1:])
    states_b, countries_b = _regions(second[1:])
    if states_a != states_b or (countries_a and countries_b and countries_a != countries_b):
        return False
    return bool(states_a or (countries_a and countries_b))


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
        for entry in _data()[table].values():
            for name in _spellings(entry):
                norm = normalize(name)
                if len(norm) > 3:
                    names.add(norm)
    return frozenset(names)
