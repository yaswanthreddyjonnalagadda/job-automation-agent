"""How reliable the agent is, run by run: measured, by job site and by code version.

Architecture review, 1 October: two weeks of runs were described by anecdotes. progress.py counts applications
by status; this counts runs, and what each one cost the owner:

    reached_review     the run got the application to its Review page
    owner_stops        how many times it stopped for the owner, and why (the first line of each reason)
    minutes            wall-clock time from start to the run's end
    ai_calls           model calls (answers, page reading, documents), with failures and seconds
    resume             how a reopened page was judged (checkpoint.reconcile verdict), when the run resumed
    unknown_outcomes   actions found pending from an earlier run (an unseen Submit)
    outcome            how the run ended: review, submitted, needs_you, skipped, ended, error
    site, code         the job site (progress.site_of) and the agent's code version (checkpoint.code_version)

One JSON line per run in data/run_metrics.jsonl (local, git-ignored), written when the run ends -- and on every
stop, so a run that is killed still leaves its numbers. `summary()` groups them for the Progress page;
`python run_metrics.py` prints the same.
"""
from __future__ import annotations

import atexit
import json
import logging
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

FILE = Path("data/run_metrics.jsonl")
_run: Optional[dict] = None
_started_at = 0.0


def start(key: str, url: str, code: str, resumed: bool = False) -> None:
    global _run, _started_at
    import progress
    _started_at = time.time()
    _run = {"run": uuid.uuid4().hex[:12], "key": key, "site": progress.site_of(url), "code": code,
            "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "resumed": bool(resumed), "resume": "",
            "reached_review": False, "owner_stops": 0, "stop_reasons": [], "ai_calls": 0, "ai_failures": 0,
            "ai_seconds": 0.0, "unknown_outcomes": 0, "outcome": "ended", "minutes": 0.0}
    atexit.register(finish)


def _active() -> bool:
    return _run is not None


def ai_call(provider: str, seconds: float, ok: bool) -> None:
    if _active():
        _run["ai_calls"] += 1
        _run["ai_failures"] += 0 if ok else 1
        _run["ai_seconds"] = round(_run["ai_seconds"] + max(0.0, seconds), 1)


def owner_stop(reason: str) -> None:
    if _active():
        _run["owner_stops"] += 1
        first = " ".join((reason or "").split(" | ")[0].split())[:120]
        if first:
            _run["stop_reasons"].append(first)
        _write()


def resume(verdict: str) -> None:
    if _active():
        _run["resume"] = verdict


def unknown_outcome() -> None:
    if _active():
        _run["unknown_outcomes"] += 1


def outcome(kind: str) -> None:
    """How the run stands: review / submitted / needs_you / skipped / error."""
    if _active():
        _run["outcome"] = kind
        if kind in ("review", "submitted"):
            _run["reached_review"] = True
        _write()


def _write() -> None:
    if not _active():
        return
    _run["minutes"] = round((time.time() - _started_at) / 60, 1)
    try:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        rows = [r for r in load() if r.get("run") != _run["run"]]   # a run's line is replaced, not repeated
        rows.append(dict(_run))
        FILE.write_text("".join(json.dumps(r) + "\n" for r in rows[-5000:]), encoding="utf-8")
    except OSError as exc:                       # numbers that cannot be written never stop a run
        logger.debug("Could not write the run metrics: %s", exc)


def finish() -> None:
    global _run
    if _active():
        _write()
        _run = None


def load(path: Optional[Path] = None) -> list[dict]:
    try:
        lines = Path(path or FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _group(rows: list[dict]) -> dict:
    n = len(rows)
    review = sum(1 for r in rows if r.get("reached_review"))
    alone = sum(1 for r in rows if r.get("reached_review") and not r.get("owner_stops"))
    resumed = [r for r in rows if r.get("resumed")]
    return {
        "runs": n,
        "reached_review": review,
        "without_you": alone,
        "rate_review": f"{round(100 * review / n)}%" if n else "-",
        "rate_without_you": f"{round(100 * alone / n)}%" if n else "-",
        "stops_per_run": round(sum(r.get("owner_stops", 0) for r in rows) / n, 1) if n else 0,
        "minutes_per_run": round(sum(r.get("minutes", 0) for r in rows) / n, 1) if n else 0,
        "ai_calls_per_run": round(sum(r.get("ai_calls", 0) for r in rows) / n, 1) if n else 0,
        "ai_failure_rate": (f"{round(100 * sum(r.get('ai_failures', 0) for r in rows) / max(1, sum(r.get('ai_calls', 0) for r in rows)))}%"
                            if any(r.get("ai_calls") for r in rows) else "-"),
        "resumes": len(resumed),
        "resumes_on_the_application": sum(1 for r in resumed if r.get("resume") in ("same_application", "posting")),
        "unknown_outcomes": sum(r.get("unknown_outcomes", 0) for r in rows),
    }


def summary(rows: Optional[list[dict]] = None) -> dict:
    """All runs, then by job site and by code version, and the commonest reasons for stopping."""
    rows = load() if rows is None else rows
    by_site, by_code = defaultdict(list), defaultdict(list)
    reasons: Counter = Counter()
    for r in rows:
        by_site[r.get("site") or "?"].append(r)
        by_code[r.get("code") or "?"].append(r)
        reasons.update(r.get("stop_reasons") or [])
    return {
        "all": _group(rows),
        "by_site": {k: _group(v) for k, v in sorted(by_site.items(), key=lambda kv: -len(kv[1]))},
        "by_code": {k: _group(v) for k, v in sorted(by_code.items(), key=lambda kv: -len(kv[1]))[:10]},
        "stop_reasons": reasons.most_common(10),
    }


if __name__ == "__main__":
    data = summary()
    a = data["all"]
    print(f"{a['runs']} runs: {a['rate_review']} reached Review, {a['rate_without_you']} without needing you; "
          f"{a['stops_per_run']} stops, {a['minutes_per_run']} min and {a['ai_calls_per_run']} AI calls per run; "
          f"{a['unknown_outcomes']} unknown outcomes")
    for title, groups in (("By site", data["by_site"]), ("By code version", data["by_code"])):
        print(f"\n{title}:")
        for name, g in groups.items():
            print(f"  {name:28} {g['runs']:4} runs  review {g['rate_review']:>4}  alone {g['rate_without_you']:>4}  "
                  f"stops {g['stops_per_run']:>4}  min {g['minutes_per_run']:>5}")
    print("\nWhy it stopped for you:")
    for reason, n in data["stop_reasons"]:
        print(f"  {n:4}  {reason}")
