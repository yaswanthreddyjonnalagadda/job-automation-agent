"""The Windows window code lives in desktop_window.py; the assistant's methods of the old names call it (audit 3.1)."""
from types import SimpleNamespace

import browser_automation
import desktop_window


def test_the_old_import_path_still_gives_the_same_function():
    assert browser_automation.pick_agent_window is desktop_window.pick_agent_window


def test_the_assistants_methods_call_the_module_with_its_own_profile_and_window_finder(monkeypatch):
    seen = {}
    monkeypatch.setattr(desktop_window, "agent_chrome_pids", lambda profile: seen.setdefault("profile", profile) and {7})
    monkeypatch.setattr(desktop_window, "find_agent_window",
                        lambda page, pids: seen.update(page=page, pids=pids) or 42)
    assistant = browser_automation.JobApplicationAssistant.__new__(browser_automation.JobApplicationAssistant)
    assistant._config = SimpleNamespace(browser_profile_dir="C:/profile")
    assert assistant._agent_window("the page") == 42
    assert seen == {"profile": "C:/profile", "page": "the page", "pids": {7}}


def test_raise_window_uses_the_assistants_window_finder(monkeypatch):
    calls = []
    monkeypatch.setattr(desktop_window.os, "name", "posix")           # no Win32 calls: only bring_to_front
    page = SimpleNamespace(bring_to_front=lambda: calls.append("front"))
    assistant = browser_automation.JobApplicationAssistant.__new__(browser_automation.JobApplicationAssistant)
    assistant.raise_window(page)
    assert calls == ["front"]


def test_a_missing_profile_folder_finds_no_processes():
    assert desktop_window.agent_chrome_pids("") == set() and desktop_window.agent_chrome_pids(None) == set()
