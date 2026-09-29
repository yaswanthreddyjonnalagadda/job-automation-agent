"""'Reload code' brings a fix into a run that is already waiting, without closing its browser.

It reloaded browser_automation, page_agent, safety and the site adapters only; the helpers the agents use
(account_state's table, the fillers, the matcher) kept the version the run started with, so new code met old
helpers -- a reload into Mutual of Enumclaw's waiting run (29 September) would have met an account_state without
its reset step. Every project module the agents use is now reloaded, each after the modules it uses.

importlib.reload is recorded here, not run: a real reload in the test process would change classes under the
other tests.
"""
import apply_flow
import browser_automation
import page_agent
from browser_automation import JobApplicationAssistant


def names(modules):
    return [m.__name__ for m in modules]


def test_the_helpers_the_agents_use_are_found_each_after_what_it_uses():
    order = names(apply_flow.project_modules_used_by(browser_automation, page_agent))
    for helper in ("account_state", "form_fields", "option_match", "geo_reference", "safety", "login_guard",
                   "emailed_codes", "repeated_entries"):
        assert helper in order, helper
    assert order.index("option_match") < order.index("form_fields")        # form_fields imports option_match
    assert order.index("geo_reference") < order.index("option_match")
    for reloaded_on_its_own in ("browser_automation", "page_agent", "apply_flow"):
        assert reloaded_on_its_own not in order


def test_a_code_reload_refreshes_every_helper_before_the_agent_that_uses_it(monkeypatch):
    reloaded = []
    monkeypatch.setattr(apply_flow.importlib, "reload", lambda module: reloaded.append(module.__name__) or module)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    apply_flow.reload_browser_automation(assistant)
    for helper in ("account_state", "form_fields", "option_match", "safety"):
        assert helper in reloaded, helper
    assert reloaded.index("option_match") < reloaded.index("form_fields")
    assert reloaded.index("account_state") < reloaded.index("browser_automation")
    assert reloaded[-1] == "browser_automation"


def test_a_helper_with_a_syntax_error_keeps_the_running_version(monkeypatch):
    reloaded = []
    monkeypatch.setattr(apply_flow.importlib, "reload", lambda module: reloaded.append(module.__name__) or module)

    def compile_(source, filename, mode):
        if filename.endswith("account_state.py"):
            raise SyntaxError("invalid syntax", (filename, 3, 1, "def half(:"))
        return compile(source, filename, mode)

    monkeypatch.setattr(apply_flow, "compile", compile_, raising=False)
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assert apply_flow.reload_browser_automation(assistant) is assistant
    assert "browser_automation" not in reloaded and "account_state" not in reloaded
    assert "form_fields" not in reloaded          # nothing half-reloaded: the whole running version is kept
