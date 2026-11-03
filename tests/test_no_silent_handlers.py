"""A caught exception leaves a trace, or the probe says it meant silence (f316).

The earlier audit only banned `except ...: pass`, so the same silence moved
into `except: continue`, `except: return False` and bare fallbacks: 400
handlers that neither log, print nor raise. No new ones: a handler that means
it lives in a function marked @probe (a liveness check, an optional import);
anything else logs at debug or raises. Counts below are the ratchet -- a file
may only go down from here.

The same change gives the 268 copies of str(exc).splitlines()[0][:100] one
home: diagnostics.short_exc (which, unlike the inline form, does not raise
IndexError on an exception with an empty message).
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_CALLS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}

# file -> silent handlers it may keep. A file missing here may keep none.
BASELINE = {
    "ai_models.py": 2,
    "answer_bank.py": 3,
    "answer_engine.py": 3,
    "apply.py": 1,
    "apply_flow.py": 15,
    "browser_automation.py": 90,
    "check_sites.py": 5,
    "checkpoint.py": 2,
    "claude_answers.py": 1,
    "claude_integration.py": 5,
    "concept_matcher.py": 1,
    "db.py": 2,
    "desktop_window.py": 1,
    "detail_view.py": 1,
    "diagnostics.py": 18,
    "discovered_view.py": 1,
    "discovery_sources.py": 2,
    "document_cache.py": 1,
    "email_verification.py": 1,
    "field_timing.py": 1,
    "form_fields.py": 6,
    "gemini_integration.py": 3,
    "interaction.py": 10,
    "job_discovery.py": 4,
    "job_sources.py": 5,
    "job_tracker.py": 3,
    "launch_dashboard.py": 1,
    "local_debug.py": 4,
    "login_guard.py": 1,
    "matching_engine.py": 4,
    "model_ladder.py": 2,
    "notifications.py": 2,
    "open_answers.py": 2,
    "openai_integration.py": 2,
    "option_match.py": 2,
    "page_agent.py": 67,
    "perception.py": 2,
    "phone_alert.py": 3,
    "phone_console.py": 8,
    "profile_setup.py": 5,
    "provenance.py": 1,
    "recovery.py": 1,
    "remote_client.py": 1,
    "replay_guard.py": 5,
    "resource_manager.py": 3,
    "run_diagnostics.py": 1,
    "runtime_events.py": 3,
    "safety.py": 7,
    "scripts/agent_status.py": 2,
    "scripts/field_timings.py": 1,
    "scripts/question_coverage.py": 4,
    "session_planner.py": 1,
    "sign_in_browser.py": 1,
    "sites/adp.py": 2,
    "sites/amazon.py": 4,
    "sites/avature.py": 1,
    "sites/greenhouse.py": 1,
    "sites/icims.py": 5,
    "sites/successfactors.py": 6,
    "sites/workday.py": 20,
    "ssh_remote_client.py": 2,
    "stop_cause.py": 2,
    "submission_guard.py": 4,
    "web_progress.py": 1,
    "web_setup.py": 3,
    "web_ui.py": 25,
}


def leaves_a_trace(handler: ast.ExceptHandler) -> bool:
    for child in ast.walk(handler):
        if isinstance(child, ast.Raise):
            return True
        if isinstance(child, ast.Call):
            fn = child.func
            if isinstance(fn, ast.Attribute) and fn.attr in LOG_CALLS:
                return True
            if isinstance(fn, ast.Name) and fn.id == "print":
                return True
    return False


def is_probe(func: ast.AST) -> bool:
    for dec in getattr(func, "decorator_list", ()):
        name = dec.id if isinstance(dec, ast.Name) else getattr(dec, "attr", "")
        if name == "probe":
            return True
    return False


class Visitor(ast.NodeVisitor):
    def __init__(self):
        self.stack = []
        self.silent = []

    def visit_FunctionDef(self, node):
        self.stack.append(node)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ExceptHandler(self, node):
        if not leaves_a_trace(node) and not any(is_probe(f) for f in self.stack):
            self.silent.append(node.lineno)
        self.generic_visit(node)


def silent_handlers(path: Path) -> list[int]:
    visitor = Visitor()
    visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
    return visitor.silent


def production_files():
    for pattern in ("*.py", "sites/*.py", "scripts/*.py"):
        yield from sorted(ROOT.glob(pattern))


def test_no_file_gains_a_silent_handler():
    over = {}
    for path in production_files():
        if path.name == "structured_logging.py":
            continue
        found = silent_handlers(path)
        allowed = BASELINE.get(path.relative_to(ROOT).as_posix(), 0)
        if len(found) > allowed:
            over[path.name] = found
    assert not over, ("log it (logger.debug(..., diagnostics.short_exc(exc))), raise it, or mark the deliberate"
                      f" probe @probe: {over}")


def test_short_exc_bounds_an_exception_to_its_first_line():
    import diagnostics
    assert diagnostics.short_exc(ValueError("first\nsecond")) == "first"
    assert diagnostics.short_exc(ValueError("x" * 200)) == "x" * 100
    assert diagnostics.short_exc(ValueError("x" * 200), 120) == "x" * 120


def test_short_exc_names_a_silent_exception_instead_of_crashing():
    import diagnostics
    assert diagnostics.short_exc(ValueError("")) == "ValueError"
    assert diagnostics.short_exc(RuntimeError()) == "RuntimeError"


def test_probe_marks_a_function_without_changing_it():
    import diagnostics

    @diagnostics.probe
    def f():
        return 7
    assert f() == 7
