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
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

FILE = Path("data/field_timings.jsonl")
logger = logging.getLogger("field_timing")
_LABEL_MAX = 80
_lock = threading.Lock()
_run = ""
_reason = threading.local()


def why(reason: str) -> None:
    """Says why the step now being timed went the way it did (a short phrase; never a value)."""
    _reason.text = " ".join(str(reason or "").split())[:_LABEL_MAX * 2]


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
        reason = getattr(_reason, "text", "")
        _reason.text = ""
        if reason:
            line["why"] = reason
        target = path()
        with _lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug("field_timing: could not record: %s", str(exc).splitlines()[0][:100] if str(exc) else type(exc).__name__)


def timed(kind: str, label: Callable[..., str] = lambda *a, **k: "", min_seconds: float = 0.0) -> Callable:
    """Times a method. `label(*args, **kwargs)` names the field (never its value); a False return is 'failed'."""
    def decorate(function: Callable) -> Callable:
        @functools.wraps(function)
        def inner(*args: Any, **kwargs: Any) -> Any:
            started, result = time.monotonic(), "error"
            _reason.text = ""
            try:
                answer = function(*args, **kwargs)
                result = "failed" if answer is False else "ok"
                return answer
            finally:
                try:
                    name = label(*args, **kwargs)
                except Exception:
                    name = ""
                took = time.monotonic() - started
                if took >= min_seconds:
                    record(kind, name, took, result)
        return inner
    return decorate
