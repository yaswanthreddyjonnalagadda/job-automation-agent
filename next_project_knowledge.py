"""One JSON file for the next project: the failures the agent has met and the questions it had to leave.

The owner asked (25 September 2026) to keep a note of each failure and of each question the agent could not
answer, so the next project builds the fixes in from the start and starts with the answers. The failure notes
are one file each in reference/failures/ (committed); the questions are data/unanswered_questions.json (local:
they name the employers applied to). This puts both in one file:

    python next_project_knowledge.py                    # writes data/next_project_knowledge.json
    python next_project_knowledge.py --out somewhere.json

    failures                  every entry of reference/failures/, by id
    build_in_from_the_start   each failure's one-line prevention, to read first
    unanswered_questions      every question left for the owner, most often asked first, each with the
                              `answer_key` line that answers it in data/profile_answers.json
    counts, generated, about

The answers themselves stay in data/profile_answers.json: they are the owner's own facts.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)

FAILURES_DIR = Path("reference/failures")
OUTPUT_FILE = Path("data/next_project_knowledge.json")
ABOUT = ("What the job-application agent has got wrong and what to build in from the start (failures, "
         "build_in_from_the_start), and the questions it could not answer (unanswered_questions; the line "
         "that answers each is its answer_key).")


def _here(path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else config.BASE_DIR / path


def _failures(directory: Path) -> list[dict]:
    entries = []
    for path in sorted(directory.glob("f*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"{path.name} is not a readable failure entry: {exc}") from exc
        if isinstance(entry, dict) and entry.get("id"):
            entries.append(entry)
    return sorted(entries, key=lambda e: str(e["id"]))


def _questions(path: Path) -> list[dict]:
    """The questions the agent has left, most often asked first. A missing or unreadable log gives none:
    the failures are still worth having."""
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        logger.warning("Could not read %s -- leaving the questions out", path)
        return []
    if not isinstance(data, dict):
        return []
    asked = [{"key": key, **entry} for key, entry in data.items()
             if isinstance(entry, dict) and entry.get("question")]
    return sorted(asked, key=lambda q: (-int(q.get("times") or 0), str(q["question"]).lower()))


def build(failures_dir=None, unanswered_file=None, today: Optional[str] = None) -> dict:
    if unanswered_file is None:
        import page_agent
        unanswered_file = page_agent.UNANSWERED_FILE
    failures = _failures(_here(failures_dir or FAILURES_DIR))
    asked = _questions(_here(unanswered_file))
    return {
        "about": ABOUT,
        "generated": today or date.today().isoformat(),
        "counts": {"failures": len(failures), "unanswered_questions": len(asked)},
        "build_in_from_the_start": [{"id": f["id"], "class": f.get("class", ""), "prevention": f.get("prevention", "")}
                                    for f in failures],
        "failures": failures,
        "unanswered_questions": asked,
    }


def write(out=None, **kwargs) -> Path:
    path = _here(out or OUTPUT_FILE)
    text = json.dumps(build(**kwargs), indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(path.name + ".tmp")
    scratch.write_text(text, encoding="utf-8")
    scratch.replace(path)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default=str(OUTPUT_FILE), help="where to write the file")
    parser.add_argument("--failures", default=str(FAILURES_DIR), help="the folder of failure entries")
    parser.add_argument("--questions", default=None, help="the unanswered-questions log")
    args = parser.parse_args(argv)
    path = write(args.out, failures_dir=args.failures, unanswered_file=args.questions)
    data = json.loads(path.read_text(encoding="utf-8"))
    print(f"{path}: {data['counts']['failures']} failures, {data['counts']['unanswered_questions']} questions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
