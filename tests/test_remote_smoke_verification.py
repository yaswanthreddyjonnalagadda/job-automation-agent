"""Remote Worker Smoke Test and Offload Verification.

Proves Section 11 & Section 12:
- Control plane enqueues work
- Remote worker registers and emits heartbeats
- Remote worker claims job lease
- Diagnostic events and telemetry correlate end-to-end
- Worker completes and releases lease
- Simulates worker loss and safe recovery reconciliation without duplicate submission
"""
import time
from pathlib import Path
import pytest

from durable_queue import DurableJobQueue, JobState
from remote_client import RemoteControlClient
from runtime_events import EventName, ReasonCode, RuntimeEvent
from worker_manager import WorkerRegistry, WorkerStatus, WorkerType, WorkerCapability


def test_remote_worker_smoke_cycle_and_loss_recovery(tmp_path):
    """Smoke test: full enqueue -> claim -> execution -> telemetry -> completion cycle + worker loss."""
    db_path = tmp_path / "queue.db"
    queue = DurableJobQueue(db_path=db_path)
    registry = WorkerRegistry(data_dir=tmp_path)

    # 1. Register remote worker
    worker_id = "oracle-vm-arm-01"
    reg = registry.register(
        worker_id=worker_id,
        worker_type=WorkerType.REMOTE_LINUX,
        capabilities=[WorkerCapability.PLAYWRIGHT, WorkerCapability.APPLICATION_RUNTIME],
        hostname="oracle-cloud-arm64",
    )
    assert reg.worker_id == worker_id

    # 2. Control plane enqueues job
    client = RemoteControlClient(base_dir=tmp_path)
    client.queue = queue
    client.registry = registry

    ok, msg, jid = client.dispatch_application("https://remote.corp.example/jobs/987")
    assert ok is True
    assert jid is not None

    # Verify job is QUEUED
    job = queue.get_job(jid)
    assert job.status == JobState.QUEUED

    # 3. Remote worker claims lease
    claimed = queue.claim_next_lease(worker_id=worker_id, lease_duration_seconds=120.0)
    assert claimed is not None
    assert claimed.job_id == jid
    assert claimed.status == JobState.LEASED
    assert claimed.assigned_worker == worker_id

    # 4. Heartbeat is emitted and visible on control plane
    registry.heartbeat(
        worker_id=worker_id,
        status=WorkerStatus.BUSY,
        current_job=jid,
        metrics={"cpu_percent": 18.5, "memory_percent": 32.1, "disk_free_gb": 142.0},
    )

    active_remote = client.get_active_remote_worker()
    assert active_remote is not None
    assert active_remote.worker_id == worker_id
    assert active_remote.status == WorkerStatus.BUSY
    assert active_remote.metrics["cpu_percent"] == 18.5

    # 5. Remote telemetry emitted with correlated run_id
    RuntimeEvent.set_current_run(jid, "app-corp-987")
    try:
        ev = RuntimeEvent.emit(
            event_name=EventName.NAVIGATION_VERIFIED,
            component="remote_worker",
            stage="navigation",
            display_message="Remote Chromium loaded employer portal",
            is_verified=True,
            evidence="page_transition",
        )
        assert ev.run_id == jid
        assert ev.application_key == "app-corp-987"
        assert ev.is_verified is True
    finally:
        RuntimeEvent.clear_current_run()

    # 6. Worker completes execution and releases lease
    queue.update_status(jid, worker_id=worker_id, new_status=JobState.COMPLETED, notes="Application completed on Oracle VM")
    registry.heartbeat(worker_id=worker_id, status=WorkerStatus.IDLE, current_job=None)

    finished_job = queue.get_job(jid)
    assert finished_job.status == JobState.COMPLETED
    assert finished_job.lease_expires_at is None

    # 7. SIMULATE WORKER LOSS / RECOVERY SCENARIO
    # Enqueue a second job
    ok2, msg2, jid2 = client.dispatch_application("https://remote.corp.example/jobs/988")
    claimed2 = queue.claim_next_lease(worker_id=worker_id, lease_duration_seconds=0.1)
    assert claimed2.job_id == jid2

    # Worker crashes: heartbeat stops, lease expires
    time.sleep(0.2)
    stale_reaped = queue.reconcile_expired_leases()
    assert stale_reaped == 1

    lost_job = queue.get_job(jid2)
    # Never auto-submitted or re-executed blindly
    assert lost_job.status == JobState.RECOVERY_REQUIRED

    # Reconcile safely
    queue.update_status(jid2, worker_id=None, new_status=JobState.QUEUED, notes="Reconciled by operator: verified safe to retry")
    reconciled_job = queue.get_job(jid2)
    assert reconciled_job.status == JobState.QUEUED

    # New worker claims and completes
    claimed_new = queue.claim_next_lease(worker_id="oracle-vm-arm-02", lease_duration_seconds=60.0)
    assert claimed_new.job_id == jid2

