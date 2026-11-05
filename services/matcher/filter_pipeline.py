"""
Typed 3-Valued Job Filtering Pipeline.
Added by ECC: Evaluates jobs against SearchSpec using Pass / Fail / Unknown verdicts with exact reasons.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from services.matcher.search_spec import SearchSpec

logger = logging.getLogger("filter_pipeline")


class FilterVerdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass
class FilterResult:
    filter_name: str
    verdict: FilterVerdict
    reason: str
    penalty: float = 0.0


@dataclass
class PipelineOutcome:
    passed: bool
    score: float
    results: List[FilterResult] = field(default_factory=list)
    rejection_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "score": round(self.score, 1),
            "rejection_reasons": self.rejection_reasons,
            "details": [
                {
                    "filter": r.filter_name,
                    "verdict": r.verdict.value,
                    "reason": r.reason,
                    "penalty": r.penalty,
                }
                for r in self.results
            ],
        }


def parse_salary_to_annual(raw: Any) -> Tuple[Optional[int], Optional[int]]:
    """
    Parses annual and hourly salary strings/numbers into (min_annual, max_annual).
    Understands:
      - '$135k' -> (135000, 135000)
      - '$120,000 - $150,000' -> (120000, 150000)
      - '$65 - $75 per hour' or '$65/hr' -> hourly * 2080
    """
    if raw is None:
        return None, None

    if isinstance(raw, (int, float)):
        val = int(raw)
        return (val * 2080, val * 2080) if val < 500 else (val, val)

    text = str(raw).strip().lower()
    if not text or text in ("competitive", "doe", "negotiable", "tbd"):
        return None, None

    is_hourly = bool(re.search(r"\b(?:hr|hour|hourly|per hour|/hr)\b", text))

    # Match '$130k' or '$130k - $160k'
    k_matches = re.findall(r"\$?\s*(\d+(?:\.\d+)?)\s*k\b", text)
    if k_matches:
        vals = [int(float(x) * 1000) for x in k_matches]
        return (min(vals), max(vals))

    # Match raw dollar numbers '$120,000'
    num_matches = re.findall(r"\$?\s*(\d[\d,]+(?:\.\d+)?)\b", text)
    if num_matches:
        parsed_nums: List[float] = []
        for s in num_matches:
            clean = s.replace(",", "")
            try:
                parsed_nums.append(float(clean))
            except ValueError:
                pass

        if parsed_nums:
            multiplier = 2080 if (is_hourly or max(parsed_nums) < 500) else 1
            annual_vals = [int(v * multiplier) for v in parsed_nums]
            return (min(annual_vals), max(annual_vals))

    return None, None


class TitleFilter:
    def __init__(self, spec: SearchSpec):
        self.spec = spec
        self.hard = spec.hard_criteria.get("title", True)

    def evaluate(self, job: Dict[str, Any]) -> FilterResult:
        title = (job.get("title") or "").strip()
        if not title:
            return FilterResult(
                filter_name="title",
                verdict=FilterVerdict.FAIL if self.hard else FilterVerdict.UNKNOWN,
                reason="Job title is empty",
                penalty=40.0,
            )

        title_lower = title.lower()

        # Check negative patterns
        for neg in self.spec.excluded_keywords:
            if re.search(rf"\b{re.escape(neg.lower())}\b", title_lower):
                return FilterResult(
                    filter_name="title",
                    verdict=FilterVerdict.FAIL,
                    reason=f"Title matched negative keyword '{neg}'",
                    penalty=100.0,
                )

        # Exact or partial match with wanted titles + synonyms + adjacent
        wanted = self.spec.all_wanted_titles()
        for w in wanted:
            if w.lower() in title_lower:
                return FilterResult(
                    filter_name="title",
                    verdict=FilterVerdict.PASS,
                    reason=f"Title matches target role '{w}'",
                )

        # Check token overlap
        title_tokens = set(re.findall(r"\w+", title_lower))
        for w in wanted:
            w_tokens = set(re.findall(r"\w+", w.lower()))
            if w_tokens and w_tokens.issubset(title_tokens):
                return FilterResult(
                    filter_name="title",
                    verdict=FilterVerdict.PASS,
                    reason=f"Title tokens overlap target role '{w}'",
                )

        # If not matched
        if self.hard:
            return FilterResult(
                filter_name="title",
                verdict=FilterVerdict.FAIL,
                reason=f"Title '{title}' does not match any approved target roles",
                penalty=50.0,
            )
        else:
            return FilterResult(
                filter_name="title",
                verdict=FilterVerdict.UNKNOWN,
                reason=f"Title '{title}' is an adjacent or unlisted role",
                penalty=20.0,
            )


class LocationFilter:
    NOVA_KEYWORDS = {
        "fairfax", "arlington", "reston", "ashburn", "herndon", "tysons",
        "mclean", "alexandria", "northern virginia", "nova", "dc metro", "dmv",
        "loudoun", "sterling", "falls church", "vienna", "virginia",
    }

    def __init__(self, spec: SearchSpec):
        self.spec = spec
        self.hard = spec.hard_criteria.get("location", True)

    def evaluate(self, job: Dict[str, Any]) -> FilterResult:
        loc = (job.get("location") or "").strip()
        title = (job.get("title") or "").strip()
        combined = f"{title} {loc}".lower()

        # Remote detection
        is_remote = bool(
            job.get("remote")
            or job.get("is_remote")
            or re.search(r"\b(remote|anywhere|telecommute|virtual|work from home)\b", combined)
        )
        if is_remote:
            return FilterResult(
                filter_name="location",
                verdict=FilterVerdict.PASS,
                reason="Workplace is remote / work-from-home",
            )

        if not loc:
            return FilterResult(
                filter_name="location",
                verdict=FilterVerdict.UNKNOWN,
                reason="Location not specified in posting (passes as unknown)",
                penalty=0.0,
            )

        loc_lower = loc.lower()
        # Northern Virginia / DC Metro check
        if any(term in loc_lower for term in self.NOVA_KEYWORDS):
            return FilterResult(
                filter_name="location",
                verdict=FilterVerdict.PASS,
                reason=f"Location matches preferred region ({loc})",
            )

        # Check explicit target locations
        for target_loc in self.spec.locations:
            clean_tgt = target_loc.lower().split(",")[0].strip()
            if clean_tgt and clean_tgt != "remote" and clean_tgt in loc_lower:
                return FilterResult(
                    filter_name="location",
                    verdict=FilterVerdict.PASS,
                    reason=f"Location matches target '{target_loc}'",
                )

        if self.hard:
            return FilterResult(
                filter_name="location",
                verdict=FilterVerdict.FAIL,
                reason=f"Location '{loc}' is outside preferred regions",
                penalty=30.0,
            )
        else:
            return FilterResult(
                filter_name="location",
                verdict=FilterVerdict.UNKNOWN,
                reason=f"Location '{loc}' is outside primary targets (soft penalty applied)",
                penalty=15.0,
            )


class SalaryFilter:
    def __init__(self, spec: SearchSpec):
        self.spec = spec
        self.hard = spec.hard_criteria.get("salary", False)
        self.floor = spec.salary_floor_annual or 0

    def evaluate(self, job: Dict[str, Any]) -> FilterResult:
        min_ann, max_ann = parse_salary_to_annual(job.get("salary_max") or job.get("salary_min"))
        if min_ann is None and max_ann is None:
            # Check description for embedded salary
            desc = job.get("description") or ""
            min_ann, max_ann = parse_salary_to_annual(desc)

        if min_ann is None and max_ann is None:
            return FilterResult(
                filter_name="salary",
                verdict=FilterVerdict.UNKNOWN,
                reason="Salary not listed in posting (neutral)",
                penalty=0.0,
            )

        effective_max = max_ann or min_ann or 0
        if self.floor > 0 and effective_max < self.floor:
            if self.hard:
                return FilterResult(
                    filter_name="salary",
                    verdict=FilterVerdict.FAIL,
                    reason=f"Maximum compensation ${effective_max:,} is below required floor ${self.floor:,}",
                    penalty=30.0,
                )
            else:
                return FilterResult(
                    filter_name="salary",
                    verdict=FilterVerdict.PASS,
                    reason=f"Salary ${effective_max:,} is below target ${self.floor:,} (soft penalty)",
                    penalty=20.0,
                )

        return FilterResult(
            filter_name="salary",
            verdict=FilterVerdict.PASS,
            reason=f"Salary ${effective_max:,} satisfies floor ${self.floor:,}",
        )


class SponsorshipFilter:
    DISQUALIFIERS = [
        "security clearance",
        "top secret",
        "ts/sci",
        "polygraph",
        "public trust",
        "us citizen only",
        "u.s. citizen only",
        "no sponsorship",
        "cannot sponsor",
        "unable to sponsor",
        "without sponsorship",
    ]

    def __init__(self, spec: SearchSpec):
        self.spec = spec
        self.hard = spec.hard_criteria.get("sponsorship", True)

    def evaluate(self, job: Dict[str, Any]) -> FilterResult:
        full_text = f"{job.get('title', '')} {job.get('location', '')} {job.get('description', '')}".lower()

        for dq in self.DISQUALIFIERS:
            if dq in full_text:
                return FilterResult(
                    filter_name="sponsorship",
                    verdict=FilterVerdict.FAIL if self.hard else FilterVerdict.UNKNOWN,
                    reason=f"Posting contains disqualifier: '{dq}'",
                    penalty=100.0,
                )

        return FilterResult(
            filter_name="sponsorship",
            verdict=FilterVerdict.PASS,
            reason="No citizenship or clearance disqualifiers found",
        )


class JobFilterPipeline:
    def __init__(self, spec: SearchSpec):
        self.spec = spec
        self.filters = [
            TitleFilter(spec),
            LocationFilter(spec),
            SalaryFilter(spec),
            SponsorshipFilter(spec),
        ]
        self._funnel_stats: Dict[str, int] = {
            "total_evaluated": 0,
            "passed": 0,
            "failed": 0,
            "rejected_by_title": 0,
            "rejected_by_location": 0,
            "rejected_by_salary": 0,
            "rejected_by_sponsorship": 0,
        }

    def evaluate(self, job: Dict[str, Any]) -> PipelineOutcome:
        self._funnel_stats["total_evaluated"] += 1
        results: List[FilterResult] = []
        rejections: List[str] = []
        score = 100.0

        for f in self.filters:
            res = f.evaluate(job)
            results.append(res)
            score -= res.penalty
            if res.verdict == FilterVerdict.FAIL:
                rejections.append(res.reason)
                stat_key = f"rejected_by_{res.filter_name}"
                if stat_key in self._funnel_stats:
                    self._funnel_stats[stat_key] += 1

        passed = len(rejections) == 0
        if passed:
            self._funnel_stats["passed"] += 1
        else:
            self._funnel_stats["failed"] += 1

        final_score = max(0.0, min(100.0, score))
        return PipelineOutcome(
            passed=passed,
            score=final_score,
            results=results,
            rejection_reasons=rejections,
        )

    def get_funnel_report(self) -> Dict[str, int]:
        return self._funnel_stats.copy()
