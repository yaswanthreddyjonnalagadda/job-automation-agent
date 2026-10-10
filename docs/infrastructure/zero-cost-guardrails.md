# Zero-Cost Guardrails & Billing Protections

**Policy Target**: $0.00 ongoing infrastructure cost.  
**Audited Services**: Oracle Cloud Infrastructure (OCI) Always Free & GitHub Actions Public CI.  

---

## 1. Inventory of Permitted Services

| Service | Specific Resource / Tier | Cost | Max Allocation Limit |
| :--- | :--- | :--- | :--- |
| **OCI Compute** | `VM.Standard.A1.Flex` (Ampere ARM) | $0.00 | 4 OCPU, 24 GB RAM |
| **OCI Storage** | Always Free Block Volume | $0.00 | 200 GB total (Configured: 100 GB boot) |
| **OCI Outbound Data** | Internet Egress | $0.00 | 10 TB / month (Agent uses < 10 GB) |
| **GitHub Actions** | Standard Hosted Runners (`ubuntu-latest`) | $0.00 | Unlimited on Public Repositories |

---

## 2. Hard Disallowed Actions & Prohibited Services

The following paths must NEVER be enabled:
* **No Paid Compute Shapes**: Never provision Intel or AMD Standard shapes (`VM.Standard3.*`, `VM.Standard.E4.*`).
* **No Paid GitHub Runners**: Never enable GitHub Actions larger runners (4-core, 8-core paid runners).
* **No Cloud Load Balancers**: Do not provision paid OCI Load Balancers or Network Load Balancers.
* **No Paid CAPTCHA Solvers**: Under Invariant B2, automated third-party CAPTCHA solvers (e.g. 2Captcha, Anti-Captcha) are strictly banned.
* **No Database as a Service**: Do not provision Autonomous Database or paid OCI MySQL. Use native on-instance SQLite / containerized PostgreSQL.

---

## 3. How the Owner Can Verify $0 Billing

1. **In OCI Console**:
   * Navigate to **Billing & Cost Management → Cost Analysis**.
   * Verify that **Current Month Spend** is **$0.00**.
   * Under **Budgets**, create an alert at threshold `$0.01` to be immediately emailed if any paid resource is accidentally enabled.
2. **In GitHub Settings**:
   * Navigate to **Settings → Billing and plans**.
   * Confirm **GitHub Actions** shows **$0.00 / 0 min billable**.

---

## 4. Decommissioning Procedure

To completely stop all remote resources:
1. In OCI Console: Navigate to **Compute → Instances** → Select `job-automation-worker-1` → Click **Terminate** (check *Permanently delete the attached boot volume*).
2. The system immediately reverts to local Windows execution if `ENABLE_LOCAL_FALLBACK=1` is provided.

