"""Client control interface for the Windows laptop dashboard.

Allows the Windows machine to function primarily as a client/monitoring console,
dispatching work to the remote Oracle worker and managing local fallback safely.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from durable_queue import DurableJobQueue, JobState, QueuedJob
from worker_manager import WorkerCapability, WorkerInfo, WorkerRegistry, WorkerStatus, WorkerType


class RemoteControlClient:
    """Interface used by web_ui and CLI on Windows to interact with the execution layer."""

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or Path(__file__).resolve().parent
        self.data_dir = self.base_dir / "data"
        self.queue = DurableJobQueue(self.data_dir / "job_queue.db")
        self.registry = WorkerRegistry(self.data_dir)
        # When JOB_SSH_HOST / JOB_SSH_KEY are set, the queue and worker registry
        # live on the Oracle VM and are reached over SSH (nothing is exposed publicly).
        self._ssh = None
        is_project_dir = self.base_dir.resolve() == Path(__file__).resolve().parent
        if is_project_dir and os.environ.get("JOB_SSH_HOST") and os.environ.get("JOB_SSH_KEY"):
            from ssh_remote_client import SshRemoteClient
            self._ssh = SshRemoteClient()

    @property
    def local_fallback_enabled(self) -> bool:
        """True if the operator explicitly authorized local Windows execution."""
        return os.environ.get("ENABLE_LOCAL_FALLBACK", "0").lower() in ("1", "true", "yes")

    def get_system_workers(self) -> List[Dict[str, Any]]:
        """Returns health summaries of all known workers for the dashboard."""
        if self._ssh is not None:
            try:
                return self._ssh.get_system_workers()
            except Exception:
                return []
        workers = self.registry.list_all()
        return [w.as_dict() for w in workers]

    def get_active_remote_worker(self) -> Optional[WorkerInfo]:
        """Finds an online remote worker capable of application execution."""
        if self._ssh is not None:
            for d in self.get_system_workers():
                if (
                    d.get("worker_type") == WorkerType.REMOTE_LINUX.value
                    and d.get("status") not in (WorkerStatus.OFFLINE.value, WorkerStatus.UNHEALTHY.value)
                    and WorkerCapability.APPLICATION_RUNTIME.value in d.get("capabilities", [])
                    and d.get("seconds_since_heartbeat", 0) < 120
                ):
                    return WorkerInfo(
                        worker_id=d["worker_id"],
                        worker_type=WorkerType.REMOTE_LINUX,
                        hostname=d.get("hostname", ""),
                        architecture=d.get("architecture", ""),
                        capabilities=list(d.get("capabilities", [])),
                        status=WorkerStatus(d["status"]),
                        last_heartbeat=d.get("last_heartbeat", 0.0),
                        current_job=d.get("current_job"),
                        git_sha=d.get("git_sha", "unknown"),
                        metrics=d.get("metrics", {}),
                    )
            return None
        workers = self.registry.list_all()
        for w in workers:
            if w.worker_type == WorkerType.REMOTE_LINUX and w.status not in (WorkerStatus.OFFLINE, WorkerStatus.UNHEALTHY):
                if WorkerCapability.APPLICATION_RUNTIME.value in w.capabilities:
                    return w
        return None

    def dispatch_application(
        self,
        job_url: str,
        application_key: Optional[str] = None,
        priority: int = 0,
    ) -> Tuple[bool, str, Optional[str]]:
        """Submits an application URL to the system.
        
        Adheres to REMOTE_REQUIRED default policy: refuses local execution unless
        explicitly overridden with ENABLE_LOCAL_FALLBACK.
        """
        remote_w = self.get_active_remote_worker()
        if self._ssh is not None and remote_w:
            return self._ssh.dispatch_application(job_url, application_key, priority)
        if not remote_w and not self.local_fallback_enabled:
            return (
                False,
                "ORACLE WORKER OFFLINE: Remote execution is required. "
                "No applications will start on this laptop without explicit local fallback enabled.",
                None,
            )

        job = self.queue.enqueue(
            job_url=job_url,
            application_key=application_key,
            priority=priority,
            job_type="application",
            notes="Dispatched via control client",
        )

        target = "remote worker" if remote_w else "local fallback worker"
        return True, f"Application queued for {target} (Job ID: {job.job_id})", job.job_id

    def list_queue_jobs(self, limit: int = 50) -> List[Dict[str, Any]]:
        if self._ssh is not None:
            return self._ssh.list_queue_jobs(limit)
        return [j.as_dict() for j in self.queue.list_jobs(limit=limit)]

