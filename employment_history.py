"""'Have you worked at <company>?' -- answered from the person's own work history.

The owner's rule (29 September 2026): such a question is answered "No", unless the company is one the person has
worked for, which their resume and profile say. Blue Cross and Blue Shield of Louisiana asked "Have you worked at
Louisiana Blue as an employee or a contracted employee?" and the question was left blank: the agent knew only a
few wordings ("previously employed by", "worked here before"), answered those with one fixed profile value
whatever the company, and had a rule written for one employer (OCC) in the code.

This is the one place that decides it:
  asks_about()    whether a question asks if the person worked at a named organisation (or at this employer:
                  "for us", "here"), and which -- not "a government agency" or "a competitor", which name no one
  past_employers() who the person has worked for: the profile's employers and the resume's work history
  answer()        "Yes" when the organisation is one of them, otherwise "No"
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

_ASKS = re.compile(
    r"\b(?:have|did|do|are|were)\s+you\b[^?]{0,40}?\b(?:ever\s+)?"
    r"(?:worked|ever\s+work|been\s+employed|been\s+(?:an?\s+)?(?:employee|contractor|intern|consultant|temp)|"
    r"provided\s+(?:services|work)|currently\s+(?:work|employed))\b[^?]{0,30}?\b(?:at|for|with|by|to)\s+"
    r"(?P<org>[^?,;:*]{2,80})", re.IGNORECASE)
_FORMER = re.compile(r"\b(?:former|previous|past)\s+(?:employee|contractor|intern)\s+(?:of|at|with)\s+"
                     r"(?P<org>[^?,;:*]{2,80})|\bworked\s+here\s+before\b|\bformer\s+employee\b", re.IGNORECASE)
_WISH = re.compile(r"\b(?:want|like|love|wish|willing|able|interested|excited|hope|plan)\s+(?:to\s+)?work",
                   re.IGNORECASE)
# Where the name ends: what follows it is the kind of work, or a time.
_NAME_ENDS = re.compile(r"\s+(?:as|in|before|previously|during|within|or\s+any|or\s+its|and|including|either|"
                        r"at\s+any\s+time|in\s+the\s+past|ever)\b.*$", re.IGNORECASE)
_THIS_EMPLOYER = re.compile(r"^(?:us|here|this\s+(?:company|organi[sz]ation|employer|firm)|our\s+(?:company|"
                            r"organi[sz]ation|firm)|the\s+company)$", re.IGNORECASE)
_NOBODY_IN_PARTICULAR = re.compile(r"^(?:a|an|any|another|the\s+(?:federal|state|us|u\.s\.)|government|federal|"
                                   r"one\s+of|some)\b", re.IGNORECASE)
_WORDS_THAT_NAME_NO_ONE = {"inc", "llc", "ltd", "corp", "corporation", "company", "co", "group", "the", "of", "and",
                           "services", "holdings", "international", "limited", "na", "national", "association",
                           "usa", "us", "america", "llp", "plc", "gmbh", "sa"}
_DATE_RANGE = re.compile(r"\b(?:[A-Za-z]{3,9}\.?\s+)?\d{4}\s*[-–—]\s*(?:(?:[A-Za-z]{3,9}\.?\s+)?\d{4}|present|current|now)\b",
                         re.IGNORECASE)


def asks_about(question: str, company: str = "") -> Optional[str]:
    """The organisation a 'have you worked at ...' question names, or `company` when it asks about this employer
    ('for us', 'here'), or None when it is not such a question or names no one in particular."""
    text = " ".join((question or "").split())
    if _WISH.search(text):
        return None                                 # "Why do you want to work for us?" asks nothing of the past
    match = _ASKS.search(text) or _FORMER.search(text)
    if not match:
        return None
    org = (match.groupdict().get("org") or "").strip()
    if not org:
        return company or None                      # "a former employee?", "worked here before?"
    org = _NAME_ENDS.sub("", org).strip(" .'\"")
    if not org or _THIS_EMPLOYER.match(org):
        return company or None
    if _NOBODY_IN_PARTICULAR.match(org):
        return None
    return org


def past_employers(profile=None, resume_text: str = "") -> list[str]:
    """Who the person has worked for: the profile's current employer and work history, and every resume line
    that reads 'Company, place | dates' (education lines carry one date, not a range, and are left out)."""
    found: list[str] = []
    if profile is not None:
        found.append(str(getattr(profile, "current_employer", "") or ""))
        for entry in getattr(profile, "reasons_for_leaving", ()) or ():
            if isinstance(entry, (list, tuple)) and entry:
                found.append(str(entry[0] or ""))
    for line in (resume_text or "").splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 2 and any(_DATE_RANGE.search(p) for p in parts[1:]):
            found.append(parts[0].split(",")[0])
    return [e for e in dict.fromkeys(e.strip() for e in found) if e]


def _words(name: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (name or "").lower()) if w not in _WORDS_THAT_NAME_NO_ONE}


def same_organisation(a: str, b: str) -> bool:
    """Two names for one organisation: every telling word of the shorter is in the longer
    ('Capital One' and 'Capital One Financial Corp.'; 'Louisiana Blue' and 'Blue Cross and Blue Shield of
    Louisiana'). An acronym counts ('OCC' and 'The Options Clearing Corporation')."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    small, large = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    if small <= large:
        return True
    for short, long in ((a, b), (b, a)):
        letters = re.sub(r"[^a-z]", "", (short or "").lower())
        initials = "".join(w[0] for w in re.findall(r"[a-z]+", (long or "").lower()) if w not in {"of", "and", "the"})
        if 2 <= len(letters) <= 6 and letters == initials:
            return True
    return False


def answer(question: str, company: str, employers: Iterable[str]) -> str:
    """'Yes' when the organisation the question names is one the person worked for, 'No' when it is not, and ''
    when the question is not one of these."""
    org = asks_about(question, company)
    if not org:
        return ""
    return "Yes" if any(same_organisation(org, e) for e in employers) else "No"
