"""A caught exception is never dropped without a trace (code-quality audit, 24 October 2026, step 2.2).

The audit counted 163 `except ...: pass` in the production modules: frame detachment, a locator that crashed, a failed
save, all vanishing, so a run that stalled said nothing about why. They now log at debug (a few at warning). This test
fails on a new one. The modules below keep theirs on purpose: they write diagnostics (an exception's text must not reach a
log there), they ARE the logging, or they are command-line scripts.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# file -> how many silent handlers it may keep, and why.
ALLOWED = {
    "diagnostics.py": 5,            # artifact capture: the exception text may carry page content (see its tests)
    "stop_cause.py": 2,             # stop-report artifacts: same reason
    "local_debug.py": 2,            # the local debug capture itself: logging from inside it would recurse
    "structured_logging.py": 1,     # the logging layer
    "runtime_events.py": 1,         # a failing event listener must not stop the others or log into the event bus
    "resource_manager.py": 2,       # per-process psutil races while counting browsers
    "check_sites.py": 1,            # command-line scripts
    "launch_dashboard.py": 1,
    "sign_in_browser.py": 1,
    "scripts/agent_status.py": 1,
    "scripts/question_coverage.py": 1,
}


def silent_handlers(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.ExceptHandler) and len(n.body) == 1 and isinstance(n.body[0], ast.Pass)]


def production_files():
    for pattern in ("*.py", "sites/*.py", "scripts/*.py"):
        yield from sorted(ROOT.glob(pattern))


def test_no_new_exception_is_swallowed_without_a_trace():
    found = {}
    for path in production_files():
        lines = silent_handlers(path)
        if lines:
            found[path.relative_to(ROOT).as_posix()] = lines
    too_many = {f: lines for f, lines in found.items() if len(lines) > ALLOWED.get(f, 0)}
    assert not too_many, ("log it (logger.debug(..., str(exc))) instead of `pass`, or add the module to ALLOWED "
                          f"with the reason: {too_many}")
