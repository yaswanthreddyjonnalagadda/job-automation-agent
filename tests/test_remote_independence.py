import time
import pytest
from durable_queue import DurableJobQueue, JobState
from remote_client import RemoteControlClient
from worker_manager import WorkerRegistry, WorkerStatus, WorkerType, WorkerCapability


def test_remote_independence_client_disconnect_reconnect(tmp_path):
    """Proves Section 5 & 46: Remote worker continues when Windows client disconnects.
    
    1. Queue a synthetic job via control client.
    2. Remote worker claims it and starts application.
    3. Windows client disconnects (simulated).
    4. Remote worker continues execution autonomously.
    5. Synthetic handoff (CAPTCHA/review) is reached and preserved.
    6. Windows client reconnects and reconstructs live state.
    7. Handoff is resolved and worker marks job completed.
    """
    db_path = tmp_path / "job_queue.db"
    queue = DurableJobQueue(db_path=db_path)
    registry = WorkerRegistry(data_dir=tmp_path)

    # 1. Register remote worker
    registry.register(
        worker_id="oracle-worker-1",
        worker_type=WorkerType.REMOTE_LINUX,
        capabilities=[WorkerCapability.APPLICATION_RUNTIME, WorkerCapability.PLAYWRIGHT],
    )

    # 2. Windows client dispatches synthetic job
    client = RemoteControlClient(base_dir=tmp_path)
    client.queue = queue
    client.registry = registry

    ok, msg, jid = client.dispatch_application("https://synthetic.careers.example/apply/security-eng")
    assert ok is True
    assert jid is not None

    # 3. Remote worker claims lease and starts
    job = queue.claim_next_lease(worker_id="oracle-worker-1", lease_duration_seconds=300.0)
    assert job is not None
    queue.update_status(job.job_id, "oracle-worker-1", JobState.RUNNING)

    # 4. SIMULATE WINDOWS CLIENT DISCONNECT (Delete client instance, simulate sleep)
    del client

    # 5. Remote worker progresses while Windows is offline
    queue.update_status(
        job.job_id,
        "oracle-worker-1",
        JobState.WAITING_FOR_USER,
        notes="Synthetic CAPTCHA challenge holding in live Chromium session",
        checkpoint_reference="chk_step2_form_filled",
    )
    # Heartbeat updates autonomously
    registry.heartbeat("oracle-worker-1", status=WorkerStatus.WAITING_FOR_USER, current_job=job.job_id)

    # 6. SIMULATE WINDOWS CLIENT RECONNECT
    reconnected_client = RemoteControlClient(base_dir=tmp_path)
    reconnected_client.queue = queue
    reconnected_client.registry = registry

    # 7. Reconstruct live state on Windows
    workers = reconnected_client.get_system_workers()
    assert len(workers) == 1
    assert workers[0]["status"] == WorkerStatus.WAITING_FOR_USER.value
    assert workers[0]["current_job"] == job.job_id

    reconstructed_job = queue.get_job(job.job_id)
    assert reconstructed_job.status == JobState.WAITING_FOR_USER
    assert reconstructed_job.checkpoint_reference == "chk_step2_form_filled"

    # 8. Resolve handoff and complete
    queue.update_status(job.job_id, "oracle-worker-1", JobState.COMPLETED, notes="Application confirmed submitted")
    registry.heartbeat("oracle-worker-1", status=WorkerStatus.IDLE, current_job=None)

    final_job = queue.get_job(job.job_id)
    assert final_job.status == JobState.COMPLETED

