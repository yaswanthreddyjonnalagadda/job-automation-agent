#!/usr/bin/env bash
# ==============================================================================
# Start Remote Worker Daemon
# ==============================================================================
set -euo pipefail

cd /opt/job-automation-agent
source .venv/bin/activate

export JOB_WORKER_ID="${JOB_WORKER_ID:-oracle-worker-1}"
export JOB_WORKER_TYPE="REMOTE_LINUX"

echo "Starting worker daemon ${JOB_WORKER_ID}..."
exec python worker_daemon.py

