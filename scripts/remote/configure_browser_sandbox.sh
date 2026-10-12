#!/usr/bin/env bash
# Run as root after installing/upgrading jobagent's Playwright browser.
# Permit its exact executable to create the namespaces Chromium's sandbox needs.
set -euo pipefail
if [[ ${EUID} -ne 0 ]]; then
    echo "Run this script with sudo." >&2
    exit 1
fi
restriction=/proc/sys/kernel/apparmor_restrict_unprivileged_userns
if [[ ! -f "$restriction" ]] || [[ $(cat "$restriction") != 1 ]]; then
    echo "AppArmor user namespace exception is not required on this host."
    exit 0
fi
browser=$(sudo -u jobagent /opt/job-automation-agent/.venv/bin/python -c \
    'from playwright.sync_api import sync_playwright; p=sync_playwright().start(); print(p.chromium.executable_path); p.stop()')
if [[ ! "$browser" =~ ^/[a-zA-Z0-9_./-]+$ ]] || [[ ! -x "$browser" ]]; then
    echo "Installed Chromium executable path is missing or invalid." >&2
    exit 1
fi
policy=$(mktemp)
trap 'rm -f "$policy"' EXIT
cat > "$policy" <<EOF
abi <abi/4.0>,
include <tunables/global>
profile job-agent-chromium "$browser" flags=(unconfined) {
  userns,
}
EOF
# Validate before replacing the installed policy; load only this profile.
apparmor_parser --skip-kernel-load "$policy"
install -o root -g root -m 644 "$policy" /etc/apparmor.d/job-agent-chromium
apparmor_parser -r /etc/apparmor.d/job-agent-chromium
echo "Chromium sandbox namespace policy loaded for the installed executable."
