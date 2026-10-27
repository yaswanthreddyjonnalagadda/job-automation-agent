@echo off
rem Double-click to open the agent's own browser so you can sign in to Gmail once.
rem The sign-in is yours; it stays in the agent's browser for later runs. Close the browser window when done.
rem Do not run this while the agent is working on a job (the browser profile can only be open once).
cd /d "%~dp0"
title Agent browser - sign in to Gmail, then close the browser window
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" sign_in_browser.py
) else (
  python sign_in_browser.py
)
pause
