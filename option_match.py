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


_INSTRUCTION = re.compile(r"\s*(?:click|tap) here\b", re.IGNORECASE)     # Lever: "Click Here (If you encounter an issue ...)"


def is_placeholder(option: str) -> bool:
    return bool(_PLACEHOLDER.fullmatch(option or "") or _INSTRUCTION.match(option or ""))


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


# Rows that differ only in what the applicant would be saying about sponsorship (f184).
_SPONSORED = re.compile(
    r"\bwith\s+(?:company\s+|employer\s+|visa\s+)?sponsorship\b|\b(?:require|need)\w*\s+(?:visa\s+)?sponsorship\b"
    r"|\bsponsorship\s+(?:is\s+)?required\b", re.IGNORECASE)
_UNRESTRICTED = re.compile(
    r"\bwithout\s+(?:any\s+)?(?:restrictions?|sponsorship)\b|\bno\s+(?:visa\s+)?sponsorship\b"
    r"|\b(?:(?:does|do|will)\s+not|don'?t|won'?t)\s+(?:now\s+or\s+in\s+the\s+future\s+)?(?:require|need)\s+"
    r"(?:visa\s+)?sponsorship\b", re.IGNORECASE)


def sponsorship_said(text: str) -> Optional[bool]:
    """What an option says the applicant needs: True for sponsorship ("No, I need sponsorship now", "Yes, with company
    sponsorship"), False for none ("Yes, without restrictions", "I do not need sponsorship"), None when it does not
    speak of sponsorship. The one reading of such rows, for choosing one and for checking one (f184, f191)."""
    if _UNRESTRICTED.search(text or ""):
        return False
    if _SPONSORED.search(text or ""):
        return True
    return None


def sponsorship_choice(options: list[str], answer: str, needs_sponsorship: Optional[bool]) -> tuple[bool, Optional[int]]:
    """(decided, index) for a bare Yes among rows that say what the applicant would need: 'Yes, without restrictions',
    'Yes, with company sponsorship'. An applicant who needs sponsorship is the sponsored row; one who does not is the
    unrestricted row; when the profile is silent (None), or only the row that would be a false statement is offered, nothing
    is chosen -- the question stays the owner's. Lists that do not speak of sponsorship are not decided here."""
    if not re.fullmatch(r"\s*yes\.?\s*", answer or "", re.IGNORECASE):
        return False, None
    sponsored = [i for i, o in enumerate(options) if sponsorship_said(o) is True]
    unrestricted = [i for i, o in enumerate(options) if sponsorship_said(o) is False]
    if not (sponsored or unrestricted):
        return False, None
    if needs_sponsorship is None:
        return True, None
    rows = sponsored if needs_sponsorship else unrestricted
    return True, (rows[0] if rows else None)


def broader_term(answer: str) -> Optional[str]:
    """The broader term a list that does not offer `answer` may offer instead ("South Asian" -> "Asian"), or None."""
    return _broader().get(plain(answer)) or None


def best_option(options: list[str], answer: str) -> Optional[int]:
    """The index of the option that is the answer, or None when none is -- or when two are equally good. An answer the
    list does not offer is looked for once more as its broader term ("South Asian" as "Asian"), never narrower."""
    hit = _best_option(options, answer)
    if hit is None:
        group = _group_of(answer) or ()
        if plain('Master') in group or plain('Doctorate') in group:
            hit = _best_option(options, 'Post-Graduate Degree')
        elif plain('Bachelor') in group:
            hit = _best_option(options, 'College/University Graduate')
    if hit is None:
        broader = _broader().get(plain(answer))
        if broader:
            hit = _best_option(options, broader)
    return hit


def matching_choices(options: list[str], answer: str) -> Optional[list[int]]:
    """Resolve a complete checkbox answer without splitting commas in labels."""
    whole = best_option(options, answer)
    if whole is not None:
        return [whole]
    separator = r"\s*[;\n|]\s*" if re.search(r"[;\n|]", answer) else r"\s*,\s+"
    parts = [part.strip() for part in re.split(separator, answer) if part.strip()]
    if len(parts) < 2:
        return None
    matches = [best_option(options, part) for part in parts]
    return (list(dict.fromkeys(match for match in matches if match is not None))
            if all(match is not None for match in matches) else None)


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
    # 6. numeric / salary / experience range buckets (e.g. '$111,000 - $120,000/yr' for '120000')
    return _numeric_range_match(real, answer)


# "I don't wish to answer" opens like a no, and is not one.
_DECLINES = re.compile(r"\b(?:wish|want|prefer|choose|like)\s+(?:not\s+)?to\s+(?:answer|disclose|say|respond|"
                       r"self|identify|provide|share)|\bdecline|\brather\s+not")


_SAYS_NO = re.compile(r"^(?:no|not|never|none|neither|i\s+(?:have|had|do|did|am|was|will)\s+not|"
                      r"i\s+(?:havent|hadnt|dont|didnt|wasnt|wont|am\s+not))\b")


def _parse_val(text: str) -> Optional[float]:
    """Extract a single numeric value from a currency/number string like '$120,000', '120k', '125000'."""
    text = (text or "").strip()
    if not text:
        return None
    clean_letters = re.sub(
        r"[\s\$,€£¥kK+/\-–—.]|\b(?:yr|year|years|month|months|hr|hour|hours|per|annual|base|salary|exp|experience)\b",
        "", text, flags=re.I)
    if clean_letters and not clean_letters.isdigit():
        return None
    m = re.search(r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*([kK])?", text)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    if m.group(2):
        num *= 1000
    return num


def _parse_range_option(text: str) -> Optional[tuple[float, float]]:
    """Parse range boundaries (low, high) from an option like '$111,000 - $120,000/yr', '< $20k', or '5+ years'."""
    text = (text or "").strip()
    if not text:
        return None
    # 1. Range: A to B or A - B
    m_range = re.search(
        r"(\$?\s*\d+(?:,\d{3})*(?:\.\d+)?\s*[kK]?)\s*(?:-|–|—|\bto\b)\s*(\$?\s*\d+(?:,\d{3})*(?:\.\d+)?\s*[kK]?)",
        text)
    if m_range:
        low = _parse_val(m_range.group(1))
        high = _parse_val(m_range.group(2))
        if low is not None and high is not None:
            return (min(low, high), max(low, high))
    # 2. Lower bound only: '300,000+', 'over $300k', '> 5'
    m_lower = re.search(
        r"(?:over|more than|greater than|>)\s*(\$?\s*\d+(?:,\d{3})*(?:\.\d+)?\s*[kK]?)|"
        r"(\$?\s*\d+(?:,\d{3})*(?:\.\d+)?\s*[kK]?)\s*(?:\+|and (?:above|more)|or more)",
        text, re.I)
    if m_lower:
        val_str = m_lower.group(1) or m_lower.group(2)
        low = _parse_val(val_str)
        if low is not None:
            return (low, float("inf"))
    # 3. Upper bound only: 'Below $20,000', '< 1 year', 'under 30k'
    m_upper = re.search(
        r"(?:below|under|less than|up to|<)\s*(\$?\s*\d+(?:,\d{3})*(?:\.\d+)?\s*[kK]?)",
        text, re.I)
    if m_upper:
        high = _parse_val(m_upper.group(1))
        if high is not None:
            return (0.0, high)
    return None


def _numeric_range_match(real: list[tuple[int, str]], answer: str) -> Optional[int]:
    target = _parse_val(answer)
    if target is None:
        return None
    ranges = []
    for i, o in real:
        rng = _parse_range_option(o)
        if rng:
            ranges.append((i, rng[0], rng[1], o))
    if len(ranges) < 2:
        return None
    matching = []
    for i, low, high, o in ranges:
        if low <= target <= high:
            matching.append(i)
    if len(matching) == 1:
        return matching[0]
    if len(matching) > 1:
        for idx in matching:
            entry = next((e for e in ranges if e[0] == idx), None)
            if entry and entry[1] <= target < entry[2]:
                return idx
        return matching[0]
    return None


_SPAN = re.compile(r"^\s*(?:(?:within|in|about|around|up to|after)\s+)?(\d+)\s*(day|week)s?\s*$", re.IGNORECASE)
_DAYS = {"day": 1, "week": 7}


def _span_days(text: str) -> Optional[int]:
    found = _SPAN.match(text or "")
    return int(found.group(1)) * _DAYS[found.group(2).lower()] if found else None


def availability_option(options: list[str], answer: str) -> Optional[int]:
    """The one option that states the same wait as the answer ('2 weeks' is 'Within 2 weeks'), or None.

    Only a span of days or weeks, with at most 'within/in/about' before it; '14 days' and '2 weeks' are the same wait,
    so a list offering both has no one answer. Months, dates and anything else are left alone. Used only when the
    question asks when the applicant can start (concept NOTICE_PERIOD): the same words answer other questions
    ('Years of experience?') differently."""
    wanted = _span_days(answer)
    if wanted is None:
        return None
    hits = [i for i, option in enumerate(options) if _span_days(option) == wanted]
    return hits[0] if len(hits) == 1 else None


def resolve_offered(options: list[str], answer: str, *, needs_sponsorship: Optional[bool] = None,
                    availability: bool = False) -> str:
    """The exact offered label that a model's answer means, or "" when none is -- the one reading of an answer given
    for a list, shared by the AI path and the browser's choosers.

    The same words win; a bare Yes among rows that speak of sponsorship follows the profile and is otherwise left (an
    answer the profile cannot back is never chosen); then the bounded matches of best_option, and for a start-date
    question the same wait in other words. No substring, near-spelling or 'only one option' guess (f226)."""
    answer = (answer or "").strip()
    if not options or not answer:
        return ""
    for option in options:
        if option.strip().lower() == answer.lower():
            return option
    decided, index = sponsorship_choice(list(options), answer, needs_sponsorship)
    if decided:
        return options[index] if index is not None else ""
    index = best_option(list(options), answer)
    if index is None and availability:
        index = availability_option(list(options), answer)
    return options[index] if index is not None else ""
