"""Is the agent working? The funnel from started to interview, by site, and why runs stop.

Two weeks of runs left no answer to the only question that matters: how many applications got through, and did
any lead anywhere. This reads what the tracker already keeps (statuses, notes, the hand-over record) and, once
the person marks what happened after submitting (interviewing, rejected, offer), shows the whole way through.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from urllib.parse import urlparse

SUBMITTED_OR_LATER = {"submitted", "interviewing", "rejected", "offer"}
REPLIED = {"interviewing", "rejected", "offer"}
INTERVIEWS = {"interviewing", "offer"}
REACHED_REVIEW = {"ready_to_submit"} | SUBMITTED_OR_LATER
SKIPPED = {"skipped", "DISQUALIFIED_POLICY_MISMATCH"}

# Job sites, told apart by their hosts; anything else is shown by its own domain.
SITES = (("myworkdayjobs", "Workday"), ("greenhouse", "Greenhouse"), ("lever.co", "Lever"), ("ashbyhq", "Ashby"),
         ("icims", "iCIMS"), ("dayforce", "Dayforce"), ("adp.com", "ADP"), ("oraclecloud", "Oracle"),
         ("successfactors", "SuccessFactors"), ("sapsf", "SuccessFactors"), ("paylocity", "Paylocity"),
         ("bamboohr", "BambooHR"), ("smartrecruiters", "SmartRecruiters"), ("eightfold", "Eightfold"),
         ("taleo", "Taleo"), ("linkedin", "LinkedIn"))


def site_of(url: str) -> str:
    host = urlparse(url or "").netloc.lower()
    for mark, name in SITES:
        if mark in host:
            return name
    parts = [p for p in host.split(".") if p and p not in ("www", "jobs", "careers", "apply")]
    return ".".join(parts[-2:]) if parts else "unknown"


def _reason(note: str) -> str:
    """A stop reason without the details that make each one unique (numbers, quoted values)."""
    text = re.sub(r"'[^']*'|\"[^\"]*\"", "'…'", note or "")
    text = re.sub(r"\d+", "N", text)
    return " ".join(text.split())[:110]


@dataclass
class Funnel:
    started: int = 0
    reached_review: int = 0
    submitted: int = 0
    replied: int = 0
    interviews: int = 0
    skipped: int = 0
    stuck: int = 0
    by_site: dict = field(default_factory=dict)       # site -> {"started", "reached_review", "submitted"}
    stop_reasons: list = field(default_factory=list)   # (reason, count), most common first

    def rate(self, part: int, whole: int) -> str:
        return f"{round(100 * part / whole)}%" if whole else "-"


def funnel(records, reached_hand_over=frozenset()) -> Funnel:
    """The funnel over tracker records. `reached_hand_over` holds the dedup keys of applications the agent
    carried to its hand-over at the Review page (the tracker's auto_submit record), whatever their status now."""
    result = Funnel()
    sites: dict = defaultdict(lambda: {"started": 0, "reached_review": 0, "submitted": 0})
    reasons: Counter = Counter()
    for record in records:
        status = getattr(record, "status", "") or ""
        if status in SKIPPED:
            result.skipped += 1
            continue
        site = sites[site_of(getattr(record, "url", "") or "")]
        result.started += 1
        site["started"] += 1
        reached = status in REACHED_REVIEW or getattr(record, "dedup_key", None) in reached_hand_over
        if reached:
            result.reached_review += 1
            site["reached_review"] += 1
        if status in SUBMITTED_OR_LATER:
            result.submitted += 1
            site["submitted"] += 1
        if status in REPLIED:
            result.replied += 1
        if status in INTERVIEWS:
            result.interviews += 1
        if status == "needs_user_review" and not reached:
            result.stuck += 1
            note = (getattr(record, "notes", "") or "").split(" | ")[0]
            if note:
                reasons[_reason(note)] += 1
    result.by_site = dict(sorted(sites.items(), key=lambda kv: -kv[1]["started"]))
    result.stop_reasons = reasons.most_common(8)
    return result
