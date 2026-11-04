"""One-off repair for f329: queued rows carried the discovery hash, runs use the URL hash.

Every run keys its handoffs, notes and checkpoints by the posting URL's hash
(``jd_analyzer.dedup_key_for_url``), but rows queued before the fix carry the
discovery hash (company|title|location|url) -- so a run's status updates land
on a key with no row and silently vanish. This moves every runnable row with a
URL onto the run's key. Safe to re-run: rows already on the run key are left
alone, and a row whose run key is taken by another row is reported and
skipped, never merged.

Usage (outside the sandbox -- the live database is read-only in it):
    python scripts/rekey_f329.py [--db data/applications.db] [--apply]
Without --apply it only reports what it would change.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from jd_analyzer import dedup_key_for_url  # noqa: E402

RUNNABLE = ("ready_to_apply", "needs_user_review", "pending", "queued", "running", "waiting", "stopped")


def plan(conn: sqlite3.Connection) -> tuple[list[tuple], list[tuple]]:
    rows = conn.execute(
        f"SELECT id, company, title, status, dedup_key, url FROM applications WHERE status IN ({','.join('?' * len(RUNNABLE))})",
        RUNNABLE,
    ).fetchall()
    move, clash = [], []
    for row in rows:
        url = (row["url"] or "").strip()
        if not url:
            continue  # a row with no URL can never run; nothing to unify
        want = dedup_key_for_url(url)
        if row["dedup_key"] == want:
            continue
        taken = conn.execute(
            "SELECT id FROM applications WHERE dedup_key = ? AND id != ?", (want, row["id"])
        ).fetchone()
        (clash if taken else move).append((row["id"], row["company"], row["title"], row["dedup_key"], want))
    return move, clash


def main() -> int:
    parser = argparse.ArgumentParser(description="f329: unify queued rows onto the run (URL) key.")
    parser.add_argument("--db", default=str(BASE_DIR / "data" / "applications.db"))
    parser.add_argument("--apply", action="store_true", help="write the changes (default: report only)")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    move, clash = plan(conn)
    for row_id, company, title, _old, new in move:
        print(f"move   id={row_id} {company} -- {title} -> {new[:12]}")
    for row_id, company, title, _old, new in clash:
        print(f"CLASH  id={row_id} {company} -- {title}: run key {new[:12]} already taken, skipped")
    if not args.apply:
        print(f"report only: {len(move)} to move, {len(clash)} clashes. Re-run with --apply to write.")
        return 0
    if not move:
        print("nothing to move.")
        return 0
    backup = args.db + ".pre-f329-rekey"
    shutil.copy2(args.db, backup)
    print(f"backup: {backup}")
    stamp = f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] f329 re-key onto the run (URL) key."
    for row_id, _c, _t, _old, new in move:
        conn.execute("UPDATE applications SET dedup_key = ? WHERE id = ?", (new, row_id))
        prior = conn.execute("SELECT notes FROM applications WHERE id = ?", (row_id,)).fetchone()[0] or ""
        conn.execute("UPDATE applications SET notes = ? WHERE id = ?", (f"{prior}\n{stamp}".strip(), row_id))
    conn.commit()
    left, _ = plan(conn)
    assert not left, f"re-key incomplete: {[r[0] for r in left]}"
    print(f"moved {len(move)} row(s); re-run is clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
