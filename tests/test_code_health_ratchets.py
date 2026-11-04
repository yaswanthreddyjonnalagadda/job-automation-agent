"""Code health only moves one way (f323).

Three ratchets with no grandfathering beyond today's counts: no function may
grow past 80 lines unless one already there shrinks first (the two 7,000-line
modules got that way one function at a time), no new time.sleep may appear
(real waits exist for browsers, schedulers for daemons), and every skipped
test already names why, which must stay true. Plus a compile gate (the
E9 check the linter config cannot cover alone).
"""
import ast
import py_compile
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LONG_BASELINE = {
    "account_state.py": 2,
    "apply_flow.py": 4,
    "browser_automation.py": 19,
    "claude_integration.py": 2,
    "concept_matcher.py": 2,
    "email_verification.py": 3,
    "gemini_auth.py": 1,
    "interaction.py": 3,
    "job_discovery.py": 1,
    "page_agent.py": 14,
    "repeated_entries.py": 1,
    "scripts/agent_status.py": 1,
    "sites/workday.py": 2,
    "submission_guard.py": 2,
    "web_ui.py": 2,
}

SLEEP_BASELINE = {
    "browser_automation.py": 5,
    "claude_integration.py": 5,
    "interaction.py": 1,
    "model_ladder.py": 1,
    "scripts/question_coverage.py": 1,
    "scripts/verify_boards.py": 1,
    "session_planner.py": 1,
    "tests/test_agent_window_is_shown.py": 1,
    "tests/test_browser_profile_lease.py": 1,
    "tests/test_continue_button.py": 1,
    "tests/test_durable_queue.py": 1,
    "tests/test_post_phase0_integration.py": 2,
    "tests/test_remote_smoke_verification.py": 1,
    "tests/test_run_recovery_regressions.py": 1,
    "tests/test_session_brain.py": 6,
    "web_ui.py": 1,
    "worker_daemon.py": 5,
}

_SLEEP = re.compile(r"(?<![\w.])time\.sleep\(|(?<![\w.])sleep\(")


def production_files():
    for pattern in ("*.py", "sites/*.py", "scripts/*.py"):
        yield from sorted(ROOT.glob(pattern))


def test_no_function_grows_past_eighty_lines():
    over = {}
    for path in production_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        count = sum(1 for node in ast.walk(tree)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and (node.end_lineno or 0) - node.lineno > 80)
        allowed = LONG_BASELINE.get(path.relative_to(ROOT).as_posix(), 0)
        if count > allowed:
            over[path.name] = (allowed, count)
    assert not over, f"split it instead: {over}"


def test_no_new_sleep_appears():
    over = {}
    for pattern in ("*.py", "sites/*.py", "scripts/*.py", "tests/*.py"):
        for path in sorted(ROOT.glob(pattern)):
            count = 0
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or "wait_for_timeout" in line:
                    continue
                if _SLEEP.search(line):
                    count += 1
            allowed = SLEEP_BASELINE.get(path.relative_to(ROOT).as_posix(), 0)
            if count > allowed:
                over[path.name] = (allowed, count)
    assert not over, f"use a real wait instead of sleep: {over}"


def test_every_skipped_test_says_why():
    missing = []
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name not in ("skipif", "skip", "xfail"):
                continue
            reasoned = any(k.arg == "reason" for k in node.keywords) or (
                name == "skip" and len(node.args) >= 1)
            if not reasoned:
                missing.append(f"{path.name}:{node.lineno}")
    assert not missing, f"say why (and when it can go): {missing}"


def test_the_tree_compiles():
    for pattern in ("*.py", "sites/*.py", "scripts/*.py", "tests/*.py"):
        for path in sorted(ROOT.glob(pattern)):
            py_compile.compile(str(path), doraise=True)
