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

    @property
    def local_fallback_enabled(self) -> bool:
        """True if the operator explicitly authorized local Windows execution."""
        return os.environ.get("ENABLE_LOCAL_FALLBACK", "0").lower() in ("1", "true", "yes")

    def get_system_workers(self) -> List[Dict[str, Any]]:
        """Returns health summaries of all known workers for the dashboard."""
        workers = self.registry.list_all()
        return [w.as_dict() for w in workers]

    def get_active_remote_worker(self) -> Optional[WorkerInfo]:
        """Finds an online remote worker capable of application execution."""
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
        return [j.as_dict() for j in self.queue.list_jobs(limit=limit)]

