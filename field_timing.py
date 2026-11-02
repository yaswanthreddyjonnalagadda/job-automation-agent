"""Where an application's time goes: how long each field, list search, model call and page read took.

The owner, 2 November 2026: "track the time taken to fill each field in the job application so that we can know where the
application is taking so long". One line per timed step is appended to data/field_timings.jsonl -- local, like
data/unanswered_questions.json. A line holds the run, the kind of step, the field's label and the seconds; it never
holds an answer, a value or a page. Timing can never stop or slow an application: any failure to record is swallowed.

`python scripts/field_timings.py` shows the slowest steps of the last run.
"""
from __future__ import annotations

import functools
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

FILE = Path("data/field_timings.jsonl")
_LABEL_MAX = 80
_lock = threading.Lock()
_run = ""


def begin(company: str = "", title: str = "") -> None:
    """Names the run the next lines belong to."""
    global _run
    _run = " | ".join(part.strip()[:60] for part in (company, title) if part and part.strip())


def path() -> Path:
    return Path(os.environ.get("JAA_FIELD_TIMINGS") or FILE)


def record(kind: str, label: str, seconds: float, result: str = "ok") -> None:
    try:
        line = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "run": _run, "kind": kind,
                "label": " ".join(str(label or "").split())[:_LABEL_MAX], "seconds": round(seconds, 2), "result": result}
        target = path()
        with _lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        pass


def timed(kind: str, label: Callable[..., str] = lambda *a, **k: "") -> Callable:
    """Times a method. `label(*args, **kwargs)` names the field (never its value); a False return is 'failed'."""
    def decorate(function: Callable) -> Callable:
        @functools.wraps(function)
        def inner(*args: Any, **kwargs: Any) -> Any:
            started, result = time.monotonic(), "error"
            try:
                answer = function(*args, **kwargs)
                result = "failed" if answer is False else "ok"
                return answer
            finally:
                try:
                    name = label(*args, **kwargs)
                except Exception:
                    name = ""
                record(kind, name, time.monotonic() - started, result)
        return inner
    return decorate
