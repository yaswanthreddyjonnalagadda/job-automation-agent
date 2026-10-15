# Deploys job-automation-agent to the free Oracle VM and sets it up (swap, deps, Chromium, systemd).
# Run from PowerShell inside C:\Users\jonna\job-automation-agent :  .\scripts\remote\deploy_to_vm.ps1
# Add -IncludeEnv to also copy .env (secrets) over SSH. Nothing is committed or exposed publicly.
param(
  [string]$VmIp = "143.47.109.116",
  [string]$KeySource = "$HOME\Downloads\ssh-key-2026-10-10 (1).key",
  [switch]$IncludeEnv,
  [switch]$SkipUpload,
  [switch]$SyncData
)
$ErrorActionPreference = "Continue"
$sshDir = "$HOME\.ssh"; New-Item -ItemType Directory -Force $sshDir | Out-Null
$key = "$sshDir\oracle-job-agent.key"
$dest = "ubuntu@$VmIp"
$tmpKey = Join-Path (Get-Location) "oracle-job-agent.key.tmp"
if (Test-Path $tmpKey) {
  if (Test-Path $key) { icacls $key /grant:r "$($env:USERNAME):(F)" | Out-Null; Remove-Item $key -Force }
  Copy-Item $tmpKey $key; Remove-Item $tmpKey -Force; Remove-Item "$tmpKey.pub" -Force -ErrorAction SilentlyContinue
}
$cands = @(); if (Test-Path $key) { $cands += Get-Item $key }
$cands += @(Get-ChildItem -LiteralPath "$HOME\Downloads" -Filter "ssh-key-*.key" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
$found = $false
foreach ($c in $cands) {
  if ($c.FullName -ne $key) { if (Test-Path $key) { icacls $key /grant:r "$($env:USERNAME):(F)" | Out-Null; Remove-Item $key -Force }; Copy-Item -LiteralPath $c.FullName $key }
  icacls $key /inheritance:r /grant:r "$($env:USERNAME):(R)" | Out-Null
  ssh -i $key -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o LogLevel=ERROR -o ConnectTimeout=15 $dest "echo ok" 2>&1 | Tee-Object -Variable lastErr | Out-Null
  if ($LASTEXITCODE -eq 0) { Write-Host "Using key $($c.Name)"; $found = $true; break }
}
if (-not $found) { Write-Host "Keys tried: $($cands.Count)"; Write-Host "Last ssh message: $lastErr"; throw "No Downloads\ssh-key-*.key could log in to $VmIp (VM may still be starting, or port 22 is blocked)." }
$ssh = @("-i", $key, "-o", "StrictHostKeyChecking=accept-new", "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=15")

Write-Host "== Testing SSH =="
ssh @ssh $dest "uname -m; nproc; free -h; df -h /"

if (-not $SkipUpload) {
Write-Host "== Packing project (excluding .venv, .env, browser_profile, data, .git) =="
$tar = "$env:TEMP\jaa.tgz"
tar --exclude=.venv --exclude=.env --exclude=browser_profile --exclude=data --exclude=.git --exclude=__pycache__ --exclude=scratch -czf $tar .
scp @ssh $tar "${dest}:/tmp/jaa.tgz"
}

scp @ssh (Join-Path $PSScriptRoot "job-worker-xvfb.sh") "${dest}:/tmp/job-worker-xvfb.sh"
Write-Host "== Setting up VM =="
$remote = @'
set -e
if ! swapon --show | grep -q swapfile; then
  sudo fallocate -l 3G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi
sudo apt-get update -y && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y bash xvfb
cd /tmp && mkdir -p jaa_src && tar -xzf jaa.tgz -C jaa_src
sed -i 's/\r$//' jaa_src/scripts/remote/*.sh
sed -i 's/libasound2 /libasound2t64 /' jaa_src/scripts/remote/bootstrap_worker.sh
sed -i 's/python -m playwright install --with-deps chromium/python -m playwright install chromium/' jaa_src/scripts/remote/install_dependencies.sh
bash jaa_src/scripts/remote/bootstrap_worker.sh
sudo rsync -a jaa_src/ /opt/job-automation-agent/ 2>/dev/null || { sudo apt-get install -y rsync; sudo rsync -a jaa_src/ /opt/job-automation-agent/; }
sudo mkdir -p /opt/job-automation-agent/data
sudo chown -R jobagent:jobagent /opt/job-automation-agent
sudo -u jobagent bash /opt/job-automation-agent/scripts/remote/install_dependencies.sh
sed 's/^MemoryHigh=.*/MemoryHigh=750M/; s/^MemoryMax=.*/MemoryMax=900M/' /opt/job-automation-agent/scripts/remote/systemd/job-agent-worker.service | sudo tee /etc/systemd/system/job-agent-worker.service >/dev/null
sudo install -m 755 /tmp/job-worker-xvfb.sh /usr/local/bin/job-worker-xvfb.sh
sudo sed -i 's|^ExecStart=.*|ExecStart=/usr/local/bin/job-worker-xvfb.sh|' /etc/systemd/system/job-agent-worker.service
sudo systemctl daemon-reload
if systemctl is-enabled job-agent-worker >/dev/null 2>&1; then sudo systemctl restart job-agent-worker; fi
echo "Setup done. Copy .env then: sudo systemctl enable --now job-agent-worker"
'@
$remote = $remote -replace "`r", ""
ssh @ssh $dest $remote

if ($IncludeEnv) {
  scp @ssh ".env" "${dest}:/tmp/job.env"
  ssh @ssh $dest "sudo install -o jobagent -g jobagent -m 600 /tmp/job.env /opt/job-automation-agent/.env && rm /tmp/job.env && sudo systemctl enable --now job-agent-worker && systemctl is-active job-agent-worker"
}
if ($SyncData) {
  Write-Host "== Syncing resume, profile and saved answers (no databases, no logs) =="
  $files = @("Yaswanth_J__Cloud_Networking&Security_Resume.pdf","profile.json","profile_answers.json","profile.example.json","profile_answers.example.json","_answer_bank.json","_ai_models.json","_experience.json","_dropdown_answers.json","_dropdown_answers_collective.json","_dropdown_answers_quantum_health.json","next_project_knowledge.json")
  $present = $files | Where-Object { Test-Path (Join-Path "data" $_) }
  $dtar = "$env:TEMP\jaa_data.tgz"
  tar -C data -czf $dtar @present
  scp @ssh $dtar "${dest}:/tmp/jaa_data.tgz"
  ssh @ssh $dest "sudo -u jobagent mkdir -p /opt/job-automation-agent/data && sudo tar -xzf /tmp/jaa_data.tgz -C /opt/job-automation-agent/data && sudo chown -R jobagent:jobagent /opt/job-automation-agent/data && sudo chmod 600 /opt/job-automation-agent/data/profile*.json && rm /tmp/jaa_data.tgz && ls /opt/job-automation-agent/data"
}
Write-Host "Done."
