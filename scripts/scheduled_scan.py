"""One discovery scan for a scheduler (f317).

Task Scheduler or cron runs this between dashboard visits so the freshness
views have something fresh to show:

    python scripts/scheduled_scan.py [--db PATH] [--companies PATH] [--threshold N]

Prints one summary line (boards scanned, jobs saved, boards failed) and exits
1 only when the scan itself fails; failed boards are recorded per board and
do not fail the run.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from job_discovery import JobDiscoveryEngine  # noqa: E402


def run(db_path=None, config_path=None, threshold: int = 50) -> dict:
    engine = JobDiscoveryEngine(
        db_path=Path(db_path) if db_path else None,
        config_path=Path(config_path) if config_path else None,
    )
    return engine.scan_all(min_match_threshold=threshold)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run one job-board scan.")
    parser.add_argument("--db", default=None, help="applications database (default: the project's)")
    parser.add_argument("--companies", default=None, help="target companies file (default: the project's)")
    parser.add_argument("--threshold", type=int, default=50, help="minimum match score to store")
    args = parser.parse_args(argv)
    try:
        stats = run(args.db, args.companies, args.threshold)
    except Exception as exc:
        print(f"scan failed: {exc}")
        return 1
    print(f"boards={stats.get('companies_scanned', 0)} "
          f"saved={stats.get('relevant_jobs_saved', 0)} "
          f"failed={stats.get('boards_failed', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
