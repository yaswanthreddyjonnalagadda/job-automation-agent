"""The browser the agent opens was never brought to the front, so it sat behind whatever the owner was
using and they never saw it (reported again 25 September; the same as the IGT run long before).

raise_window looked for a window titled "Chrome for Testing" -- Playwright's bundled browser -- but the
agent has used real Google Chrome for a long time, whose windows are titled "... - Google Chrome". Nothing
matched, so nothing was ever raised and no error said so. It was also only called at a hand-over, never when a
run starts. The class: a window found by a title that changed when the browser did.
"""
import os
import re
import subprocess
import time
from types import SimpleNamespace

import pytest

from browser_automation import JobApplicationAssistant, pick_agent_window

MINE, YOURS = {100, 101}, {200}
WINDOWS = [
    (1, "Sign In - Google Chrome", 100),               # the agent's window
    (2, "Restore pages?", 100),                        # Chrome's own bubble, same process
    (3, "Sign In - Google Chrome", 200),               # the owner's own Chrome, same page title
    (4, "Job Applications - Google Chrome", 200),      # the owner's dashboard
    (5, "Untitled - Notepad", 300),
]


def test_the_agents_real_chrome_window_is_the_one_picked():
    assert pick_agent_window(WINDOWS, MINE, "Sign In") == 1


def test_the_owners_own_chrome_is_never_picked_even_with_the_same_title():
    assert pick_agent_window(WINDOWS, YOURS | MINE, "Job Applications") == 4     # only its process is asked for
    assert pick_agent_window(WINDOWS, MINE, "Job Applications") == 1              # ... not the owner's dashboard
    assert pick_agent_window([w for w in WINDOWS if w[2] == 200], MINE, "Sign In") is None


@pytest.mark.parametrize("title", ["Sign In - Google Chrome", "Sign In - Chrome for Testing", "Sign In - Chromium"])
def test_every_title_a_chrome_family_browser_gives_is_recognised(title):
    assert pick_agent_window([(7, title, 100)], MINE, "Sign In") == 7


def test_a_window_whose_page_title_just_changed_is_still_the_agents_by_its_process():
    assert pick_agent_window(WINDOWS, MINE, "A page that was just replaced") == 1


def test_chromes_own_bubbles_are_not_the_window():
    assert pick_agent_window([(2, "Restore pages?", 100)], MINE, "Sign In") is None


def test_with_no_process_to_go_by_only_a_matching_title_counts_and_never_any_chrome():
    assert pick_agent_window(WINDOWS, set(), "Job Applications") == 4
    assert pick_agent_window(WINDOWS, set(), "") is None
    assert pick_agent_window(WINDOWS, set(), "No such page") is None


def test_nothing_is_picked_when_there_is_nothing():
    assert pick_agent_window([], MINE, "Sign In") is None


def test_the_browser_starts_without_the_restore_pages_bubble(tmp_path):
    """The agent's Chrome is stopped by force, so the next start asked "Restore pages?" over the page."""
    seen = {}
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant._config = SimpleNamespace(browser_headless=False)
    assistant._playwright = SimpleNamespace(chromium=SimpleNamespace(
        launch_persistent_context=lambda **kwargs: seen.update(kwargs)))
    assistant._launch("chrome", tmp_path)
    assert "--start-maximized" in seen["args"]
    assert "--disable-session-crashed-bubble" in seen["args"] and "--hide-crash-restore-bubble" in seen["args"]


def test_a_run_raises_the_window_as_soon_as_it_has_opened_the_page():
    """apply_flow raises the window when it starts working, not only at a hand-over."""
    from pathlib import Path
    source = (Path(__file__).parents[1] / "apply_flow.py").read_text(encoding="utf-8")
    opened = source.index("page = assistant.open_job_page(resume_at or job.url)")
    assert "assistant.raise_window(page)" in source[opened:opened + 400]


@pytest.mark.skipif(os.name != "nt" or not os.getenv("RAISE_WINDOW_TEST"),
                    reason="opens two real Chrome windows on the desktop for a few seconds: set RAISE_WINDOW_TEST=1")
def test_the_real_windows_call_raises_the_agents_window_and_not_the_owners(tmp_path):
    """Two real Chrome windows with the same page title: the agent's is the one raised."""
    from playwright.sync_api import sync_playwright

    def pids_using(profile):
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Where-Object "
             f"{{ $_.CommandLine -like '*{profile}*' }}).ProcessId -join ','"],
            capture_output=True, text=True, timeout=30)
        return {int(x) for x in res.stdout.strip().split(",") if x.strip().isdigit()}

    mine, yours = tmp_path / "agent_profile", tmp_path / "owner_profile"
    with sync_playwright() as p:
        try:
            agent = p.chromium.launch_persistent_context(str(mine), channel="chrome", headless=False,
                                                         no_viewport=True, args=["--start-maximized"])
            other = p.chromium.launch_persistent_context(str(yours), channel="chrome", headless=False,
                                                         no_viewport=True)
        except Exception as exc:
            pytest.skip(f"real Chrome is not available here: {str(exc).splitlines()[0][:80]}")
        try:
            for ctx in (agent, other):
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.set_content("<title>Same Title</title><p>x</p>")
            time.sleep(2)
            assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
            assistant._config = SimpleNamespace(browser_profile_dir=str(mine))
            hwnd = assistant._agent_window(agent.pages[0])
            assert hwnd
            import ctypes
            from ctypes import wintypes
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            assert pid.value in pids_using(str(mine)) and pid.value not in pids_using(str(yours))
            assistant.raise_window(agent.pages[0])                      # must not raise
        finally:
            agent.close()
            other.close()
