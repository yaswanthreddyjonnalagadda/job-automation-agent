#!/usr/bin/env bash
# ==============================================================================
# Safe Remote Worker Update / Deployment Script
# Enforces Section 42/43 Deployment Lifecycle:
# DRAIN -> WAIT ACTIVE APPLICATION -> UPDATE SHA -> HEALTH CHECK -> RESTART
# ==============================================================================
set -euo pipefail

TARGET_SHA="${1:-HEAD}"
cd /opt/job-automation-agent

echo "=== [1/4] Signalling worker to DRAIN ==="
pkill -TERM -f "worker_daemon.py" || true
# Allow time for active application to pause or complete
sleep 5

echo "=== [2/4] Fetching and checking out ${TARGET_SHA} ==="
git fetch origin
git checkout "${TARGET_SHA}"

echo "=== [3/4] Updating dependencies ==="
source .venv/bin/activate
pip install -r requirements.txt

echo "=== [4/4] Verifying health and restarting service ==="
bash scripts/remote/health_check.sh

if command -v systemctl &>/dev/null; then
    sudo systemctl restart job-agent-worker.service
    echo "Worker service restarted successfully."
else
    echo "Manual restart required: bash scripts/remote/start_worker.sh"
fi

