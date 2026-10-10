# Current Windows Resource Load Audit

**Date**: October 2026  
**Host Platform**: Windows 11 Desktop / Laptop  

---

## 1. Inventory of Current Local Workloads

The Windows development machine currently bears the compound load of simultaneous developer tooling, testing infrastructure, and live browser automation:

| Workload Component | Process Type | Typical Resource Impact (Observed) | Bottleneck Class |
| :--- | :--- | :--- | :--- |
| **Full Pytest Suite** (`pytest -n auto`) | 8–16 parallel Python worker processes (`pytest-xdist`) | 100% CPU utilization across all cores, 3–6 GB RAM | Thermal throttling, fan noise, editor freeze |
| **Replay Suite** (`test_finding_the_way.py`) | 616 saved pages, DOM parsing, JSON deserialization | High disk I/O, 1–2 GB RAM, 60–90 seconds CPU burn | Disk I/O & CPU |
| **Hypothesis Property Tests** | 45+ parameterized permutations (`test_handoff_observability.py`) | Sustained CPU saturation for ~60s | CPU compute |
| **Live Playwright Runs** (`apply_flow.py`) | Python runtime + Chromium process tree (Browser, GPU, Renderer, Utility) | 800 MB – 2.0 GB RAM per browser instance, background CPU | RAM capacity & Window focus conflicts |
| **Diagnostic Captures** | Screenshots (`.png`), DOM dumps (`.html`), AXTree JSON dumps | Rapid file read/write operations in `output/` and `data/` | Disk I/O |
| **UI Dashboard Server** | Flask server (`web_ui.py`) | 50–150 MB RAM, light CPU | Low |
| **Database Engines** | SQLite (`applications.db`) & local PostgreSQL container | Light to moderate disk writes | Low to moderate |

---

## 2. Workload Classification Matrix

```
┌────────────────────────────────────────────────────────────────────────┐
│                        WORKLOAD CLASSIFICATION                         │
├──────────────────────┬─────────────────────────┬───────────────────────┤
│    Classification    │       Components        │   Assigned Location   │
├──────────────────────┼─────────────────────────┼───────────────────────┤
│ LOCAL_REQUIRED       │ VS Code, Antigravity,   │ Windows Laptop        │
│                      │ Git, Dashboard client,  │ (Client Mode)         │
│                      │ targeted unit tests     │                       │
├──────────────────────┼─────────────────────────┼───────────────────────┤
│ REMOTE_RUNTIME       │ Real Playwright worker, │ Oracle Free VM        │
│                      │ Chromium sessions,      │ (Ampere A1 Flex)      │
│                      │ job queue daemon,       │                       │
│                      │ checkpoints, CAPTCHA/   │                       │
│                      │ MFA holding sessions    │                       │
├──────────────────────┼─────────────────────────┼───────────────────────┤
│ CI_ONLY              │ Full pytest suite,      │ GitHub Actions        │
│                      │ Replay tests (616 pgs), │ (Hosted Free Runner)  │
│                      │ Hypothesis property     │                       │
│                      │ tests, static checks    │                       │
├──────────────────────┼─────────────────────────┼───────────────────────┤
│ OPTIONALLY_REMOTE    │ Job sourcing/scraping,  │ Oracle Worker or      │
│                      │ resume tailoring        │ Windows on demand     │
└──────────────────────┴─────────────────────────┴───────────────────────┘
```

---

## 3. Local Resource Safeguards & Enforced Limits

To permanently protect the Windows laptop from thermal throttling and UI stutter:

1. **`LOCAL_MAX_JOB_WORKERS = 0` (Default)**:
   * The local laptop refuses to claim live application jobs by default.
   * Execution must be routed to the remote worker unless explicit override `ENABLE_LOCAL_FALLBACK=1` is provided.

2. **`LOCAL_MAX_BROWSER_WORKERS = 0` (Default)**:
   * Background headless browser worker processes are suppressed locally during normal operation.

3. **`LOCAL_TEST_WORKERS = 2`**:
   * Developers running targeted tests locally default to at most 2 parallel workers, avoiding `pytest -n auto` core saturation.

