#!/usr/bin/env bash
# ==============================================================================
# Dependency Installation & Synthetic Browser Launch Test
# Run as user 'jobagent' inside /opt/job-automation-agent
# ==============================================================================
set -euo pipefail

cd /opt/job-automation-agent

echo "=== [1/4] Setting up Python virtual environment ==="
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate

echo "=== [2/4] Installing Python Requirements ==="
pip install --upgrade pip
pip install -r requirements.txt

echo "=== [3/4] Installing Playwright Chromium & Native Dependencies ==="
if sudo -n true 2>/dev/null; then
    python -m playwright install --with-deps chromium
else
    # System libraries are installed by bootstrap_worker.sh; no sudo needed for the browser itself.
    python -m playwright install chromium
fi

echo "=== [4/4] Performing Synthetic Browser Launch Test ==="
# Install/update the exact Chromium AppArmor exception before checking startup.
# An administrator runs this separately when jobagent has no sudo permission.
if sudo -n true 2>/dev/null; then
    sudo bash scripts/remote/configure_browser_sandbox.sh
fi
xvfb-run -a python scripts/remote/verify_browser_startup.py
echo "All dependencies and browser engine verified successfully!"

