"""Shows where the last application's time went, from data/field_timings.jsonl (see field_timing.py).

    python scripts/field_timings.py            # the last run
    python scripts/field_timings.py verizon    # the last run whose name contains this
    python scripts/field_timings.py --top 25
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import field_timing  # noqa: E402


def load(file: Path) -> list[dict]:
    rows = []
    for line in file.read_text(encoding="utf-8").splitlines() if file.exists() else ():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def last_run(rows: list[dict], match: str = "") -> list[dict]:
    """The rows of the most recent run (a run's lines are together; a new run starts when the name changes)."""
    rows = [r for r in rows if match.lower() in str(r.get("run", "")).lower()]
    if not rows:
        return []
    name, start = rows[-1].get("run"), len(rows) - 1
    while start > 0 and rows[start - 1].get("run") == name:
        start -= 1
    return rows[start:]


def report(rows: list[dict], top: int = 15) -> str:
    if not rows:
        return "No timings recorded yet (they are written from the next application on)."
    by_kind: dict[str, float] = defaultdict(float)
    for r in rows:
        by_kind[r["kind"]] += float(r["seconds"])
    began = datetime.fromisoformat(rows[0]["at"])
    ended = datetime.fromisoformat(rows[-1]["at"])
    lines = [f"Run: {rows[0].get('run') or '(unnamed)'}",
             f"First step {rows[0]['at']}  last step {rows[-1]['at']}  "
             f"({(ended - began).total_seconds():.0f} s on the clock, {sum(by_kind.values()):.0f} s in timed steps)", "",
             "Time by kind of step:"]
    lines += [f"  {kind:<8} {secs:7.1f} s" for kind, secs in sorted(by_kind.items(), key=lambda kv: -kv[1])]
    lines += ["", f"Slowest {top} steps:"]
    for r in sorted(rows, key=lambda r: -float(r["seconds"]))[:top]:
        lines.append(f"  {float(r['seconds']):7.1f} s  {r['kind']:<8} {r['result']:<7} {r['label']}"
                     + (f"   [{r['why']}]" if r.get("why") else ""))
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("match", nargs="?", default="")
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args()
    print(report(last_run(load(field_timing.path()), args.match), args.top))
