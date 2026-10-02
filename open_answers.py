"""Open questions answered in the owner's own plain words.

"Why are you interested in joining WRITER?" and "Give an example from your experience that aligns with
our values" were left blank, or answered with one stiff sentence. The owner's rule (28 September 2026):
write them from his profile and resume in plain, simple English that a non-native speaker understands on
first reading -- no AI wording -- and keep to any word or character limit the form sets.

How an answer is made:
  1. The limit is read from the question, the hint text beside the box ("max 150 words", "0/500") and the
     box's own maxlength. With no limit, a short default for the kind of question is used.
  2. The model is asked for one answer, with the style rules and the facts it may use: the resume, the
     profile, and the job posting (for what is said about the company). Nothing else.
  3. The draft is checked here, not trusted: words that sound like AI (reference/plain_english.json), long
     sentences, dashes, lists, exclamation marks, and length. Simple swaps ("utilize" -> "use") are made in
     code; anything else goes back to the model once, with the problems named.
  4. The answer is cut to fit a stated limit at a sentence end -- never mid-sentence, never over.
What is left after that is reported, and the owner reads every written answer before he submits.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

STYLE_FILE = Path(__file__).parent / "reference" / "plain_english.json"


@lru_cache(maxsize=1)
def style() -> dict:
    return json.loads(STYLE_FILE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Which questions are open, and what kind
# ---------------------------------------------------------------------------
_OPEN = re.compile(
    r"\b(why|describe|tell us|explain|example|share|how would you|how do you|how have you|walk us through|"
    r"what (makes|draws|excites|interests|motivates|attracts)|additional information|anything else|"
    r"cover letter|in your own words)\b", re.IGNORECASE)
_EXAMPLE = re.compile(
    r"\b(example|a time when|a situation|tell us about a time|describe a (time|situation|project|challenge)|"
    r"share (an|a) (example|experience|story))\b", re.IGNORECASE)
_WHY = re.compile(r"\bwhy\b|interest(ed)? in|what (draws|attracts|excites|motivates|interests)|motivat",
                  re.IGNORECASE)
_LIST = re.compile(r"^\W*(?:please\s+)?list\b|\b(?:which|what|any)\s+(?:certifications?|licen[cs]es?|languages|"
                   r"tools|technologies|software|programming languages|clearances?)\b|\bname (?:the|any|your)\b",
                   re.IGNORECASE)


def is_open(question: str, multiline: bool = False) -> bool:
    """A question answered in the owner's words, not with a fact or a choice."""
    return bool(multiline or _OPEN.search(question or ""))


def kind(question: str) -> str:
    # A question that asks for a list is answered with the list: "List any certifications you have" in a large box
    # was written up as a paragraph about experience, padded to the essay minimum (SK AX USA, ADP, 30 September).
    if _LIST.search(question or ""):
        return "list"
    if _EXAMPLE.search(question or ""):
        return "example"
    if _WHY.search(question or ""):
        return "why"
    return "other"


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------
@dataclass
class Limits:
    min_words: Optional[int] = None
    max_words: Optional[int] = None
    max_chars: Optional[int] = None

    def target(self, question_kind: str) -> tuple[int, int]:
        """How many words to aim for: the default for the kind, inside what the form allows."""
        lo, hi = style()["target_words"].get(question_kind, style()["target_words"]["other"])
        if self.min_words:
            lo = max(lo, self.min_words)
            hi = max(hi, int(self.min_words * 1.25))
        if self.max_words:
            hi = min(hi, max(int(self.max_words * 0.9), 1))
        if self.max_chars:
            hi = min(hi, max(int(self.max_chars * 0.9 / 6.5), 1))    # ~6.5 characters a word, with spaces
        return min(lo, hi), hi


_NUM = r"(\d{1,3}(?:,\d{3})+|\d+)"
_UNITS = {"words": r"words?", "chars": r"(?:characters?|chars?)"}


def _n(text: str) -> int:
    return int(text.replace(",", ""))


def limits_from(*texts: str, maxlength: Optional[int] = None, minlength: Optional[int] = None) -> Limits:
    """The limits a form states, in its words or on the box itself."""
    text = " ".join(t for t in texts if t)
    found = {"words": [None, None], "chars": [None, None]}           # [min, max]
    for unit, word in _UNITS.items():
        ranges = [rf"{_NUM}\s*(?:-|–|to)\s*{_NUM}\s*{word}\b", rf"between\s*{_NUM}\s*and\s*{_NUM}\s*{word}\b"]
        for pattern in ranges:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                found[unit] = [_n(m.group(1)), _n(m.group(2))]
        if found[unit][1] is not None:
            continue                                   # a stated range says it all
        maxima = [
            rf"(?:max(?:imum)?|up to|no more than|not (?:to )?exceed(?:ing)?|under|less than|fewer than|"
            rf"limit(?:ed)?(?: to| of)?|within|at most|about|around|approximately)\s*:?\s*(?:of\s*)?{_NUM}\s*{word}\b",
            rf"{_NUM}\s*{word}\s*(?:max(?:imum)?|or less|or fewer|limit|at most)\b",
            rf"\(\s*{_NUM}\s*{word}\s*\)",
            rf"\bin\s*{_NUM}\s*{word}\b",
        ]
        for pattern in maxima:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                value = _n(m.group(1))
                found[unit][1] = value if found[unit][1] is None else min(found[unit][1], value)
        for m in re.finditer(rf"(?:at least|minimum(?: of)?|min\.?|no (?:less|fewer) than)\s*{_NUM}\s*{word}\b",
                             text, re.IGNORECASE):
            found[unit][0] = _n(m.group(1))
    # "0/500" under a box. Three digits at least, so a date ("9/28") or "24/7" is not taken for one.
    counter = re.search(r"(?<![\d/])\d{1,5}\s*/\s*(\d{3,5})\b(?!\s*/)(?!\s*words?)", text)
    max_chars = [v for v in (found["chars"][1], maxlength if maxlength and maxlength > 0 else None,
                             _n(counter.group(1)) if counter else None) if v]
    min_words = found["words"][0]
    if minlength and minlength > 0 and not min_words:
        min_words = max(int(minlength / 6.5), 1)
    return Limits(min_words=min_words, max_words=found["words"][1], max_chars=min(max_chars) if max_chars else None)


# ---------------------------------------------------------------------------
# Checking a draft
# ---------------------------------------------------------------------------
def words(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][\w'’.-]*", text or ""))


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.?])\s+", (text or "").strip()) if s]


def _phrase(phrase: str) -> re.Pattern:
    return re.compile(rf"(?<![\w-]){re.escape(phrase)}(?![\w-])", re.IGNORECASE)


def tidy(text: str) -> str:
    """What code can put right without changing what is said."""
    text = (text or "").strip().strip('"').strip()
    text = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", text, flags=re.MULTILINE)       # list markers
    text = re.sub(r"[*_#`]+", "", text)                                              # markdown
    text = re.sub(r"\s*[—–]\s*|\s+-\s+", ", ", text)                                 # dashes between words
    text = text.replace(";", ".").replace("!", ".")
    for old, new in style().get("replace", {}).items():
        text = _phrase(old).sub(lambda m, new=new: new[0].upper() + new[1:] if m.group(0)[0].isupper() else new,
                                text)
    text = re.sub(r"\.\s+([a-z])", lambda m: ". " + m.group(1).upper(), text)       # after a ; became .
    text = re.sub(r"\s+", " ", text).replace(" ,", ",").replace(" .", ".")
    return text.strip()


def problems(text: str, limits: Limits, question_kind: str = "other") -> list[str]:
    """Why a draft is not yet plain English in the owner's voice, or not the right length."""
    found = []
    avoided = [p for p in style()["avoid"] if _phrase(p).search(text or "")]
    if avoided:
        found.append("uses words that sound written by AI: " + ", ".join(f'"{p}"' for p in avoided))
    longest = style()["max_sentence_words"]
    long_ones = [s for s in sentences(text) if words(s) > longest]
    if long_ones:
        found.append(f"{len(long_ones)} sentence(s) longer than {longest} words: split them")
    if re.search(r"[—–;!•]|\n\s*[-*]|[\U0001F300-\U0001FAFF]", text or ""):
        found.append("uses dashes, semicolons, exclamation marks, lists or emoji")
    count = words(text)
    lo, hi = limits.target(question_kind)
    if limits.min_words and count < limits.min_words:
        found.append(f"{count} words, the form asks for at least {limits.min_words}")
    elif count < int(lo * 0.7):
        found.append(f"only {count} words: aim for {lo} to {hi}")
    if count > hi:
        found.append(f"{count} words: keep it to {hi} or fewer")
    if limits.max_chars and len(text) > limits.max_chars:
        found.append(f"{len(text)} characters, the box takes {limits.max_chars}")
    return found


def fit(text: str, limits: Limits) -> str:
    """Cut to a stated limit at a sentence end; a first sentence that is itself too long is cut at a word."""
    def over(t: str) -> bool:
        return bool((limits.max_words and words(t) > limits.max_words)
                    or (limits.max_chars and len(t) > limits.max_chars))
    parts = sentences(text)
    while len(parts) > 1 and over(" ".join(parts)):
        parts.pop()
    text = " ".join(parts)
    if over(text):
        kept = text.split()
        while len(kept) > 1 and over(" ".join(kept).rstrip(",;:.?") + "."):
            kept.pop()
        text = " ".join(kept).rstrip(",;:.?") + "."
    return text


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def _system(question_kind: str, lo: int, hi: int) -> str:
    shape = {
        "why": "Give one or two real reasons. Tie each to something in the job posting and to something the "
               "applicant has actually done.",
        "example": "Tell ONE real example from the resume: what the situation was, what the applicant did, "
                   "and what happened because of it. Use a number from the resume if there is one.",
        "list": "Give only the items asked for, as a short comma-separated list taken from the resume and the "
                "applicant's details -- no sentences about experience. If the resume shows none, write: None.",
        "other": "Answer only what is asked.",
    }[question_kind]
    avoid = ", ".join(f'"{p}"' for p in style()["avoid"])
    return f"""You write one answer on a job application, as the applicant, in the first person.

Write plain, simple English that a person who speaks English as a second language understands the first time
they read it. It must sound like a real person wrote it, not an AI.

Rules:
1. Use ONLY facts from RESUME and PROFILE about the applicant, and ONLY facts from JOB POSTING and QUESTION
   about the company. Never invent a project, a number, a tool, a feeling about the company, or anything the
   company does that the posting does not say.
2. Short sentences: most under 15 words, none over {style()["max_sentence_words"]}. Common everyday words.
   No idioms, no metaphors, no big claims.
3. Be specific: name a real tool, system, project or result from the resume.
4. Do not repeat the question. Do not start with "I am excited", "As a" or the company's name.
5. No lists, headings, dashes, semicolons, exclamation marks, quotes or emoji. One paragraph.
6. Never use these words or phrases: {avoid}.
7. {shape}
8. Length: {lo} to {hi} words.

Respond with ONLY JSON: {{"answer": "<the answer>"}}"""


def _answer_from(raw: str) -> str:
    raw = (raw or "").strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return str(json.loads(match.group(0)).get("answer") or "").strip()
        except (ValueError, AttributeError):
            pass
    return raw


def _badness(text: str, found: list[str]) -> tuple[int, int]:
    """AI wording counts first -- the owner's first rule -- then everything else."""
    return sum(1 for p in style()["avoid"] if _phrase(p).search(text)), len(found)


def write(question: str, *, ask: Callable[[str, str, int], str], facts: str, resume_text: str,
          job_text: str = "", hint: str = "", limits: Optional[Limits] = None) -> tuple[str, list[str]]:
    """The answer to an open question, and what is still wrong with it ([] when nothing).

    `ask(system, user_message, max_tokens)` calls the model. An answer the model could not give is "".
    """
    limits = limits or limits_from(question, hint)
    question_kind = kind(question)
    lo, hi = limits.target(question_kind)
    system = _system(question_kind, lo, hi)
    user = (f"PROFILE:\n{facts}\n\nRESUME:\n{(resume_text or '').strip()[:6000]}\n\n"
            f"JOB POSTING:\n{(job_text or '').strip()[:4000]}\n\n"
            f"QUESTION: {question}\n" + (f"TEXT BESIDE THE BOX: {hint}\n" if hint else ""))
    best, best_problems = "", ["no answer"]
    feedback = ""
    for _attempt in range(2):
        draft = tidy(_answer_from(ask(system, user + feedback, 1_000)))
        if not draft:
            continue
        found = problems(draft, limits, question_kind)
        if not best or _badness(draft, found) < _badness(best, best_problems):
            best, best_problems = draft, found
        if not found:
            break
        feedback = ("\nYOUR LAST ANSWER:\n" + draft + "\n\nFIX THESE PROBLEMS AND WRITE IT AGAIN:\n- "
                    + "\n- ".join(found) + "\n")
    if not best:
        return "", best_problems
    final = fit(best, limits)
    left = problems(final, limits, question_kind)
    if left:
        logger.info("OPEN_ANSWER: %r still has: %s", question[:50], "; ".join(left))
    return final, left


# ---------------------------------------------------------------------------
# Reading the box on the page
# ---------------------------------------------------------------------------
_READ_BOX = """el => {
  const n = v => { const x = parseInt(v, 10); return Number.isFinite(x) && x > 0 ? x : null; };
  let hint = '';
  const described = (el.getAttribute('aria-describedby') || '').split(/\\s+/).filter(Boolean)
    .map(id => (document.getElementById(id) || {}).innerText || '').join(' ');
  let box = el.parentElement;
  for (let i = 0; i < 4 && box; i++, box = box.parentElement) {
    const text = (box.innerText || '').trim();
    if (text.length > 20) { hint = text; break; }
  }
  return {multiline: el.tagName === 'TEXTAREA' || el.isContentEditable,
          maxlength: n(el.getAttribute('maxlength')), minlength: n(el.getAttribute('minlength')),
          hint: (described + ' ' + hint).trim().slice(0, 800)};
}"""


def read_box(locator) -> dict:
    """What the page says about a text box: multi-line or not, its length limits, the text around it."""
    try:
        found = locator.evaluate(_READ_BOX, timeout=3_000) or {}
    except Exception:
        found = {}
    return {"multiline": bool(found.get("multiline")), "maxlength": found.get("maxlength"),
            "minlength": found.get("minlength"), "hint": str(found.get("hint") or "")}
