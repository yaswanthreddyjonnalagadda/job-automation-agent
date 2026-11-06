"""Report-only live check for ATS board readers.

Run as:
    python -m services.poller.check --provider greenhouse --board <slug>
    python -m services.poller.check --provider greenhouse --limit 5

Calls read_<provider>_board directly and prints one line per board:
    provider, board, postings, complete, error, seconds

WRITES NOTHING: no database writes, no file writes. Opens the database read-only
when --limit is used to select board slugs from company_directory.

Supported providers: greenhouse, lever, ashby, smartrecruiters, bamboohr,
and workday / icims if their modules expose read_workday_board / read_icims_board.
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Callable, Dict, List, Optional

# Import reader functions into module namespace so tests can monkeypatch them.
from services.poller.greenhouse import read_greenhouse_board
from services.poller.lever import read_lever_board
from services.poller.ashby import read_ashby_board
from services.poller.smartrecruiters import read_smartrecruiters_board
from services.poller.bamboohr import read_bamboohr_board

try:
    from services.poller.workday import read_workday_board  # type: ignore[assignment]
except (ImportError, AttributeError):  # pragma: no cover
    read_workday_board = None  # type: ignore[assignment]

try:
    from services.poller.icims import read_icims_board  # type: ignore[assignment]
except (ImportError, AttributeError):  # pragma: no cover
    read_icims_board = None  # type: ignore[assignment]


# Map provider name → (module-level attr name, board_id_field)
# We resolve reader_fn via getattr(module, attr_name) at call time so that
# tests can monkeypatch the module attribute and the change is picked up.
_PROVIDER_MAP: Dict[str, tuple] = {
    "greenhouse":      ("read_greenhouse_board",       "board_slug"),
    "lever":           ("read_lever_board",             "board_slug"),
    "ashby":           ("read_ashby_board",             "board_slug"),
    "smartrecruiters": ("read_smartrecruiters_board",   "board_slug"),
    "bamboohr":        ("read_bamboohr_board",           "board_slug"),
    "workday":         ("read_workday_board",            "board_url"),
    "icims":           ("read_icims_board",              "board_url"),
}

import sys as _sys
_THIS_MODULE = _sys.modules[__name__]


def _get_reader(provider: str) -> Optional[Callable]:
    """Resolve the reader function for *provider* from this module at call time.

    Using getattr here (rather than a prebuilt dict of callables) lets tests
    monkeypatch the module-level name and have the change take effect.
    """
    if provider not in _PROVIDER_MAP:
        return None
    attr_name, _ = _PROVIDER_MAP[provider]
    fn = getattr(_THIS_MODULE, attr_name, None)
    return fn  # may be None if the optional reader was not importable


def _open_storage_readonly():
    """Open JobStorage in read-only SQLite mode from the default DB path.

    Kept as a named function so tests can monkeypatch it cleanly.
    Writes NOTHING — only get_companies_to_poll() is called on the returned object.
    """
    from services.diff_engine.storage import JobStorage
    import os
    db_path = os.environ.get("JOB_AGENT_DB", "data/jobs.db")
    return JobStorage(dsn=f"sqlite:///{db_path}")


def _run_one(provider: str, board_id: str) -> str:
    """Call the reader for *provider*, time it, return the formatted output line."""
    reader_fn = _get_reader(provider)
    if reader_fn is None:
        return f"{provider}\t{board_id}\t0\tFalse\treader_not_available\t0.00s"

    t0 = time.monotonic()
    try:
        if provider == "bamboohr":
            from services.poller.reader_result import ReaderResult
            result: ReaderResult = reader_fn(company_slug=board_id, company_name=board_id)
        else:
            result = reader_fn(board_id)
    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - t0
        return f"{provider}\t{board_id}\t0\tFalse\t{type(exc).__name__}: {exc}\t{elapsed:.2f}s"

    elapsed = time.monotonic() - t0
    count = len(result.postings)
    complete = result.complete
    error = result.error or ""
    return f"{provider}\t{board_id}\t{count}\t{complete}\t{error}\t{elapsed:.2f}s"


def run(argv: Optional[List[str]] = None) -> None:
    """Main entry point; argv defaults to sys.argv[1:] for CLI use, or can be supplied for tests."""
    parser = argparse.ArgumentParser(
        prog="python -m services.poller.check",
        description="Report-only live check for ATS board readers. Writes nothing.",
    )
    parser.add_argument("--provider", required=True,
                        help="ATS provider name (greenhouse, lever, ashby, smartrecruiters, bamboohr, workday, icims)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--board", help="Board slug or URL to check directly")
    group.add_argument("--limit", type=int, help="Read N boards from company_directory (read-only)")

    args = parser.parse_args(argv)
    provider = args.provider.lower()

    if provider not in _PROVIDER_MAP:
        parser.error(
            f"Unknown provider {provider!r}. "
            f"Supported: {', '.join(sorted(_PROVIDER_MAP))}"
        )

    if args.board:
        print(_run_one(provider, args.board))
    else:
        storage = _open_storage_readonly()
        companies: List[Dict[str, Any]] = storage.get_companies_to_poll(ats_platform=provider)
        companies = companies[: args.limit]
        if not companies:
            print(f"# No active {provider} companies found in company_directory.", file=sys.stderr)
            return
        _, arg_field = _PROVIDER_MAP[provider]
        for company in companies:
            board_id = company.get(arg_field) or company.get("board_slug") or company.get("board_url", "")
            if not board_id:
                print(f"{provider}\t(no board id)\t0\tFalse\tmissing_{arg_field}\t0.00s")
                continue
            print(_run_one(provider, board_id))


if __name__ == "__main__":  # pragma: no cover
    run()
