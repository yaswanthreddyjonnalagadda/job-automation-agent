"""Is this process on the Windows desktop the owner is looking at?

A process opens its windows on the desktop it was started on. The owner looks at
WinSta0\\Default. An IDE's AI assistant runs its terminal on a private desktop of
its own ("exebox-..."), so a dashboard it starts -- and every run and browser that
dashboard starts -- opens where nobody can see it, while the log still reports the
window brought to the front (28 September). Nothing on such a desktop is allowed
to run: the dashboard will not start there, and neither will the browser.

This is the one place that decides it; callers use refuse_if_invisible().
"""
from __future__ import annotations

import os
from typing import Optional

# The interactive window station and the desktop on it that the signed-in user sees.
_OWNERS_DESKTOP = ("winsta0", "default")
_UOI_NAME = 2


class InvisibleDesktopError(RuntimeError):
    pass


def _name_of(user32, handle) -> str:
    import ctypes
    from ctypes import wintypes
    if not handle:
        return ""
    needed = wintypes.DWORD(0)
    buf = ctypes.create_unicode_buffer(256)
    if not user32.GetUserObjectInformationW(handle, _UOI_NAME, buf, ctypes.sizeof(buf), ctypes.byref(needed)):
        return ""
    return buf.value


def where_this_runs() -> Optional[tuple[str, str]]:
    """(window station, desktop) this process runs on, or None off Windows. A name
    that cannot be read comes back empty."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetProcessWindowStation.restype = wintypes.HANDLE
    user32.GetThreadDesktop.restype = wintypes.HANDLE
    user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                 wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    station = _name_of(user32, user32.GetProcessWindowStation())
    desktop = _name_of(user32, user32.GetThreadDesktop(kernel32.GetCurrentThreadId()))
    return station, desktop


def why_invisible(where: Optional[tuple[str, str]] = ...) -> Optional[str]:
    """None when the owner can see this process's windows, else why not, in words
    for the owner. A desktop whose name cannot be read is refused too."""
    if where is ...:
        where = where_this_runs()
    if where is None:
        return None
    station, desktop = where
    if (station.lower(), desktop.lower()) == _OWNERS_DESKTOP:
        return None
    return (f"Refusing to run: this was started on the Windows desktop '{station}\\{desktop}', which is not "
            "the one on your screen, so the agent's browser would open where you cannot see it. This happens "
            "when an AI assistant in an IDE starts it from its own terminal. Start the dashboard yourself: "
            "double-click 'Start Dashboard.bat', or run 'python web_ui.py' in a terminal you opened.")


def refuse_if_invisible(where: Optional[tuple[str, str]] = ...) -> None:
    reason = why_invisible(where)
    if reason:
        raise InvisibleDesktopError(reason)
