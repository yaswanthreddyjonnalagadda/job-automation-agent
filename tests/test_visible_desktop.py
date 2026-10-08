"""The agent's browser opened where the owner could not see it (28 September, Writer on Ashby).

An IDE's AI assistant started the dashboard from its own terminal. That terminal runs on a private
Windows desktop ("exebox-..."), and everything it starts -- the dashboard, apply.py, apply_flow.py
and the browser -- opens its windows there. The run went on, the log even said the window had been
brought to the front (it had, on that desktop), and the owner saw nothing. The class: a program that
shows the owner a window never checked that it was on the desktop the owner looks at.
"""
import ctypes
import os
import subprocess
import sys
import uuid
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

import pytest
from hypothesis import given, strategies as st

import visible_desktop
from visible_desktop import InvisibleDesktopError, refuse_if_invisible, why_invisible

ROOT = Path(__file__).parents[1]


def test_the_owners_own_desktop_is_allowed():
    assert why_invisible(("WinSta0", "Default")) is None
    assert why_invisible(("winsta0", "default")) is None          # Windows names are not case-sensitive


def test_the_ide_assistants_private_desktop_is_refused():
    reason = why_invisible(("WinSta0", "exebox-477MW5ARHBVVTDW3B434YAPPT2"))
    assert reason and "exebox-477MW5ARHBVVTDW3B434YAPPT2" in reason and "Start Dashboard.bat" in reason


def test_a_service_window_station_is_refused_even_with_a_default_desktop():
    assert why_invisible(("Service-0x0-3e7$", "Default"))


def test_a_desktop_whose_name_cannot_be_read_is_refused():
    assert why_invisible(("", ""))
    assert why_invisible(("WinSta0", ""))


@given(st.text(min_size=1, max_size=40).filter(lambda n: n.lower() != "default"))
def test_every_desktop_but_the_owners_is_refused(name):
    assert why_invisible(("WinSta0", name))


@given(st.text(max_size=40).filter(lambda n: n.lower() != "winsta0"))
def test_every_window_station_but_the_owners_is_refused(station):
    assert why_invisible((station, "Default"))


def test_off_windows_there_is_nothing_to_check():
    assert why_invisible(None) is None


def test_refusing_raises_with_the_reason():
    with pytest.raises(InvisibleDesktopError, match="Start Dashboard.bat"):
        refuse_if_invisible(("WinSta0", "exebox-1"))
    refuse_if_invisible(("WinSta0", "Default"))                    # must not raise


def test_the_browser_is_never_started_on_a_hidden_desktop(monkeypatch):
    import browser_automation
    started = []
    monkeypatch.setattr(visible_desktop, "where_this_runs", lambda: ("WinSta0", "exebox-1"))
    monkeypatch.setattr(browser_automation, "sync_playwright", lambda: started.append(1))
    assistant = browser_automation.JobApplicationAssistant.__new__(browser_automation.JobApplicationAssistant)
    assistant._config = SimpleNamespace(browser_profile_dir="unused")
    with pytest.raises(InvisibleDesktopError):
        assistant.__enter__()
    assert not started


def test_the_dashboard_does_not_start_on_a_hidden_desktop(monkeypatch, capsys, tmp_path):
    import web_ui
    served = []
    monkeypatch.setattr(web_ui, "BASE_DIR", tmp_path)
    monkeypatch.setattr(visible_desktop, "where_this_runs", lambda: ("WinSta0", "exebox-1"))
    monkeypatch.setattr(web_ui.app, "run", lambda **kw: served.append(kw))
    with pytest.raises(SystemExit) as stopped:
        web_ui.serve()
    assert stopped.value.code == 2 and not served
    assert "exebox-1" in capsys.readouterr().err


def test_the_dashboard_starts_on_the_owners_desktop(monkeypatch, tmp_path):
    import web_ui
    served = []
    monkeypatch.setattr(web_ui, "BASE_DIR", tmp_path)
    monkeypatch.setattr(visible_desktop, "where_this_runs", lambda: ("WinSta0", "Default"))
    monkeypatch.setattr(web_ui.app, "run", lambda **kw: served.append(kw))
    web_ui.serve()
    assert served and served[0]["host"] == "127.0.0.1"


@pytest.mark.skipif(os.name != "nt", reason="Windows desktops")
def test_this_test_run_reads_a_real_desktop_name():
    station, desktop = visible_desktop.where_this_runs()
    assert station and desktop


class _STARTUPINFO(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
                ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


@pytest.mark.skipif(os.name != "nt", reason="Windows desktops")
def test_a_process_started_on_a_real_hidden_desktop_is_refused(tmp_path):
    """The way the IDE assistant does it: a desktop of its own, and a process started on it."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.CreateDesktopW.restype = wintypes.HANDLE
    user32.CreateDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD,
                                      wintypes.DWORD, ctypes.c_void_p]
    kernel32.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
                                        ctypes.POINTER(_STARTUPINFO), ctypes.POINTER(_PROCESS_INFORMATION)]
    name = f"agent-test-{uuid.uuid4().hex[:8]}"
    desktop = user32.CreateDesktopW(name, None, None, 0, 0x10000000, None)     # GENERIC_ALL
    if not desktop:
        pytest.skip(f"cannot create a desktop here (error {ctypes.get_last_error()})")
    out = tmp_path / "answer.txt"
    script = ("import sys; sys.path.insert(0, sys.argv[1]); import visible_desktop; "
              "open(sys.argv[2], 'w', encoding='utf-8').write(repr(visible_desktop.where_this_runs()) + '\\n' "
              "+ str(visible_desktop.why_invisible()))")
    command = subprocess.list2cmdline([sys.executable, "-c", script, str(ROOT), str(out)])
    startup = _STARTUPINFO(cb=ctypes.sizeof(_STARTUPINFO), lpDesktop=f"WinSta0\\{name}")
    info = _PROCESS_INFORMATION()
    try:
        ok = kernel32.CreateProcessW(None, ctypes.create_unicode_buffer(command), None, None, False,
                                     0x08000000, None, None, ctypes.byref(startup), ctypes.byref(info))
        assert ok, f"CreateProcess failed ({ctypes.get_last_error()})"
        kernel32.WaitForSingleObject(info.hProcess, 60000)
        kernel32.CloseHandle(info.hProcess)
        kernel32.CloseHandle(info.hThread)
        where, reason = out.read_text(encoding="utf-8").split("\n", 1)
        assert name in where
        assert reason != "None" and name in reason
    finally:
        user32.CloseDesktop(desktop)
