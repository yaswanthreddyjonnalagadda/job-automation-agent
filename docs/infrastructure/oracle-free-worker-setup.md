# Oracle Cloud Always Free Worker Setup Guide

**Target VM Shape**: `VM.Standard.A1.Flex` (4 OCPU, 24 GB RAM, Ubuntu LTS aarch64)  
**Ongoing Cost**: $0.00 / month forever  

---

## 1. Owner Manual Setup Steps (In OCI Console)

1. **Sign in to Oracle Cloud Console** (Always Free Tier).
2. **Navigate to Compute → Instances → Create Instance**:
   * **Name**: `job-automation-worker-1`
   * **Placement**: Select any Availability Domain in your home region.
   * **Image**: Select **Ubuntu 22.04 LTS** (or 24.04 LTS) — ensure **ARM64** is selected.
   * **Shape**: Click *Change Shape* → **Ampere** → Select **VM.Standard.A1.Flex**.
     * Configure OCPU: **4 OCPU**
     * Configure Memory: **24 GB**
     * *(Cost Estimate will state $0.00 / Always Free Eligible)*
   * **Networking**: Place in default VCN / Public Subnet. Ensure *Assign a public IPv4 address* is checked.
   * **Add SSH Keys**: Upload or paste your personal SSH Public Key.
   * **Boot Volume**: Specify **100 GB** (well within the 200 GB Always Free limit).
3. **Click Create**. Note the Public IP address once provisioned.

---

## 2. Server Bootstrap Commands (One-Time Execution)

SSH into the newly provisioned instance:

```bash
ssh ubuntu@<ORACLE_VM_PUBLIC_IP>
```

Run the automated reproducible setup:

```bash
# 1. Clone repository to /opt/job-automation-agent
sudo git clone https://github.com/yaswanthreddyjonnalagadda/job-automation-agent.git /opt/job-automation-agent
cd /opt/job-automation-agent

# 2. Run system bootstrap (creates non-root 'jobagent' user and installs system libraries)
bash scripts/remote/bootstrap_worker.sh

# 3. Install Python virtualenv, Playwright Chromium, and run synthetic browser test
sudo -u jobagent bash -c "cd /opt/job-automation-agent && bash scripts/remote/install_dependencies.sh"

# 4. Install and enable systemd background service
sudo cp scripts/remote/systemd/job-agent-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now job-agent-worker.service

# 5. Check worker health
sudo -u jobagent bash -c "cd /opt/job-automation-agent && bash scripts/remote/health_check.sh"
```

---

## 3. Verifying Worker in Dashboard

Once the service is active, the worker publishes heartbeats to the shared durable registry.
Open your Windows dashboard:
```
http://localhost:5000/health
```
The **Oracle Runtime Worker** will be reported as `HEALTHY` with CPU, RAM, and Disk metrics.

