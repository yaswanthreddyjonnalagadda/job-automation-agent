"""Who may change an application's status, in one place for every tracker.

An application that has gone -- submitted, and then interviewing, an offer, or rejected -- stays gone. A run of
the agent may not move it back to "needs you" or "filling in": on 29 September the owner marked Aristocrat
submitted while its run was still going, and the run set it back to "needs you" a minute later. The owner can
still change any status from the dashboard (by_owner=True): it is the owner's record.
"""
from __future__ import annotations

DONE = frozenset({"submitted", "interviewing", "offer", "rejected"})


def may_replace(current: str | None, new: str, by_owner: bool = False) -> bool:
    """Whether `new` may replace `current`."""
    if by_owner or not current:
        return True
    return not (current.lower() in DONE and (new or "").lower() not in DONE)
