@echo off
REM Job-source scheduled crawl (Card 6): polls every live board for all providers.
REM Appends output to data\job_source_runs.log. Safe to run while another copy runs (per-provider lock).
REM
REM Windows Task Scheduler setup (run once as the owner):
REM   1. Open Task Scheduler and choose Create Task (not Basic Task).
REM   2. Triggers tab, New: Begin the task On a schedule, Daily, Start 6:00:00, Recur every 1 days, Enabled.
REM   3. Triggers tab, New again: Daily, Start 18:00:00, Recur every 1 days, Enabled.
REM   4. Actions tab, New: Start a program = C:\Users\jonna\job-automation-agent\scripts\run_job_source.bat,
REM      Start in = C:\Users\jonna\job-automation-agent.
REM   5. Conditions tab (optional): Wake the computer to run this task.
cd /d C:\Users\jonna\job-automation-agent
if exist venv\Scripts\python.exe (
  venv\Scripts\python.exe -m services.poller.run --all >> data\job_source_runs.log 2>&1
) else (
  python -m services.poller.run --all >> data\job_source_runs.log 2>&1
)