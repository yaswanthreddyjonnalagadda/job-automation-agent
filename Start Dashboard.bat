@echo off
rem Double-click to start the dashboard in a window of its own.
rem Started this way, the agent's browser opens on your screen. Started in the background
rem (for example by an IDE's AI assistant), the browser runs with no window you can see.
cd /d "%~dp0"
title Job agent dashboard - keep this window open
echo Starting the dashboard at http://127.0.0.1:5000 ...
echo Keep this window open while the agent works. Close it to stop the dashboard.
start "" http://127.0.0.1:5000
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" launch_dashboard.py
) else (
  python launch_dashboard.py
)
pause
