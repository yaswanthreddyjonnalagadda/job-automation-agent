"""Compares the legacy job_discovery feed against the new job-source service's `open_jobs`
view, read-only on both sides. Card 7 ("switch the agent over, behind a switch that stays
OFF"): before `JOB_SOURCE` is ever flipped to "service", this answers "how different would
the suggested-jobs list actually be?"

Writes one entry per day to data/_source_compare.json (a JSON array), replacing that day's
entry if the script is run again the same day rather than piling up duplicates. Jobs are
matched across the two sources by normalised company + title + location, since the two
sources use different id spaces.

    python scripts/compare_sources.py [--days 7] [--min-score 60]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import diagnostics
import job_source_client

logger = logging.getLogger("compare_sources")

DEFAULT_OUT_PATH = ROOT / "data" / "_source_compare.json"
MAX_LISTED = 20


def _norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _match_key(company: Any, title: Any, location: Any) -> str:
    return f"{_norm(company)}|{_norm(title)}|{_norm(location)}"


def fetch_old_jobs(days: int, min_score: int) -> List[Dict[str, Any]]:
    """Read-only: the legacy job_discovery feed, posted within the window, scored at
    least min_score."""
    from job_discovery import JobDiscoveryEngine
    engine = JobDiscoveryEngine()
    return engine.get_discovered_jobs(
        min_score=min_score, limit=1000, hide_applied=False, within_hours=days * 24,
    )


def fetch_new_jobs(days: int, min_score: int) -> List[Dict[str, Any]]:
    """Read-only: the new job-source service's open_jobs view."""
    return job_source_client.get_open_jobs(min_score=min_score, days=days, limit=1000)


def compare(old_jobs: List[Dict[str, Any]], new_jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """{old_count, new_count, only_in_old, only_in_new, both} -- matched by normalised
    company + title + location, each only_in_* list capped at MAX_LISTED, alphabetical."""
    old_by_key: Dict[str, Dict[str, Any]] = {}
    for job in old_jobs:
        key = _match_key(job.get("company_name"), job.get("job_title"), job.get("location"))
        old_by_key.setdefault(key, job)
    new_by_key: Dict[str, Dict[str, Any]] = {}
    for job in new_jobs:
        key = _match_key(job.get("company_name"), job.get("job_title"), job.get("location"))
        new_by_key.setdefault(key, job)

    old_keys = set(old_by_key)
    new_keys = set(new_by_key)

    def _label(job: Dict[str, Any]) -> str:
        return f"{job.get('company_name') or ''} | {job.get('job_title') or ''}"

    only_in_old = sorted(_label(old_by_key[k]) for k in (old_keys - new_keys))[:MAX_LISTED]
    only_in_new = sorted(_label(new_by_key[k]) for k in (new_keys - old_keys))[:MAX_LISTED]

    return {
        "old_count": len(old_jobs),
        "new_count": len(new_jobs),
        "only_in_old": only_in_old,
        "only_in_new": only_in_new,
        "both": len(old_keys & new_keys),
    }


def write_entry(entry: Dict[str, Any], out_path: Path) -> None:
    """Appends `entry` to the JSON array at out_path, replacing any existing entry for the
    same date so re-running on the same day does not pile up duplicates."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    existing: List[Dict[str, Any]] = []
    if out_path.exists():
        try:
            loaded = json.loads(out_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                existing = loaded
        except (OSError, ValueError) as exc:
            logger.warning("Could not read existing comparisons at %s: %s",
                            out_path, diagnostics.short_exc(exc, 100))
            existing = []
    existing = [e for e in existing if e.get("date") != entry["date"]]
    existing.append(entry)
    out_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")


def run(days: int = 7, min_score: int = 60, out_path: Optional[Path] = None) -> Dict[str, Any]:
    """Runs the comparison and writes the entry; returns it (with its date) for callers/tests."""
    out_path = out_path or DEFAULT_OUT_PATH
    old_jobs = fetch_old_jobs(days, min_score)
    new_jobs = fetch_new_jobs(days, min_score)
    entry = {"date": datetime.now(timezone.utc).date().isoformat(), **compare(old_jobs, new_jobs)}
    write_entry(entry, out_path)
    return entry


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--min-score", type=int, default=60)
    args = parser.parse_args(argv)

    entry = run(days=args.days, min_score=args.min_score)
    logger.info(
        "old=%d new=%d both=%d only_in_old=%d only_in_new=%d -> %s",
        entry["old_count"], entry["new_count"], entry["both"],
        len(entry["only_in_old"]), len(entry["only_in_new"]), DEFAULT_OUT_PATH,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
