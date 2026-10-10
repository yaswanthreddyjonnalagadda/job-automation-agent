"""Generic worker management, capabilities, and heartbeat monitoring.

Supports multi-host execution across Local Windows, Remote Linux (Oracle Free VM),
and CI environments without coupling runtime logic to a specific cloud provider.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from resource_manager import HealthCategory, ResourceMetrics


class WorkerType(str, Enum):
    LOCAL_WINDOWS = "LOCAL_WINDOWS"
    REMOTE_LINUX = "REMOTE_LINUX"
    CI_WORKER = "CI_WORKER"


class WorkerStatus(str, Enum):
    REGISTERING = "REGISTERING"
    IDLE = "IDLE"
    BUSY = "BUSY"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    PRESSURE = "PRESSURE"
    HIGH_PRESSURE = "HIGH_PRESSURE"
    DRAINING = "DRAINING"
    UNHEALTHY = "UNHEALTHY"
    OFFLINE = "OFFLINE"


class WorkerCapability(str, Enum):
    PLAYWRIGHT = "PLAYWRIGHT"
    PERSISTENT_BROWSER = "PERSISTENT_BROWSER"
    APPLICATION_RUNTIME = "APPLICATION_RUNTIME"
    FULL_TEST_SUITE = "FULL_TEST_SUITE"
    REPLAY = "REPLAY"
    DIAGNOSTICS = "DIAGNOSTICS"
    DEVELOPMENT = "DEVELOPMENT"


def detect_git_sha(base_dir: Optional[Path] = None) -> str:
    """Safely retrieves current git commit SHA without raising exceptions."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(base_dir or Path(__file__).resolve().parent),
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return "unknown"


@dataclass
class WorkerInfo:
    worker_id: str
    worker_type: WorkerType
    hostname: str
    architecture: str
    capabilities: List[str]
    status: WorkerStatus = WorkerStatus.REGISTERING
    last_heartbeat: float = field(default_factory=time.time)
    current_job: Optional[str] = None
    git_sha: str = "unknown"
    python_version: str = platform.python_version()
    playwright_version: str = "unknown"
    os_name: str = platform.system()
    metrics: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "worker_type": self.worker_type.value if isinstance(self.worker_type, Enum) else self.worker_type,
            "hostname": self.hostname,
            "architecture": self.architecture,
            "capabilities": list(self.capabilities),
            "status": self.status.value if isinstance(self.status, Enum) else self.status,
            "last_heartbeat": self.last_heartbeat,
            "seconds_since_heartbeat": round(max(0.0, time.time() - self.last_heartbeat), 1),
            "current_job": self.current_job,
            "git_sha": self.git_sha,
            "python_version": self.python_version,
            "playwright_version": self.playwright_version,
            "os_name": self.os_name,
            "metrics": self.metrics,
            "metadata": self.metadata,
        }


class WorkerRegistry:
    """Thread-safe registry for worker discovery, health, and assignment."""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or (Path(__file__).resolve().parent / "data")
        self._lock = threading.RLock()
        self._workers: Dict[str, WorkerInfo] = {}
        self._storage_path = self.data_dir / "workers.json"
        self._load()

    def _load(self) -> None:
        if not self._storage_path.is_file():
            return
        try:
            with open(self._storage_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for wid, item in raw.items():
                wtype = WorkerType(item.get("worker_type", WorkerType.REMOTE_LINUX))
                wstatus = WorkerStatus(item.get("status", WorkerStatus.OFFLINE))
                self._workers[wid] = WorkerInfo(
                    worker_id=item["worker_id"],
                    worker_type=wtype,
                    hostname=item.get("hostname", "unknown"),
                    architecture=item.get("architecture", "unknown"),
                    capabilities=item.get("capabilities", []),
                    status=wstatus,
                    last_heartbeat=item.get("last_heartbeat", 0.0),
                    current_job=item.get("current_job"),
                    git_sha=item.get("git_sha", "unknown"),
                    python_version=item.get("python_version", ""),
                    playwright_version=item.get("playwright_version", ""),
                    os_name=item.get("os_name", ""),
                    metrics=item.get("metrics", {}),
                    metadata=item.get("metadata", {}),
                )
        except Exception:
            pass

    def _save(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            temp = self._storage_path.with_suffix(".tmp")
            data = {wid: w.as_dict() for wid, w in self._workers.items()}
            with open(temp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            temp.replace(self._storage_path)
        except Exception:
            pass

    def register(
        self,
        worker_id: str,
        worker_type: WorkerType | str,
        capabilities: List[WorkerCapability | str],
        hostname: Optional[str] = None,
        architecture: Optional[str] = None,
        git_sha: Optional[str] = None,
        playwright_version: str = "unknown",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> WorkerInfo:
        """Registers a worker in the pool."""
        wt = WorkerType(worker_type) if not isinstance(worker_type, WorkerType) else worker_type
        caps = [c.value if isinstance(c, Enum) else str(c) for c in capabilities]

        info = WorkerInfo(
            worker_id=worker_id,
            worker_type=wt,
            hostname=hostname or platform.node(),
            architecture=architecture or platform.machine(),
            capabilities=caps,
            status=WorkerStatus.IDLE,
            last_heartbeat=time.time(),
            git_sha=git_sha or detect_git_sha(),
            python_version=platform.python_version(),
            playwright_version=playwright_version,
            os_name=platform.system(),
            metadata=metadata or {},
        )

        with self._lock:
            self._workers[worker_id] = info
            self._save()
        return info

    def heartbeat(
        self,
        worker_id: str,
        status: Optional[WorkerStatus | str] = None,
        metrics: Optional[ResourceMetrics | Dict[str, Any]] = None,
        current_job: Optional[str] = None,
    ) -> Optional[WorkerInfo]:
        """Records a health heartbeat from a worker."""
        with self._lock:
            w = self._workers.get(worker_id)
            if not w:
                return None

            w.last_heartbeat = time.time()
            if status:
                st = WorkerStatus(status) if not isinstance(status, WorkerStatus) else status
                w.status = st
            if current_job is not None:
                w.current_job = current_job or None

            if metrics:
                w.metrics = metrics.as_dict() if isinstance(metrics, ResourceMetrics) else dict(metrics)

            self._save()
            return w

    def get(self, worker_id: str) -> Optional[WorkerInfo]:
        with self._lock:
            self.refresh_health_states()
            return self._workers.get(worker_id)

    def list_all(self) -> List[WorkerInfo]:
        with self._lock:
            self.refresh_health_states()
            return list(self._workers.values())

    def refresh_health_states(
        self,
        unhealthy_timeout_seconds: float = 30.0,
        offline_timeout_seconds: float = 60.0,
    ) -> None:
        """Marks workers UNHEALTHY or OFFLINE if heartbeats are stale."""
        now = time.time()
        changed = False
        with self._lock:
            for w in self._workers.values():
                elapsed = now - w.last_heartbeat
                if elapsed > offline_timeout_seconds and w.status != WorkerStatus.OFFLINE:
                    w.status = WorkerStatus.OFFLINE
                    changed = True
                elif elapsed > unhealthy_timeout_seconds and w.status not in (WorkerStatus.OFFLINE, WorkerStatus.UNHEALTHY):
                    w.status = WorkerStatus.UNHEALTHY
                    changed = True
            if changed:
                self._save()

    def select_worker_for_job(
        self,
        required_capabilities: List[WorkerCapability | str],
        prefer_remote: bool = True,
    ) -> Optional[WorkerInfo]:
        """Selects an eligible, healthy, non-draining worker for a job."""
        self.refresh_health_states()
        req_set = {c.value if isinstance(c, Enum) else str(c) for c in required_capabilities}

        with self._lock:
            candidates: List[WorkerInfo] = []
            for w in self._workers.values():
                if w.status not in (WorkerStatus.IDLE, WorkerStatus.PRESSURE):
                    continue
                if not req_set.issubset(set(w.capabilities)):
                    continue
                candidates.append(w)

            if not candidates:
                return None

            if prefer_remote:
                remotes = [c for c in candidates if c.worker_type == WorkerType.REMOTE_LINUX]
                if remotes:
                    return remotes[0]

            return candidates[0]

