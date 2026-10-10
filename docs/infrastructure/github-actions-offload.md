# GitHub Actions CI Offload Strategy

**Repository**: `yaswanthreddyjonnalagadda/job-automation-agent`  
**Visibility**: `PUBLIC`  
**Cost**: $0.00 (Unlimited free standard runner minutes)  

---

## 1. Workload Offload Rationale

Running the full pytest suite and the 616-page replay test suite on the Windows laptop pins CPU utilization to 100% across all cores for minutes at a time, degrading VS Code performance, inducing fan noise, and causing thermal throttling.

By shifting all comprehensive validation to GitHub-hosted Actions:
1. **Windows CPU load during development is reduced by ~80%**.
2. **Developers only run small, targeted unit tests locally** (e.g., `pytest tests/test_ui_cockpit.py`).
3. **Branch protection gates `main` on GitHub Actions pass**, guaranteeing full regression coverage before merges.

---

## 2. Workflow Specifications

The workflow in `.github/workflows/ci.yml` is configured with:
* **Hosted Runner**: `ubuntu-latest` (Standard 2-core runner, free on public repositories).
* **Container Services**: PostgreSQL 16 via Docker for database isolation.
* **Concurrency**: `cancel-in-progress: true` to prevent redundant builds on rapid pushes.
* **Caching**: `actions/setup-python` pip dependency caching.
* **Security & Secrets**: Real profile data injected only via repository secret `PROFILE_JSON` without logging to build stdout.

