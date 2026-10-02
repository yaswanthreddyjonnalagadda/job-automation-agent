"""The answer bank: an answer given once is the answer wherever the same question is asked again.

Every answer the agent gives is already recorded against its application (the tracker's form_answers), but only the
owner's own, only on the same site and only in nearly the same words was ever used again -- so a question answered on
a Workday form was a new question on Greenhouse, and each new wording needed a change to the code (29 September).

What is kept, from the tracker:
  * every answer the owner gave, on any application ("you");
  * every answer the agent gave -- the AI's, or a choice it made and read back -- on an application the owner then
    sent ("confirmed": the owner saw it on the form before sending it).
What never is: a declaration or signature, a legal-status or sponsorship question (the profile answers those afresh
each time), an essay (written for one employer), a question that names the employer, a date (a start date goes
stale), and nothing the profile answers (it answers them itself, so a change to the profile is never hidden behind an
old copy -- such answers are not recorded in the first place).

The meaning is kept, not one portal's words: a Yes or No as Yes or No (the chosen "No, I do not have a disability" is
kept as "No"), a choice as chosen ("Masters Degree"), several ticks as "A; B". Whatever the next form draws -- a text
box, a list, a type-to-search list, Yes/No buttons, tick boxes -- the answer goes in the way the page agent puts any
answer in: through the one matcher (option_match), so "Masters Degree" still finds "Master's" and "No" finds "Never
been an employee"; dates never come from here.

When one question has several answers, the owner's wins over the agent's, and the newest over older ones; the others
are listed beside it. A learned answer the owner does not want is listed under "forgotten" and never learned again.
data/_answer_bank.json (git-ignored) is rebuilt from the tracker when a run starts and after each wait for the owner.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

import config
import option_match

logger = logging.getLogger(__name__)

BANK_FILE = config.DATA_DIR / "_answer_bank.json"
SENT = "submitted"
AGENT_SOURCES = ("verified_agent", "agent", "claude", "gemini", "openai", "ai")

# Words that change nothing about what is asked.
_FILLER = re.compile(r"\b(?:required|optional|please|kindly|select one|select all that apply|mark all that apply|"
                     r"check all that apply|choose one|pick one)\b")
# The words of a company's name that could name it inside a question (not "Inc", "Group", "Services" ...).
_GENERIC_NAME_WORDS = {"inc", "llc", "ltd", "corp", "corporation", "company", "co", "group", "holdings", "services",
                       "service", "solutions", "technologies", "technology", "systems", "international", "global",
                       "the", "and", "of", "us", "usa", "america", "american", "national", "bank", "health",
                       "healthcare", "insurance", "motors", "srvs", "job", "board", "careers"}
_DATE_QUESTION = re.compile(r"\bdate\b|\bwhen\b|\bstart\b|\bavailab", re.IGNORECASE)
# The same words known_answer keeps away from the owner's own answers: they are one entry's, or one employer's.
_ENTRY_QUESTION = re.compile(r"employer|company|school|university|college|institution|supervisor|reference|"
                             r"previous|this position|why (?:do|are) you|job title|position title|degree|"
                             r"area of study|field of study|major|gpa", re.IGNORECASE)
_YES_NO = re.compile(r"^(yes|no)\b(?=\s*(?:[,.(\-–—:;]|$))", re.IGNORECASE)
_MAX_WORDS = 12          # longer than this is writing for one form, not a reusable answer
_MIN_WORDS = 3           # "Month", "Year", "Location", "Spoken Level": parts of something larger, not a question
# What older runs recorded that is not an answer: a press ("open it", "focus it"), a form's internal field name.
_UI_ACTION = re.compile(r"^(?:open|focus|click|press|expand|close|toggle|scroll)(?:\s+it)?$", re.IGNORECASE)
_MACHINE_KEY = re.compile(r"^[\w.\-]+(?:,[\w.\-]+)+$")

_cache: dict[str, Any] = {"mtime": None, "bank": None}


def key(question: str) -> str:
    """The question as the bank files it: case, punctuation, 'required' and 'please' do not make it another one."""
    return " ".join(_FILLER.sub(" ", option_match.plain(question or "")).split())


def names_company(question: str, company: str) -> bool:
    """Whether the question names the employer ("Rubrik's", "Lucid, or any of its entities")."""
    words = option_match.plain(question).split()
    return any(word.startswith(name) for name in option_match.plain(company).split()
               if len(name) >= 4 and name not in _GENERIC_NAME_WORDS for word in words)


def meaning(answer: str) -> str:
    """What is kept of an answer: Yes or No where it starts with one ("No, I do not have a disability"), else itself."""
    text = " ".join((answer or "").split())
    m = _YES_NO.match(text)
    return m.group(1).capitalize() if m else text


def portable(question: str, answer: str, company: str = "") -> bool:
    """Whether an answer given on one application may be given on another."""
    import concept_matcher
    import form_fields
    import open_answers
    import safety
    from claude_integration import is_non_answer
    question, answer = (question or "").strip(), (answer or "").strip()
    if not question or not answer or len(key(question).split()) < _MIN_WORDS or _MACHINE_KEY.match(question):
        return False
    if _UI_ACTION.match(answer) or is_non_answer(answer):
        return False
    if safety.is_attestation(question) or safety.is_attestation(answer) or safety.is_legal_status_question(question) \
            or safety.is_privacy_consent(question):
        return False
    # The profile answers it, afresh each time (a name, work authorization, relocation, EEO ...).
    if concept_matcher.match_concept(question):
        return False
    # A box of one job or one degree ("Company", "College/University Name"): it belongs to that entry.
    if _ENTRY_QUESTION.search(question):
        return False
    if open_answers.is_open(question) or len(answer.split()) > _MAX_WORDS:
        return False
    if form_fields.parse_date(answer) is not None and _DATE_QUESTION.search(question):
        return False
    if company and names_company(question, company):
        return False
    return True


# ---------------------------------------------------------------------------------------------------------------
# Building it from the tracker
# ---------------------------------------------------------------------------------------------------------------
def _stamp(app) -> str:
    when = getattr(app, "updated_at", None) or getattr(app, "created_at", None)
    return when.isoformat() if hasattr(when, "isoformat") else str(when or "")


def rebuild(tracker, path: Path = BANK_FILE) -> int:
    """Reads every application's answers from the tracker and writes the bank. Returns how many questions it holds."""
    import concept_matcher
    old = _read(path)
    forgotten = set(old.get("forgotten") or [])
    entries: dict[str, dict] = {}
    for app in tracker.list_all():
        sent = str(getattr(app, "status", "")) == SENT
        try:
            rows = tracker.answers_for(app.id)
        except Exception as exc:
            logger.debug("ANSWER_BANK: could not read the answers of %s: %s", getattr(app, "company", "?"), exc)
            continue
        for row in rows:
            by = str(row.get("answered_by") or "").lower()
            source = "you" if by == "user" else "confirmed" if (sent and by in AGENT_SOURCES) else ""
            question, answer = row.get("question") or "", row.get("answer") or ""
            if not source or not portable(question, answer, getattr(app, "company", "") or ""):
                continue
            if source != "you" and concept_matcher.is_self_identification(question):
                continue                     # who the owner is: only their own answer is ever kept
            k = key(question)
            if not k or k in forgotten:
                continue
            candidate = {"question": " ".join(question.split())[:300], "value": meaning(answer), "source": source,
                         "when": _stamp(app), "companies": [getattr(app, "company", "") or ""],
                         "sites": [row.get("host") or ""], "others": []}
            current = entries.get(k)
            if current is None:
                entries[k] = candidate
                continue
            for field in ("companies", "sites"):
                current[field] = sorted(set(current[field]) | set(candidate[field]) - {""})
            better = (candidate["source"] == "you", candidate["when"]) > (current["source"] == "you", current["when"])
            if better:
                candidate["companies"], candidate["sites"] = current["companies"], current["sites"]
                candidate["others"] = sorted(set(current["others"]) | {current["value"]} - {candidate["value"]})
                entries[k] = candidate
            elif candidate["value"] != current["value"]:
                current["others"] = sorted(set(current["others"]) | {candidate["value"]})
    bank = {"_about": "Answers reused on every portal; rebuilt from the tracker (answer_bank.py). Put a question's "
                      "key under 'forgotten' to stop an answer being reused.",
            "forgotten": sorted(forgotten), "entries": dict(sorted(entries.items()))}
    _write(path, bank)
    return len(entries)


def _read(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=str(path.parent), prefix=".answer_bank_", suffix=".json")
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        json.dump(data, out, indent=1, ensure_ascii=False)
    os.replace(temp, path)
    _cache["mtime"] = None


# ---------------------------------------------------------------------------------------------------------------
# Asking it
# ---------------------------------------------------------------------------------------------------------------
def _bank(path: Path = BANK_FILE) -> dict:
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        return {}
    if _cache["mtime"] != (str(path), mtime):
        _cache["bank"], _cache["mtime"] = _read(path).get("entries") or {}, (str(path), mtime)
    return _cache["bank"]


def lookup(question: str, company: str = "", path: Path = BANK_FILE) -> Optional[dict]:
    """The bank's answer to this question, or None. The same questions are refused here as when it is built."""
    k = key(question)
    if not k or (company and names_company(question, company)):
        return None
    bank = _bank(path)
    entry = bank.get(k)
    if entry is None and len(k) >= 25:
        # A form that cuts a long question short asks the same question (the page agent's _same_question rule);
        # only when exactly one kept question fits.
        fits = [e for other, e in bank.items() if len(other) >= 25 and (other.startswith(k[:60]) or k.startswith(other[:60]))]
        entry = fits[0] if len(fits) == 1 else None
    return entry
