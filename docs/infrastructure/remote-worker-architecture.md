# Remote Worker Architecture & Remote-Independent Control Plane

**Date**: October 2026  
**Status**: Implemented  

---

## 1. System Topology

```
             ┌────────────────────────────────────────────────────────┐
             │                     WINDOWS LAPTOP                     │
             │  • VS Code / Editor        • Dashboard (Client Mode)   │
             │  • Git / Antigravity       • Targeted unit tests (≤2)  │
             └────────────────────────────┬───────────────────────────┘
                                          │
                   Async Control State    │   Git Push (PR / Commit)
                 (SQLite / Event Stream)  │
                                          │
                   ┌──────────────────────┴──────────────────────┐
                   │                                             │
                   ▼                                             ▼
     ┌────────────────────────────┐               ┌────────────────────────────┐
     │      ORACLE FREE VM        │               │       GITHUB ACTIONS       │
     │   (VM.Standard.A1.Flex)    │               │    (Hosted Free Runner)    │
     ├────────────────────────────┤               ├────────────────────────────┤
     │ • 4 OCPU, 24 GB RAM        │               │ • Unlimited on Public Repo │
     │ • Persistent Worker Daemon │               │ • Full Pytest Suite        │
     │ • Durable Job Queue (DB)   │               │ • 616-page Replay Tests    │
     │ • Playwright & Chromium    │               │ • Hypothesis Property Tests│
     │ • Checkpoints & DB State   │               │ • Static & Security Checks │
     │ • Human Handoff Registry   │               │ • PostgreSQL Service       │
     └────────────────────────────┘               └────────────────────────────┘
```

---

## 2. Remote Independence Guarantee

The Oracle worker daemon operates independently of the Windows development machine:

1. **Autonomous Execution**: Once a job is queued in `data/job_queue.db`, the worker daemon leases and executes it. The Windows laptop can sleep, disconnect from WiFi, or shut down without interrupting the remote browser automation.
2. **Durable Handoff Preservation**: When a CAPTCHA or MFA challenge is encountered, the worker enters `WAITING_FOR_USER`, holds the exact live Chromium context open, and periodically renews its job lease.
3. **Client Reconnection**: When the Windows dashboard reconnects, `remote_client.py` reads the current durable state, reconstructs the Live Agent Cockpit, and presents the pending challenge for human resolution.
4. **B3 Recovery Reconciliation**: If the worker loses connection or reboots, jobs transition to `RECOVERY_REQUIRED`. Under invariant B3, the agent re-inspects live browser and checkpoint evidence before resuming. It **never** blindly reruns a consequential application from scratch.

---

## 3. Worker Capabilities & Lifecycle

* **Capabilities**:
  * `PLAYWRIGHT`: Controls headless or headed browser automation.
  * `PERSISTENT_BROWSER`: Maintains long-running browser context across handoff states.
  * `APPLICATION_RUNTIME`: Authorized to execute application form-filling and submission workflows.
  * `DIAGNOSTICS`: Writes sanitized forensic captures under B5.

* **Lifecycle States**:
  `REGISTERING` → `IDLE` ⇄ `BUSY` → `DRAINING` → `OFFLINE`
  (Pressure states: `PRESSURE`, `HIGH_PRESSURE`, `UNHEALTHY`)

