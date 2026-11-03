"""The page loop never hot-patches running code by itself (f315).

When a posting guard denied a click, the main page loop re-read two modules
from disk and re-injected the guard script: with no edit on disk the reload
was a no-op that also reset module state, and with an edit mid-run the
behavior depended on timing no test could pin. Re-reading now needs an
explicit HOT_RELOAD=1; otherwise the guard is rebuilt from the running code.
The deliberate resume path (load_latest_code, which exists to pick up a fix
the owner just made) is unchanged.
"""
import page_agent


def test_posting_refresh_keeps_running_code_by_default(monkeypatch):
    monkeypatch.delenv("HOT_RELOAD", raising=False)
    reloaded = []
    monkeypatch.setattr(page_agent.importlib, "reload", lambda m: reloaded.append(m))
    page_agent.posting_guard_refresh_modules()
    assert reloaded == []


def test_posting_refresh_rereads_code_only_when_asked(monkeypatch):
    monkeypatch.setenv("HOT_RELOAD", "1")
    reloaded = []
    monkeypatch.setattr(page_agent.importlib, "reload", lambda m: reloaded.append(m))
    guard = page_agent.posting_guard_refresh_modules()
    assert len(reloaded) == 2
    assert guard.__name__ == "submission_guard"
