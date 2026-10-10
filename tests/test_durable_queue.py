import time
import pytest
from durable_queue import DurableJobQueue, JobState


def test_enqueue_and_claim_lease(tmp_path):
    queue = DurableJobQueue(db_path=tmp_path / "test_queue.db")

    job = queue.enqueue(
        job_url="https://jobs.example.com/sec_eng",
        application_key="app_123",
        priority=0,
    )
    assert job.status == JobState.QUEUED
    assert job.job_id.startswith("job_")

    claimed = queue.claim_next_lease(worker_id="oracle-worker-1", lease_duration_seconds=60.0)
    assert claimed is not None
    assert claimed.job_id == job.job_id
    assert claimed.status == JobState.LEASED
    assert claimed.assigned_worker == "oracle-worker-1"
    assert claimed.lease_expires_at > time.time()

    # Second claim returns None since queue is empty
    claimed2 = queue.claim_next_lease(worker_id="oracle-worker-2")
    assert claimed2 is None


def test_lease_renewal_and_completion(tmp_path):
    queue = DurableJobQueue(db_path=tmp_path / "test_queue.db")

    job = queue.enqueue(job_url="https://jobs.example.com/devops")
    claimed = queue.claim_next_lease(worker_id="oracle-worker-1", lease_duration_seconds=10.0)

    old_expires = claimed.lease_expires_at
    time.sleep(0.01)
    renewed = queue.renew_lease(claimed.job_id, worker_id="oracle-worker-1", extension_seconds=60.0)
    assert renewed is True

    updated_job = queue.get_job(claimed.job_id)
    assert updated_job.lease_expires_at > old_expires

    # Update to completed
    done = queue.update_status(
        job_id=claimed.job_id,
        worker_id="oracle-worker-1",
        new_status=JobState.COMPLETED,
        notes="All fields verified and submitted",
    )
    assert done is True

    final_job = queue.get_job(claimed.job_id)
    assert final_job.status == JobState.COMPLETED
    assert final_job.lease_expires_at is None


def test_b1_b3_lease_expiry_requires_recovery_not_blind_restart(tmp_path):
    """Section 9 Invariant: Expired leases transition to RECOVERY_REQUIRED.
    
    A worker crash or lease timeout must NEVER automatically re-run an in-flight
    consequential application from scratch.
    """
    queue = DurableJobQueue(db_path=tmp_path / "test_queue.db")

    job = queue.enqueue(job_url="https://jobs.example.com/principal_eng")
    # Claim with an already-expired lease
    claimed = queue.claim_next_lease(worker_id="oracle-worker-1", lease_duration_seconds=-5.0)
    assert claimed is not None

    # Reconcile expired leases
    reconciled_count = queue.reconcile_expired_leases()
    assert reconciled_count == 1

    reconciled_job = queue.get_job(claimed.job_id)
    assert reconciled_job.status == JobState.RECOVERY_REQUIRED
    assert "reconciliation" in reconciled_job.notes.lower()

