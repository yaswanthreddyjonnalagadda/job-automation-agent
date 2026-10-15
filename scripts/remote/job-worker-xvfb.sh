#!/usr/bin/env bash
# Runs the worker under a virtual display so the (headed) browser works on a VM with no screen.
exec xvfb-run -a -s "-screen 0 1920x1080x24" /opt/job-automation-agent/.venv/bin/python worker_daemon.py
