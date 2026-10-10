import time
import pytest
from worker_manager import WorkerRegistry, WorkerInfo, WorkerStatus, WorkerType, WorkerCapability


def test_worker_registration(tmp_path):
    registry = WorkerRegistry(data_dir=tmp_path)
    worker = registry.register(
        worker_id="oracle-test-1",
        worker_type=WorkerType.REMOTE_LINUX,
        capabilities=[WorkerCapability.PLAYWRIGHT, WorkerCapability.APPLICATION_RUNTIME],
        hostname="oracle-vm",
        architecture="aarch64",
    )

    assert worker.worker_id == "oracle-test-1"
    assert worker.worker_type == WorkerType.REMOTE_LINUX
    assert worker.status == WorkerStatus.IDLE
    assert WorkerCapability.PLAYWRIGHT.value in worker.capabilities

    fetched = registry.get("oracle-test-1")
    assert fetched is not None
    assert fetched.hostname == "oracle-vm"


def test_worker_heartbeat_and_metrics(tmp_path):
    registry = WorkerRegistry(data_dir=tmp_path)
    registry.register(
        worker_id="oracle-test-1",
        worker_type=WorkerType.REMOTE_LINUX,
        capabilities=[WorkerCapability.PLAYWRIGHT],
    )

    updated = registry.heartbeat(
        worker_id="oracle-test-1",
        status=WorkerStatus.BUSY,
        metrics={"cpu_percent": 42.0, "memory_percent": 55.0},
        current_job="job_12345",
    )

    assert updated is not None
    assert updated.status == WorkerStatus.BUSY
    assert updated.current_job == "job_12345"
    assert updated.metrics["cpu_percent"] == 42.0


def test_worker_stale_detection(tmp_path):
    registry = WorkerRegistry(data_dir=tmp_path)
    worker = registry.register(
        worker_id="oracle-test-1",
        worker_type=WorkerType.REMOTE_LINUX,
        capabilities=[WorkerCapability.PLAYWRIGHT],
    )

    # Simulate old heartbeat
    worker.last_heartbeat = time.time() - 40.0
    registry.refresh_health_states(unhealthy_timeout_seconds=30.0, offline_timeout_seconds=60.0)
    assert registry.get("oracle-test-1").status == WorkerStatus.UNHEALTHY

    worker.last_heartbeat = time.time() - 70.0
    registry.refresh_health_states(unhealthy_timeout_seconds=30.0, offline_timeout_seconds=60.0)
    assert registry.get("oracle-test-1").status == WorkerStatus.OFFLINE


def test_worker_selection_by_capability(tmp_path):
    registry = WorkerRegistry(data_dir=tmp_path)
    registry.register(
        worker_id="windows-local",
        worker_type=WorkerType.LOCAL_WINDOWS,
        capabilities=[WorkerCapability.DEVELOPMENT],
    )
    registry.register(
        worker_id="oracle-remote",
        worker_type=WorkerType.REMOTE_LINUX,
        capabilities=[WorkerCapability.PLAYWRIGHT, WorkerCapability.APPLICATION_RUNTIME],
    )

    selected = registry.select_worker_for_job([WorkerCapability.APPLICATION_RUNTIME])
    assert selected is not None
    assert selected.worker_id == "oracle-remote"

    none_found = registry.select_worker_for_job([WorkerCapability.FULL_TEST_SUITE])
    assert none_found is None

