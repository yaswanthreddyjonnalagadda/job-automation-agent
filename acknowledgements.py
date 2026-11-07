"""Acknowledgements, consents, certifications, and marketing opt-ins resolution.

Always accept terms, privacy, certifications, background check consents,
e-signatures, at-will agreements, arbitration clauses, communication consents,
and marketing opt-ins. Checkboxes that represent legal declarations (such as
work authorization, visa sponsorship, citizenship) or job condition questions
(such as relocation, travel, on-site, shift, salary) remain questions and are
never auto-checked blindly as acknowledgements.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

import concept_matcher
import legal_questions
import marketing_opt_in
import safety

_DATA_PATH = Path(__file__).resolve().parent / "reference" / "acknowledgements.json"
_CONFIG: dict[str, Any] = json.loads(_DATA_PATH.read_text(encoding="utf-8"))

CATEGORIES: dict[str, dict[str, Any]] = _CONFIG.get("categories", {})
AGREE_BUTTONS: tuple[str, ...] = tuple(_CONFIG.get("agree_buttons", ()))
EXCLUSIONS: tuple[str, ...] = tuple(_CONFIG.get("job_condition_exclusions", ()))

_LEGAL_EXCEPTIONS = re.compile(
    r"\b(?:sponsor|sponsorship|authori[sz]ed to work|work authori[sz]ation|visa|"
    r"immigration|citizen|citizenship|felony|criminal|convict)\b",
    re.IGNORECASE,
)


def _has_job_condition_exclusion(text: str) -> bool:
    cleaned = concept_matcher.clean_text(text)
    for exc in EXCLUSIONS:
        c_exc = concept_matcher.clean_text(exc)
        if not c_exc:
            continue
        pattern = r"\b" + re.escape(c_exc) + r"\b"
        if re.search(pattern, cleaned, re.IGNORECASE) or re.search(pattern, text, re.IGNORECASE):
            return True
        if "-" in c_exc:
            no_hyphen = c_exc.replace("-", "")
            if re.search(r"\b" + re.escape(no_hyphen) + r"\b", cleaned, re.IGNORECASE):
                return True
    return False


def is_acknowledgement(text: str) -> bool:
    """Whether a checkbox or section is an acknowledgement, consent, or marketing opt-in."""
    if not text:
        return False
    folded = concept_matcher.clean_text(text)
    if not folded:
        return False

    # Job conditions (relocate, on-site, travel, salary, shift, etc.) are questions, not acknowledgements
    if _has_job_condition_exclusion(text):
        return False

    # Legal questions and declarations (e.g. sponsorship, work auth) are NOT acknowledgements
    if legal_questions.is_legal_question(text) or safety.is_legal_status_question(text) or _LEGAL_EXCEPTIONS.search(folded):
        return False

    # Marketing opt-in phrases
    if marketing_opt_in.is_marketing_opt_in(text):
        return True

    # Check all configured categories in reference/acknowledgements.json
    for cat_data in CATEGORIES.values():
        for phrase in cat_data.get("phrases", ()):
            cleaned_phrase = concept_matcher.clean_text(phrase)
            if cleaned_phrase and cleaned_phrase in folded:
                return True

    return False


def is_agree_button(label: str) -> bool:
    """Whether a button text means 'agree / accept / continue' on a consent modal."""
    if not label:
        return False
    folded = concept_matcher.clean_text(label)
    for btn in AGREE_BUTTONS:
        if folded == concept_matcher.clean_text(btn):
            return True
    return False
