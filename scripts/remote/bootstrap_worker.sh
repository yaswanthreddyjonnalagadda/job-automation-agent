#!/usr/bin/env bash
# ==============================================================================
# Oracle Cloud Free VM - Worker Bootstrap Script
# Target OS: Ubuntu 22.04 LTS / 24.04 LTS (aarch64 Ampere or x86_64)
# Runs as non-root service user 'jobagent' with zero paid infrastructure dependencies.
# ==============================================================================
set -euo pipefail

echo "=== [1/5] Verifying System Architecture & OS ==="
ARCH=$(uname -m)
echo "Architecture detected: ${ARCH}"
if [[ "${ARCH}" != "aarch64" && "${ARCH}" != "x86_64" ]]; then
    echo "ERROR: Unsupported architecture ${ARCH}."
    exit 1
fi

echo "=== [2/5] Creating dedicated service user 'jobagent' ==="
if ! id "jobagent" &>/dev/null; then
    sudo useradd -m -s /bin/bash jobagent
    echo "Created user 'jobagent'."
else
    echo "User 'jobagent' already exists."
fi

echo "=== [3/5] Installing System Dependencies ==="
sudo apt-get update -y
sudo apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv git curl \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libpango-1.0-0 libcairo2

echo "=== [4/5] Setting up Application Directory & Permissions ==="
INSTALL_DIR="/opt/job-automation-agent"
if [ ! -d "${INSTALL_DIR}" ]; then
    sudo mkdir -p "${INSTALL_DIR}"
    sudo chown -R jobagent:jobagent "${INSTALL_DIR}"
fi

echo "=== [5/5] Bootstrap complete ==="
echo "Next step: Run install_dependencies.sh as 'jobagent'."

