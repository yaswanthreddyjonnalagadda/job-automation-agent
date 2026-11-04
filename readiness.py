"""What a posting's form will ask, before the application starts (f319).

Greenhouse publishes every job's application form (?questions=true): labels,
required flags, and options. Each required question runs through the same
answer stack the application will use -- a profile fact, an upload, or an
approved answer -- and the card says how many are answered and names the ones
that need the owner. A question the stack cannot answer is listed, never
assumed.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

import requests

import answer_bank
import concept_matcher
import diagnostics

logger = logging.getLogger(__name__)

#: Tests point the answer-bank lookup at a fixture through this; None means the real bank.
BANK_FILE = None

_UPLOAD = re.compile(r"\b(resume|c\.?v\.?|curriculum vitae|cover letter)\b", re.IGNORECASE)
_JOB_ID = re.compile(r"/jobs/(\d+)")


def greenhouse_job_id(url: str) -> Optional[str]:
    """The Greenhouse job id from a posting address, or None when it is not one."""
    match = _JOB_ID.search(url or "")
    return match.group(1) if match else None


def fetch_greenhouse_questions(token: str, job_id: str, timeout: float = 8.0) -> list[dict]:
    """The application form as published: every block (questions, location, compliance, demographic survey)
    flattened to label/required/options. A board that does not publish its form yields no questions."""
    try:
        resp = requests.get(
            f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}?questions=true",
            headers={"User-Agent": "job-agent-readiness/1", "Accept": "application/json"},
            timeout=timeout)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception as exc:
        logger.debug("No form for %s/%s: %s", token, job_id, diagnostics.short_exc(exc, 100))
        return []
    blocks: list[dict] = list(data.get("questions") or [])
    blocks.extend(data.get("location_questions") or [])
    for group in data.get("compliance") or []:
        blocks.extend((group or {}).get("questions") or [])
    blocks.extend(data.get("demographic_questions") or [])
    out = []
    for raw in blocks:
        if not isinstance(raw, dict):
            continue
        fields = raw.get("fields") or []
        options: list[str] = []
        for field in fields:
            for value in (field.get("values") or []) if isinstance(field, dict) else []:
                label = (value or {}).get("label") if isinstance(value, dict) else value
                if label:
                    options.append(str(label))
        out.append({"label": str(raw.get("label") or "").strip(),
                    "required": bool(raw.get("required")),
                    "options": options,
                    "fields": [f.get("type") for f in fields if isinstance(f, dict)]})
    return out


def _upload(label: str, fields: list) -> bool:
    if "file" in [f for f in fields or []]:
        return True
    return bool(_UPLOAD.search(label or ""))


def assess(questions: list[dict], profile: Any, company: str = "") -> dict[str, Any]:
    """Each required question through the application stack: a profile fact, an upload, or an approved answer.
    Returns required/answered counts plus the labels that need the owner."""
    needs_owner: list[str] = []
    required = 0
    for question in questions or []:
        label = (question or {}).get("label") or ""
        if not label or not (question or {}).get("required"):
            continue
        required += 1
        options = (question or {}).get("options") or []
        if _upload(label, (question or {}).get("fields")):
            continue
        concept = concept_matcher.match_concept(label)
        value, _src = concept_matcher.resolve_profile_value(concept or "", profile, options, label)
        if value:
            continue
        if answer_bank.lookup(label, company or "", path=BANK_FILE or answer_bank.BANK_FILE) is not None:
            continue
        needs_owner.append(label)
    return {"required": required, "answered": required - len(needs_owner), "needs_owner": needs_owner}


def summarize(assessment: dict[str, Any]) -> str:
    """The card line: what was checked, in plain words."""
    required = int((assessment or {}).get("required") or 0)
    answered = int((assessment or {}).get("answered") or 0)
    missing = len((assessment or {}).get("needs_owner") or [])
    if required == 0:
        return "No required questions found"
    if missing == 0:
        return f"All {required} required answered"
    noun = "needs" if missing == 1 else "need"
    return f"{answered} of {required} required answered · {missing} {noun} you"
