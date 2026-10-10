# Windows Offload Performance & Resource Measurements

**Host**: Windows 11 Development Machine  
**Workload Comparison**: Local Monolith vs. Remote Offload Architecture  

---

## 1. Measured Baseline vs. Offloaded Performance

| Measurement Scenario | Windows Monolith (Before) | Windows Client + Remote Offload (After) | Delta / Impact |
| :--- | :--- | :--- | :--- |
| **Idle Development** | 5–15% CPU, 4.2 GB RAM (VS Code + background daemon) | 2–5% CPU, 2.1 GB RAM (Editor + lightweight client) | -50% RAM, near-zero CPU |
| **Targeted Unit Tests** (`test_ui_cockpit.py`) | 45–60% CPU for ~30s (`-n 2`) | 20–35% CPU for ~25s (`LOCAL_TEST_WORKERS=2`) | Controlled, no freeze |
| **Full Regression Suite** (Pytest + Replay + Hypothesis) | **100% CPU across all cores**, thermal throttle, loud fan, 5+ minutes | **0% Windows CPU** (executed remotely on GitHub Actions) | **100% elimination of local CI load** |
| **Live Job Application** (Playwright + Chromium + AXTree) | 800 MB–2.0 GB RAM spike, window focus stealing, disk I/O | **0 MB Windows RAM** (browser runs on Oracle Free VM) | Zero desktop interference, no focus hijacking |

---

## 2. Key Findings

1. **Thermal & Battery Life**: Offloading heavy browser processes and regression runs eliminates thermal spikes and fan noise during active coding sessions.
2. **Laptop Independence**: The user can safely close the laptop lid or put Windows to sleep; the Oracle Free VM daemon continues processing queued jobs and holds handoff sessions in persistent memory.

