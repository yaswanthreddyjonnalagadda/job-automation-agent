"""
Helper to launch a command on the user's interactive desktop (WinSta0\\Default).
Allows IDE AI assistants (Antigravity/Codex) to run browser automation
visibly on the user's screen without triggering visible_desktop.InvisibleDesktopError.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import sys
import subprocess

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

class STARTUPINFO(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]

class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]

STARTF_USESTDHANDLES = 0x00000100
STD_INPUT_HANDLE = -10
STD_OUTPUT_HANDLE = -11
STD_ERROR_HANDLE = -12

def run_command_on_default_desktop(cmd_args: list[str]) -> int:
    cmd_str = subprocess.list2cmdline(cmd_args)
    print(f"Launching on WinSta0\\Default: {cmd_str}")

    si = STARTUPINFO()
    si.cb = ctypes.sizeof(STARTUPINFO)
    si.lpDesktop = "WinSta0\\Default"
    si.dwFlags = STARTF_USESTDHANDLES
    si.hStdInput = kernel32.GetStdHandle(STD_INPUT_HANDLE)
    si.hStdOutput = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
    si.hStdError = kernel32.GetStdHandle(STD_ERROR_HANDLE)

    pi = PROCESS_INFORMATION()

    success = kernel32.CreateProcessW(
        None,
        cmd_str,
        None,
        None,
        True,  # inherit handles
        0,
        None,
        None,
        ctypes.byref(si),
        ctypes.byref(pi),
    )
    if not success:
        err = ctypes.get_last_error()
        print(f"CreateProcessW failed with error code {err}", file=sys.stderr)
        return 1

    kernel32.CloseHandle(pi.hThread)
    kernel32.WaitForSingleObject(pi.hProcess, 0xFFFFFFFF)  # INFINITE
    exit_code = wintypes.DWORD()
    kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(exit_code))
    kernel32.CloseHandle(pi.hProcess)
    return exit_code.value

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/run_desktop.py <cmd> [args...]")
        sys.exit(1)
    args = sys.argv[1:]
    if args[0] == "--":
        args = args[1:]
    sys.exit(run_command_on_default_desktop(args))

