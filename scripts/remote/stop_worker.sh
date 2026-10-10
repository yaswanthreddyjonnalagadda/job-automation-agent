#!/usr/bin/env bash
# ==============================================================================
# Graceful Stop / Drain Worker Daemon
# ==============================================================================
set -euo pipefail

echo "Stopping worker daemon gracefully..."
pkill -TERM -f "worker_daemon.py" || true
echo "Worker stopped."

