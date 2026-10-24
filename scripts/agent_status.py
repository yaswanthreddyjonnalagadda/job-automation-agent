"""Multi-agent pre-flight status check for Claude Code, Codex, and Antigravity.

Run at the start and end of every coding session:
    python scripts/agent_status.py

Reports in <0.5s (with zero network calls and no Postgres timeout):
1. Current git branch, ahead/behind state, and recent commits
2. Uncommitted work (staged, unstaged, untracked) so agents never overwrite each other
3. Pending git stashes and unmerged local branches
4. Latest failure catalogue ID (fNNN) and the next available ID
5. Pending applications needing review/recovery in data/applications.db + latest saved page run
6. Open backlog items from docs/AGENT_SYNC.md
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _git(*args: str) -> str:
    try:
        res = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        return res.stdout.strip()
    except Exception as exc:
        return f"<git error: {exc}>"


def main() -> int:
    print("=" * 76)
    print("JOB AUTOMATION AGENT -- MULTI-AGENT STATUS (Claude Code | Codex | Antigravity)")
    print("=" * 76)

    # 1. Branch & Recent Commits
    branch = _git("branch", "--show-current") or "(detached HEAD)"
    tracking = _git("status", "-sb").splitlines()[0] if _git("status", "-sb") else branch
    print(f"\n[1] BRANCH & RECENT COMMITS: {tracking}")
    for line in _git("log", "-n", "5", "--oneline").splitlines():
        print(f"    {line}")

    # 2. Uncommitted Work (Working Tree & Index)
    status_lines = [
        line for line in _git("status", "-s").splitlines()
        if not line.strip().endswith(".claude/")
    ]
    print(f"\n[2] UNCOMMITTED WORK IN WORKING TREE ({len(status_lines)} item(s)):")
    if status_lines:
        print("    WARNING: Uncommitted changes present! Inspect `git diff` before editing:")
        for line in status_lines:
            print(f"      {line}")
    else:
        print("    Working tree is clean.")

    # 3. Pending Branches & Stashes
    unmerged = _git("branch", "--no-merged", "HEAD").splitlines()
    stashes = _git("stash", "list").splitlines()
    print(f"\n[3] PENDING BRANCHES ({len(unmerged)}) & STASHES ({len(stashes)}):")
    for b in unmerged[:8]:
        print(f"    unmerged branch: {b.strip()}")
    for s in stashes[:5]:
        print(f"    {s.strip()}")

    # 4. Failure Catalogue State
    failures_dir = ROOT / "reference" / "failures"
    f_nums: list[tuple[int, str]] = []
    if failures_dir.is_dir():
        for p in failures_dir.glob("f*.json"):
            m = re.match(r"^f(\d+)", p.name)
            if m:
                f_nums.append((int(m.group(1)), p.name))
    f_nums.sort()
    if f_nums:
        last_num, last_name = f_nums[-1]
        print(f"\n[4] FAILURE CATALOGUE: Latest = f{last_num:03d} ({last_name}) -> NEXT ID = f{last_num + 1:03d}")
    else:
        print("\n[4] FAILURE CATALOGUE: none found")

    # 5. Pending Application Runs & Unanswered Questions (Fast SQLite direct read)
    db_path = ROOT / "data" / "applications.db"
    print("\n[5] APPLICATION TRACKER & LATEST STOPPED RUNS:")
    if db_path.is_file():
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
            rows = conn.execute(
                "SELECT status, company, title, COALESCE(notes, '') "
                "FROM applications ORDER BY updated_at DESC"
            ).fetchall()
            conn.close()
            pending = [
                r for r in rows
                if r[0] not in ("submitted", "skipped", "already_applied", "job_not_found")
            ]
            print(f"    Total tracked applications: {len(rows)} | Pending/Active: {len(pending)}")
            for status, company, title, notes in pending[:8]:
                short_note = " ".join(notes.split())[:110]
                print(f"    * [{status}] {company} -- {title}")
                if short_note:
                    print(f"      reason: {short_note}")
        except Exception as exc:
            print(f"    (Could not read data/applications.db: {exc})")

    out_dir = ROOT / "output"
    if out_dir.is_dir():
        page_runs = sorted(
            out_dir.glob("*/pages/*"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )
        if page_runs:
            latest_run = page_runs[0]
            pages = sorted(p.name for p in latest_run.glob("page_*.txt"))
            print(f"    Latest saved page run: {latest_run.relative_to(ROOT)} ({len(pages)} pages: {', '.join(pages[-4:])})")

    uq_path = ROOT / "data" / "unanswered_questions.json"
    if uq_path.is_file():
        try:
            uq_data = json.loads(uq_path.read_text(encoding="utf-8"))
            items = uq_data.get("questions", uq_data) if isinstance(uq_data, dict) else uq_data
            if isinstance(items, dict):
                items = list(items.values())
            unanswered = [q for q in items if isinstance(q, dict) and not q.get("answer")]
            print(f"    Unanswered questions in data/unanswered_questions.json: {len(unanswered)} unanswered / {len(items)} total")
        except Exception:
            pass

    # 6. Pending Shared Backlog in docs/AGENT_SYNC.md
    sync_doc = ROOT / "docs" / "AGENT_SYNC.md"
    if sync_doc.is_file():
        open_tasks = [
            line.strip()[6:]
            for line in sync_doc.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("- [ ] ")
        ]
        print(f"\n[6] SHARED BACKLOG IN docs/AGENT_SYNC.md ({len(open_tasks)} open item(s)):")
        for task in open_tasks:
            print(f"    - [ ] {task}")

    print("=" * 76)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

