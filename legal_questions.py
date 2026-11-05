"""Legal and self-identification question classification and profile isolation.

Answers to legal, compliance, and EEO questions may only be drawn from explicit
facts in the candidate's profile, or marked as 'declined' when a prefer-not-to-say
option is offered. When no fact exists and no decline option is present, the agent
waits for the owner rather than fabricating a legal declaration.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Optional

import concept_matcher

_DATA_PATH = Path(__file__).resolve().parent / "reference" / "legal_questions.json"
_CONFIG = json.loads(_DATA_PATH.read_text(encoding="utf-8"))

CATEGORIES: dict[str, dict[str, Any]] = _CONFIG["categories"]
DECLINE_PHRASES: tuple[str, ...] = tuple(_CONFIG["decline_phrases"])


def classify_legal_question(question: str) -> Optional[str]:
    """Classifies a question into a legal/EEO category or returns None."""
    if not question:
        return None
    folded = concept_matcher.clean_text(question)
    if not folded:
        return None

    # Check categories in order; first matching category wins.
    for cat_name, cat_data in CATEGORIES.items():
        for phrase in cat_data.get("phrases", ()):
            cleaned_phrase = concept_matcher.clean_text(phrase)
            if not cleaned_phrase:
                continue
            pattern = rf"\b{re.escape(cleaned_phrase)}\b"
            if re.search(pattern, folded):
                return cat_name
    return None


def is_legal_question(question: str) -> bool:
    """True if the question belongs to a recognized legal or EEO category."""
    return classify_legal_question(question) is not None


def get_category_profile_fields(category: str) -> list[str]:
    """Returns the profile field names associated with this legal category."""
    cat_data = CATEGORIES.get(category)
    return list(cat_data.get("profile_fields", [])) if cat_data else []


def get_category_facts(category: str, profile: Any) -> dict[str, Any]:
    """Extracts ONLY non-empty profile fields for the given legal category."""
    if not profile or not category:
        return {}
    fields = get_category_profile_fields(category)
    facts: dict[str, Any] = {}
    for f in fields:
        val = getattr(profile, f, None)
        # For boolean fields (e.g. requires_visa_sponsorship), False is a valid fact.
        if isinstance(val, bool):
            facts[f] = val
        elif val is not None and str(val).strip() != "":
            facts[f] = val
    return facts


def find_decline_option(options: list[str]) -> Optional[str]:
    """Finds an option in options matching a decline / prefer-not-to-say phrase."""
    if not options:
        return None
    for opt in options:
        cleaned_opt = concept_matcher.clean_text(opt)
        for phrase in DECLINE_PHRASES:
            cleaned_phrase = concept_matcher.clean_text(phrase)
            if cleaned_phrase and cleaned_phrase in cleaned_opt:
                return opt
    return None
