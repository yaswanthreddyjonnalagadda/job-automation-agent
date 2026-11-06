"""Board Approval CLI.
Allows inspecting pending boards and explicitly approving (sets status 'live')
or rejecting (sets status 'paused').
"""
from __future__ import annotations

import argparse
from typing import List, Optional

from services.diff_engine.storage import JobStorage


def list_pending_boards(storage: Optional[JobStorage] = None) -> List[dict]:
    """List pending boards sorted with best matches (highest job count) first."""
    store = storage or JobStorage()
    pending = store.boards(status="pending")
    # Sort best matches (most jobs) first
    pending.sort(key=lambda b: (b.get("total_jobs_last") or 0), reverse=True)

    print(f"\nPending Boards for Review ({len(pending)} total):")
    print(f"{'ID':<6} {'Provider':<16} {'Total Jobs':<12} {'Board Identifier'}")
    print("-" * 70)
    for b in pending:
        bid = b.get("id", "")
        prov = b.get("ats_platform", "")
        jobs = b.get("total_jobs_last", 0)
        slug = b.get("board_slug", "")
        print(f"{bid:<6} {prov:<16} {jobs:<12} {slug}")
    print()
    return pending


def run_approve_action(
    storage: Optional[JobStorage] = None,
    approve_ids: Optional[List[int]] = None,
    reject_ids: Optional[List[int]] = None,
) -> None:
    """Approve or reject boards by ID."""
    store = storage or JobStorage()

    if approve_ids:
        for bid in approve_ids:
            store.set_board_status(int(bid), "live")
            print(f"Board ID {bid} status set to 'live'")

    if reject_ids:
        for bid in reject_ids:
            store.set_board_status(int(bid), "paused")
            print(f"Board ID {bid} status set to 'paused'")


def main() -> None:
    parser = argparse.ArgumentParser(description="Review, approve, or reject candidate ATS boards.")
    parser.add_argument("--list", action="store_true", help="List all pending boards (best matches first)")
    parser.add_argument("--approve", nargs="+", type=int, help="Approve board IDs (sets status to 'live')")
    parser.add_argument("--reject", nargs="+", type=int, help="Reject board IDs (sets status to 'paused')")
    parser.add_argument("--db", default=None, help="SQLite/Postgres DSN")
    args = parser.parse_args()

    store = JobStorage(dsn=args.db) if args.db else None

    if args.list:
        list_pending_boards(store)
    elif args.approve or args.reject:
        run_approve_action(storage=store, approve_ids=args.approve, reject_ids=args.reject)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
