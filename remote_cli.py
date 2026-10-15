"""VM-side command line used by the laptop over SSH.

Every command prints exactly one JSON document on stdout, so the laptop can
queue jobs and read state without exposing any network port or sharing a
SQLite file across machines. Run from the project directory on the worker:

    python remote_cli.py enqueue --url URL [--key KEY] [--priority N]
    python remote_cli.py jobs [--limit N]
    python remote_cli.py workers
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from durable_queue import DurableJobQueue
from worker_manager import WorkerRegistry

BASE_DIR = Path(__file__).resolve().parent


def run(argv: list[str], base_dir: Path = BASE_DIR) -> dict:
    parser = argparse.ArgumentParser(prog="remote_cli")
    sub = parser.add_subparsers(dest="command", required=True)
    enq = sub.add_parser("enqueue")
    enq.add_argument("--url", required=True)
    enq.add_argument("--key", default=None)
    enq.add_argument("--priority", type=int, default=0)
    jobs = sub.add_parser("jobs")
    jobs.add_argument("--limit", type=int, default=50)
    sub.add_parser("workers")
    args = parser.parse_args(argv)

    data_dir = base_dir / "data"
    if args.command == "enqueue":
        queue = DurableJobQueue(data_dir / "job_queue.db")
        job = queue.enqueue(
            job_url=args.url,
            application_key=args.key,
            priority=args.priority,
            job_type="application",
            notes="Dispatched from laptop over SSH",
        )
        return {"ok": True, "job_id": job.job_id}
    if args.command == "jobs":
        queue = DurableJobQueue(data_dir / "job_queue.db")
        return {"ok": True, "jobs": [j.as_dict() for j in queue.list_jobs(limit=args.limit)]}
    registry = WorkerRegistry(data_dir)
    return {"ok": True, "workers": [w.as_dict() for w in registry.list_all()]}


def main() -> int:
    try:
        print(json.dumps(run(sys.argv[1:]), default=str))
        return 0
    except SystemExit:
        raise
    except Exception as exc:  # report as JSON so the laptop can show the cause
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
