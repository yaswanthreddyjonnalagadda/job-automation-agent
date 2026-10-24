"""One employer written two ways is one employer.

The posting says 'Marathon Petroleum Company LP', the careers site 'Marathon Petroleum', an earlier run 'marathon
petroleum co'. Only the legal form differs, so it is dropped before names are compared; the rest must match whole.
"""
import re

_LEGAL_FORMS = frozenset({"inc", "incorporated", "llc", "lp", "llp", "ltd", "limited", "corp", "corporation", "co",
                          "company", "plc", "gmbh", "the"})


def employer_key(name: str) -> str:
    """The employer's name without case, punctuation or legal form ('' when nothing else is left)."""
    words = re.findall(r"[a-z0-9]+", (name or "").lower().replace("&", " and "))
    return " ".join(w for w in words if w not in _LEGAL_FORMS)


def same_employer(a: str, b: str) -> bool:
    key = employer_key(a)
    return bool(key) and key == employer_key(b)
