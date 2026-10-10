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
python -m playwright install --with-deps chromium

echo "=== [4/4] Performing Synthetic Browser Launch Test ==="
python -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('about:blank')
    print('SUCCESS: Synthetic headless Chromium launch passed on ' + str(page.title() or 'blank page'))
    browser.close()
"
echo "All dependencies and browser engine verified successfully!"

