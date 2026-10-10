# Zero-Cost Cloud Provider Verification

**Date**: October 2026  
**Repository**: `yaswanthreddyjonnalagadda/job-automation-agent`  
**Visibility**: `PUBLIC` (Verified via GitHub API)

---

## 1. Oracle Cloud Infrastructure (OCI) Always Free Terms

### 1.1 Compute Resources (Always Free Tier)
* **Ampere A1 Compute (`VM.Standard.A1.Flex`)**:
  * **Architecture**: ARM64 (aarch64, Ampere Altra / Neoverse N1).
  * **Capacity Allocation**: Up to 4 OCPUs and 24 GB of RAM total per tenancy, usable as a single VM or split across up to 4 VMs.
  * **Cost**: $0.00 / month forever under Always Free.
  * **Operating System**: Ubuntu 22.04 LTS (aarch64) or Ubuntu 24.04 LTS (aarch64).
  * **ARM & Browser Compatibility**:
    * Python 3.11 / 3.12 / 3.13 are fully supported on Ubuntu aarch64.
    * Playwright officially supports Linux ARM64 (Chromium build available via `playwright install chromium` on aarch64).
  * **AMD Micro Compute (`VM.Standard.E2.1.Micro`)**:
    * 1/8 OCPU, 1 GB RAM (x86_64).
    * Free up to 2 instances, but 1 GB RAM is insufficient for sustained headless Chromium + Python + diagnostics without high memory pressure.
  * **Selected Architecture**: **1 instance of `VM.Standard.A1.Flex` with 4 OCPU, 24 GB RAM, Ubuntu LTS**.

### 1.2 Storage & Network Allowances
* **Block Storage**: 200 GB total Always Free block storage (including boot volumes).
  * Recommended allocation: 100 GB boot volume for the worker instance. Leaves 100 GB headroom.
* **Outbound Data Transfer**: 10 TB per month Always Free. (Job automation agent uses < 10 GB/mo).
* **Public IPv4**: 1 Always Free ephemeral or reserved public IPv4 address.

### 1.3 Billing Hazards & Avoidance Rules
| Risk Path | Mechanism | Mandatory Protection Rule |
| :--- | :--- | :--- |
| **Paid Shape Selection** | Selecting an Intel/AMD Standard shape (e.g. `VM.Standard3.Flex` or `VM.Standard.E4.Flex`) incurs hourly compute charges. | **Never provision any shape other than `VM.Standard.A1.Flex`** (or `VM.Standard.E2.1.Micro`). Explicitly assert shape name in bootstrap scripts. |
| **RAM / OCPU Over-allocation** | Allocating > 4 OCPUs or > 24 GB RAM triggers paid billing. | Hardcode allocation limits: 4 OCPUs, 24 GB RAM max per tenancy. |
| **Storage Over-allocation** | Provisioning boot + block volumes exceeding 200 GB total incurs storage charges. | Boot volume size restricted to 50 GB – 100 GB. |
| **Idle Instance Reclamation** | Oracle may reclaim Always Free compute instances if CPU utilization is < 20% over 7 days and network is < 20%. | The worker daemon heartbeats, periodic health checks, and scheduled discovery prevent idle reclamation safely at $0. |
| **Paid Service Add-ons** | OCI Load Balancers (> 1 free), NAT Gateways, Vault secrets with KMS pricing, or paid DNS services. | **No paid network components**. Use direct SSH key access and native Ubuntu `ufw`. |

---

## 2. GitHub Actions Provider Verification

### 2.1 Repository Visibility: PUBLIC
* Verified via GitHub API: `{"isPrivate": false, "visibility": "PUBLIC"}`.

### 2.2 GitHub-Hosted Runner Usage & Limits
* **Usage Fee**: **$0.00 (Completely Free)** for public repositories on standard GitHub-hosted runners (`ubuntu-latest`, `windows-latest`, `macos-latest`).
* **Concurrency**: Up to 20 concurrent jobs on standard free accounts for public repositories.
* **Monthly Minutes**: **Unlimited** on public repositories using standard runners.
* **Job Execution Limit**: 6 hours maximum per job; 72 hours per workflow run.
* **Storage / Artifacts**: Free up to 500 MB per repository on public repositories (with automatic retention aging).

### 2.3 Offload Strategy
* **Continuous Integration & Heavy Test Suite**: Run completely on GitHub-hosted `ubuntu-latest` runners for all pull requests and pushes to `main`.
* **Zero Cost**: Incurring $0 ongoing cost, zero private minute consumption, and zero load on the local Windows laptop.
* **Self-Hosted Runner Option**: Documented and supported on the Oracle worker if the repository is ever converted to private in the future.

