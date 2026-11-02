"""Four-stage match scoring for a job posting against the owner's profile (P3-2).

    1. hard gates   -- sponsorship / citizenship / clearance / onsite elsewhere
    2. lexical      -- profile skills found in the posting (required 3x, preferred 1x)
    3. semantic     -- cosine similarity of posting and resume text
    4. LLM fit      -- optional, injected; only ever a judgement, never a gate

Nothing here reads a profile file, calls a model or opens a network
connection on its own: the profile, the resume text and the optional model
callable are passed in. The sponsorship decision is `safety.no_sponsorship_statement`
(one decision, one place); skills and places come from the profile.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import safety

_VOCABULARY = Path(__file__).resolve().parent / "reference" / "skills.json"

_CLEARANCE = re.compile(
    r"\b(?:security clearance|ts/sci|top secret|secret clearance|active clearance)\b"
    r"[^.!?\n]{0,40}\b(?:required|must|needed|necessary)\b"
    r"|\b(?:requires?|must (?:have|hold)|active)\b[^.!?\n]{0,30}"
    r"\b(?:security clearance|ts/sci|top secret|secret clearance)\b",
    re.IGNORECASE,
)
_ONSITE = re.compile(r"\b(?:on-?site|in[- ]office|in[- ]person)\b", re.IGNORECASE)
_REMOTE = re.compile(r"\bremote\b|\bwork from home\b|\banywhere\b", re.IGNORECASE)
_PREFERRED_HEAD = re.compile(
    r"^\W*(?:preferred|nice to have|bonus|a plus|desired|good to have|"
    r"preferred qualifications|what would make you stand out)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)
_REQUIRED_HEAD = re.compile(
    r"^\W*(?:requirements?|qualifications?|minimum qualifications|must have|"
    r"what you(?:'ll)? need|what we(?:'re| are) looking for|basic qualifications)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)
_STOP = frozenset(
    "a an and are as at be by for from has have in is it its of on or our that the "
    "their this to will with you your we they not can who all any more such other".split()
)

WEIGHTS_WITH_LLM = {"lexical": 0.40, "semantic": 0.20, "llm": 0.40}
WEIGHTS_WITHOUT_LLM = {"lexical": 0.65, "semantic": 0.35}


@dataclass
class MatchResult:
    overall_score: int
    is_eligible: bool
    disqualification_reason: Optional[str] = None
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    stage_breakdown: dict[str, Any] = field(default_factory=dict)
    summary: str = ""


def _get(profile: Any, key: str, default: Any = None) -> Any:
    if isinstance(profile, dict):
        return profile.get(key, default)
    return getattr(profile, key, default)


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9+#.]+", (text or "").lower()) if t not in _STOP and len(t) > 1]


def check_hard_gates(
    jd_text: str, profile: Any, job_location: str = ""
) -> tuple[bool, Optional[str]]:
    """(eligible, reason). Reason is None when eligible."""
    text = jd_text or ""
    if _get(profile, "requires_visa_sponsorship", False):
        clause = safety.no_sponsorship_statement(text)
        if clause:
            return False, f"Posting excludes sponsorship: {clause}"
    clearance = _CLEARANCE.search(text)
    if clearance:
        return False, f"Posting requires a security clearance: {clearance.group(0).strip()[:120]}"

    places = [str(p) for p in (_get(profile, "locations", []) or [])]
    where = f"{job_location} {text[:400]}" if job_location else ""
    if places and job_location and not _REMOTE.search(where):
        wanted = [p.lower().split(",")[0].strip() for p in places if p.strip()]
        in_reach = any(w and w in job_location.lower() for w in wanted)
        if not in_reach and not any(w.lower() == "remote" for w in wanted):
            return False, f"Not remote and outside the owner's locations: {job_location.strip()[:80]}"
        if not in_reach and _ONSITE.search(text):
            return False, f"Onsite outside the owner's locations: {job_location.strip()[:80]}"
    return True, None


def split_sections(jd_text: str) -> tuple[str, str]:
    """(required_text, preferred_text). Without headings everything is required."""
    text = jd_text or ""
    pref = _PREFERRED_HEAD.search(text)
    if not pref:
        return text, ""
    return text[: pref.start()], text[pref.start():]


def _has_skill(skill: str, text: str) -> bool:
    s = skill.strip().lower()
    if not s:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(s) + r"(?![a-z0-9])", text.lower()) is not None


def load_vocabulary() -> list[str]:
    """Skill terms to look for in a posting (reference/skills.json)."""
    try:
        with open(_VOCABULARY, encoding="utf-8") as handle:
            return [str(s) for s in json.load(handle).get("skills", [])]
    except (OSError, ValueError):
        return []


def score_lexical(
    jd_text: str,
    resume_text: str,
    skills: list[str],
    vocabulary: Optional[list[str]] = None,
) -> tuple[float, list[str], list[str]]:
    """(0-100, matched, missing). The skills the posting asks for are the
    vocabulary terms (plus the owner's own skills) it names: required 3x,
    preferred 1x. The owner has a skill when the resume or profile names it."""
    required, preferred = split_sections(jd_text)
    vocab = load_vocabulary() if vocabulary is None else vocabulary
    owned = f"{resume_text or ''} {' '.join(skills)}"
    matched: list[str] = []
    missing: list[str] = []
    earned = possible = 0
    for skill in dict.fromkeys(s.strip() for s in [*vocab, *skills] if s and s.strip()):
        weight = 3 if _has_skill(skill, required) else 1 if _has_skill(skill, preferred) else 0
        if not weight:
            continue
        possible += weight
        if _has_skill(skill, owned):
            earned += weight
            matched.append(skill)
        else:
            missing.append(skill)
    if not possible:
        return 0.0, matched, missing
    return 100.0 * earned / possible, matched, missing


def score_semantic(
    jd_text: str,
    resume_text: str,
    embed: Optional[Callable[[str], list[float]]] = None,
) -> float:
    """Cosine similarity, 0-100. `embed` swaps in a dense embedding; the default
    is a bag-of-words cosine with no dependency."""
    if embed is not None:
        a, b = embed(jd_text), embed(resume_text)
    else:
        ca, cb = Counter(_tokens(jd_text)), Counter(_tokens(resume_text))
        keys = set(ca) | set(cb)
        a, b = [ca[k] for k in keys], [cb[k] for k in keys]
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return 100.0 * dot / norm if norm else 0.0


def _parse_llm(raw: Any) -> Optional[dict]:
    if isinstance(raw, dict):
        data = raw
    else:
        found = re.search(r"\{.*\}", str(raw or ""), re.DOTALL)
        if not found:
            return None
        try:
            data = json.loads(found.group(0))
        except ValueError:
            return None
    try:
        score = float(data.get("score"))
    except (TypeError, ValueError):
        return None
    return {"score": max(0.0, min(100.0, score)), "summary": str(data.get("summary") or "").strip()}


def synthesize_fit(
    jd_text: str,
    profile: Any,
    sub_scores: dict[str, float],
    llm: Optional[Callable[[str], Any]] = None,
    resume_text: str = "",
) -> tuple[int, str, dict[str, float]]:
    """(overall, summary, sub_scores). `llm` takes a prompt and returns JSON
    text or a dict {"score": 0-100, "summary": str}. A failed or malformed
    reply drops the stage rather than inventing a number."""
    scores = dict(sub_scores)
    summary = ""
    if llm is not None:
        prompt = (
            "Rate how well this candidate fits the job from 0 to 100, using only "
            "the resume text given. Reply JSON: {\"score\": n, \"summary\": "
            "\"2-3 sentences\"}.\n\nJOB:\n" + (jd_text or "")[:6000]
            + "\n\nRESUME:\n" + (resume_text or "")[:6000]
            + "\n\nSTAGE SCORES: " + json.dumps(sub_scores)
        )
        try:
            parsed = _parse_llm(llm(prompt))
        except Exception:
            parsed = None
        if parsed:
            scores["llm"] = parsed["score"]
            summary = parsed["summary"]
    weights = WEIGHTS_WITH_LLM if "llm" in scores else WEIGHTS_WITHOUT_LLM
    overall = sum(scores.get(k, 0.0) * w for k, w in weights.items())
    return int(round(max(0.0, min(100.0, overall)))), summary, scores


def evaluate(
    jd_text: str,
    profile: Any,
    resume_text: str = "",
    *,
    job_location: str = "",
    llm: Optional[Callable[[str], Any]] = None,
    embed: Optional[Callable[[str], list[float]]] = None,
) -> MatchResult:
    """Run all four stages. A failed gate short-circuits to score 0."""
    eligible, reason = check_hard_gates(jd_text, profile, job_location)
    if not eligible:
        return MatchResult(
            overall_score=0, is_eligible=False, disqualification_reason=reason,
            stage_breakdown={"gate": 0},
            summary=f"Disqualified before scoring: {reason}",
        )
    skills = [str(s) for s in (_get(profile, "skills", []) or [])]
    lexical, matched, missing = score_lexical(jd_text, resume_text, skills)
    semantic = score_semantic(jd_text, resume_text, embed)
    overall, llm_summary, scores = synthesize_fit(
        jd_text, profile, {"lexical": lexical, "semantic": semantic}, llm, resume_text
    )
    summary = llm_summary or (
        f"Matches {len(matched)} of {len(matched) + len(missing)} skills the posting names"
        + (f"; missing {', '.join(missing[:5])}." if missing else ".")
    )
    return MatchResult(
        overall_score=overall, is_eligible=True, matched_skills=matched,
        missing_skills=missing, stage_breakdown={"gate": 100, **{k: round(v) for k, v in scores.items()}},
        summary=summary,
    )
