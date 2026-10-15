#!/usr/bin/env bash
# ==============================================================================
# Safe Remote Worker Update Script -- run ON the Oracle VM, after a payload has
# been uploaded to /tmp/jaa.tgz (the same convention scripts/remote/deploy_to_vm.ps1
# uses). Applies it and restarts the worker safely:
# DRAIN -> WAIT ACTIVE APPLICATION -> APPLY -> HEALTH CHECK -> RESTART
#
# This project is not deployed via a version-control pull on the VM:
# /opt/job-automation-agent has no history directory by design -- deploy_to_vm.ps1
# packs the repo on Windows and rsyncs it over instead. An earlier version of this
# script tried to fetch and switch to a revision here and always failed with
# "fatal: not a git repository", and signalled the daemon by matching its command
# line as the `ubuntu` SSH user, who has no permission to send it a signal --
# the process is owned by `jobagent` (f144).
# ==============================================================================
set -euo pipefail

PAYLOAD="${1:-/tmp/jaa.tgz}"
INSTALL_DIR="/opt/job-automation-agent"
SERVICE="job-agent-worker.service"
DRAIN_TIMEOUT="${DRAIN_TIMEOUT_SECONDS:-300}"

if [ ! -f "$PAYLOAD" ]; then
    echo "No payload at $PAYLOAD -- upload one first (see scripts/remote/deploy_to_vm.ps1)." >&2
    exit 1
fi

echo "=== [1/4] Signalling worker to DRAIN ==="
# worker_daemon.py's own SIGTERM handler stops it claiming new jobs and lets an
# active one finish. `systemctl kill` reaches the jobagent-owned process cleanly,
# where matching its command line as the unprivileged ubuntu user cannot.
sudo systemctl kill -s SIGTERM "$SERVICE" 2>/dev/null || true

echo "=== [2/4] Waiting for any active application to finish (up to ${DRAIN_TIMEOUT}s) ==="
elapsed=0
while [ "$elapsed" -lt "$DRAIN_TIMEOUT" ]; do
    status=$(sudo -u jobagent "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/remote_cli.py" workers 2>/dev/null \
        | python3 -c "import json,sys
d=json.load(sys.stdin)
w=d['workers'][0] if d.get('workers') else {}
print(w.get('status','UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")
    if [ "$status" != "BUSY" ]; then
        break
    fi
    sleep 5
    elapsed=$((elapsed + 5))
done
if [ "$elapsed" -ge "$DRAIN_TIMEOUT" ]; then
    echo "WARNING: still BUSY after ${DRAIN_TIMEOUT}s -- proceeding anyway." >&2
fi

echo "=== [3/4] Applying payload (rsync, preserving runtime state) ==="
TMP_EXTRACT="$(mktemp -d)"
tar -xzf "$PAYLOAD" -C "$TMP_EXTRACT"
sudo rsync -a --delete \
    --exclude='.venv' --exclude='.env' --exclude='browser_profile' \
    --exclude='data' --exclude='.git' --exclude='__pycache__' --exclude='scratch' \
    "$TMP_EXTRACT/" "$INSTALL_DIR/"
sudo chown -R jobagent:jobagent "$INSTALL_DIR"
rm -rf "$TMP_EXTRACT"

echo "=== [4/4] Verifying health and restarting service ==="
cd "$INSTALL_DIR"
sudo -u jobagent bash -c "source .venv/bin/activate && bash scripts/remote/health_check.sh"
sudo systemctl restart "$SERVICE"
echo "Worker service restarted successfully."
