"""Finding the agent's browser window on the Windows desktop and bringing it to the front.

Moved out of browser_automation.JobApplicationAssistant (code-quality audit, step 3.1): none of this needs the
assistant's state, only the agent's browser-profile folder and the page's title. The assistant keeps methods of the
same names that call these, so callers and tests are unchanged.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("browser_automation")

# A Chrome-family window is titled "<page> - Google Chrome" (real Chrome, what the agent uses),
# "... - Chrome for Testing" (Playwright's bundled browser) or "... - Chromium". Chrome's own bubbles
# ("Restore pages?") have no such suffix and are not the window.
_BROWSER_WINDOW_TITLE = re.compile(r"(Google Chrome|Chrome for Testing|Chromium)\s*$")


def pick_agent_window(windows: list[tuple[int, str, int]], agent_pids: set[int], title: str) -> Optional[int]:
    """Which top-level window is the agent's browser: (handle, title, process id) for each visible window.

    By process where it is known -- the agent's Chrome runs on its own profile, so its processes are told
    apart from the owner's own Chrome even when both show the same page title -- else by the page's title;
    never "any Chrome window". The window is looked for by its process first because a browser change had
    already broken the title once: it was matched on "Chrome for Testing" after the agent moved to real Chrome,
    so nothing was ever raised."""
    pool = [w for w in windows if w[2] in agent_pids] if agent_pids else list(windows)
    candidates = [w for w in pool if _BROWSER_WINDOW_TITLE.search(w[1] or "")]
    if not candidates:
        return None
    if title:
        named = [w for w in candidates if title[:40] in w[1]]
        if named:
            return named[0][0]
    if not agent_pids:
        return None                       # nothing but a title to go by, and it matches nothing
    return candidates[0][0]


def agent_chrome_pids(profile_dir) -> set[int]:
    """The process ids of the Chrome running on the agent's own profile (Windows)."""
    profile = str(profile_dir or "")
    if not profile:
        return set()
    try:
        escaped = str(Path(profile).resolve()).replace("'", "''")
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Where-Object "
             f"{{ $_.CommandLine -like '*{escaped}*' }}).ProcessId -join ','"],
            capture_output=True, text=True, timeout=15)
        return {int(x) for x in res.stdout.strip().split(",") if x.strip().isdigit()}
    except Exception as _swallowed:
        logger.debug("Could not list the agent's Chrome processes: %s", str(_swallowed).splitlines()[0][:80])
        return set()


def find_agent_window(page, agent_pids: set[int]) -> Optional[int]:
    """The handle of the agent's browser window (Windows), or None."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    windows: list[tuple[int, str, int]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd)
        if length and (user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd)):
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            windows.append((hwnd, buf.value, pid.value))
        return True

    user32.EnumWindows(visit, 0)
    try:
        title = (page.title() or "").strip()
    except Exception:
        title = ""
    return pick_agent_window(windows, agent_pids, title)


def raise_window(page, find_window: Callable[[object], Optional[int]]) -> None:
    """Puts the agent's browser window in front of other apps. Windows
    won't let a background process simply take focus -- the window opens
    behind whatever you're using (the IGT run's window was never seen) --
    but restoring it, putting it briefly on top of everything and then
    back, and asking for focus brings it up and keeps it visible.

    `find_window(page)` returns the window's handle; the assistant passes its own `_agent_window`."""
    try:
        page.bring_to_front()
        if os.name != "nt":
            return
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = find_window(page)
        if not hwnd:
            logger.info("Could not find the agent's browser window to bring it to the front")
            return
        user32.ShowWindow(hwnd, 6)   # SW_MINIMIZE, then back: the change puts it in front of the others
        user32.ShowWindow(hwnd, 3)   # SW_MAXIMIZE
        # Top of the stack whatever has focus: topmost, then ordinary again, without moving or resizing.
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0043)   # HWND_TOPMOST, NOMOVE | NOSIZE | SHOWWINDOW
        user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0043)   # HWND_NOTOPMOST
        user32.SetForegroundWindow(hwnd)
        logger.info("Brought the agent's browser window to the front")
    except Exception as exc:
        logger.debug("Could not raise the browser window: %s", exc)
