"""Remote worker daemon process running on the Oracle Free VM.

Continuously claims leased jobs, publishes heartbeats, monitors resource limits,
and preserves browser sessions for human handoffs (CAPTCHA/MFA) independently
of the Windows development client.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from durable_queue import DurableJobQueue, JobState, QueuedJob
from resource_manager import HealthCategory, ResourceManager, WorkloadPriority
from worker_manager import WorkerCapability, WorkerInfo, WorkerRegistry, WorkerStatus, WorkerType

logger = logging.getLogger("worker_daemon")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s")


class WorkerDaemon:
    """Persistent execution daemon running on the remote worker host."""

    def __init__(
        self,
        worker_id: str = "oracle-worker-1",
        worker_type: WorkerType = WorkerType.REMOTE_LINUX,
        poll_interval_seconds: float = 5.0,
        heartbeat_interval_seconds: float = 10.0,
        lease_duration_seconds: float = 300.0,
        base_dir: Optional[Path] = None,
    ):
        self.worker_id = worker_id
        self.worker_type = worker_type
        self.poll_interval = poll_interval_seconds
        self.heartbeat_interval = heartbeat_interval_seconds
        self.lease_duration = lease_duration_seconds
        self.base_dir = base_dir or Path(__file__).resolve().parent

        self.queue = DurableJobQueue(self.base_dir / "data" / "job_queue.db")
        self.registry = WorkerRegistry(self.base_dir / "data")
        self.resources = ResourceManager(self.base_dir)

        self._running = False
        self._draining = False
        self._active_job: Optional[QueuedJob] = None
        self._active_process: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()

    def start(self) -> None:
        """Starts the worker daemon loop and background heartbeat thread."""
        self._running = True

        # Register worker capabilities
        capabilities = [
            WorkerCapability.PLAYWRIGHT,
            WorkerCapability.PERSISTENT_BROWSER,
            WorkerCapability.APPLICATION_RUNTIME,
            WorkerCapability.DIAGNOSTICS,
        ]
        self.registry.register(
            worker_id=self.worker_id,
            worker_type=self.worker_type,
            capabilities=capabilities,
        )
        logger.info("Worker %s registered with capabilities %s", self.worker_id, capabilities)

        # Start background heartbeat thread
        hb_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        hb_thread.start()

        # Main execution loop
        try:
            self._main_loop()
        except KeyboardInterrupt:
            logger.info("Shutdown signal received.")
        finally:
            self.stop()

    def stop(self) -> None:
        """Stops the daemon safely."""
        with self._lock:
            self._running = False
            self.registry.heartbeat(self.worker_id, status=WorkerStatus.OFFLINE)
        logger.info("Worker %s stopped.", self.worker_id)

    def drain(self) -> None:
        """Enters draining mode: finishes active application without accepting new jobs."""
        logger.info("Worker %s entering DRAINING mode.", self.worker_id)
        with self._lock:
            self._draining = True
            self.registry.heartbeat(self.worker_id, status=WorkerStatus.DRAINING)

    def _heartbeat_loop(self) -> None:
        while self._running:
            try:
                metrics = self.resources.sample_metrics(
                    browser_contexts=1 if self._active_job else 0,
                    active_jobs=1 if self._active_job else 0,
                )
                health = self.resources.classify_health(metrics)

                with self._lock:
                    if self._draining:
                        st = WorkerStatus.DRAINING
                    elif health == HealthCategory.CRITICAL:
                        st = WorkerStatus.UNHEALTHY
                    elif health in (HealthCategory.PRESSURE, HealthCategory.HIGH_PRESSURE):
                        st = WorkerStatus.PRESSURE
                    elif self._active_job:
                        st = WorkerStatus.BUSY
                    else:
                        st = WorkerStatus.IDLE

                    current_jid = self._active_job.job_id if self._active_job else None
                    self.registry.heartbeat(self.worker_id, status=st, metrics=metrics, current_job=current_jid)

                    # Renew active lease while running
                    if self._active_job:
                        self.queue.renew_lease(self._active_job.job_id, self.worker_id, self.lease_duration)

            except Exception as e:
                logger.error("Heartbeat error: %s", e)

            time.sleep(self.heartbeat_interval)

    def _main_loop(self) -> None:
        while self._running:
            if self._draining:
                if not self._active_job:
                    logger.info("Draining complete; worker idle.")
                    break

            # Check resource admission before claiming a job
            can_run, reason = self.resources.can_schedule(WorkloadPriority.P0_ACTIVE_APPLICATION)
            if not can_run:
                logger.warning("Resource throttle: %s; sleeping.", reason)
                time.sleep(self.poll_interval)
                continue

            # Try to claim next job from queue
            job = self.queue.claim_next_lease(self.worker_id, self.lease_duration)
            if not job:
                time.sleep(self.poll_interval)
                continue

            logger.info("Claimed job %s (%s)", job.job_id, job.job_url)
            with self._lock:
                self._active_job = job
                self.queue.update_status(job.job_id, self.worker_id, JobState.RUNNING)

            self._execute_job(job)

            with self._lock:
                self._active_job = None

            time.sleep(1.0)

    def _execute_job(self, job: QueuedJob) -> None:
        """Executes apply.py in a dedicated subprocess for the claimed job."""
        cmd = [sys.executable, "apply.py", job.job_url, "--run-id", job.job_id]
        logger.info("Launching execution: %s", " ".join(cmd))

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(self.base_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            with self._lock:
                self._active_process = proc

            # Monitor execution output
            while proc.poll() is None:
                # Renew lease periodically
                self.queue.renew_lease(job.job_id, self.worker_id, self.lease_duration)
                time.sleep(2.0)

            ret = proc.returncode
            if ret == 0:
                logger.info("Job %s completed successfully.", job.job_id)
                self.queue.update_status(job.job_id, self.worker_id, JobState.COMPLETED, notes="Application completed")
            else:
                logger.warning("Job %s finished with return code %d", job.job_id, ret)
                self.queue.update_status(job.job_id, self.worker_id, JobState.FAILED, notes=f"Process exited {ret}")

        except Exception as exc:
            logger.error("Job %s failed with exception: %s", job.job_id, exc)
            self.queue.update_status(job.job_id, self.worker_id, JobState.FAILED, notes=str(exc))
        finally:
            with self._lock:
                self._active_process = None


def main() -> None:
    wid = os.environ.get("JOB_WORKER_ID", "oracle-worker-1")
    wtype_str = os.environ.get("JOB_WORKER_TYPE", "REMOTE_LINUX")
    wtype = WorkerType.REMOTE_LINUX if wtype_str == "REMOTE_LINUX" else WorkerType.LOCAL_WINDOWS
    daemon = WorkerDaemon(worker_id=wid, worker_type=wtype)

    def handle_sigterm(signum, frame):
        logger.info("SIGTERM received; draining worker...")
        daemon.drain()

    signal.signal(signal.SIGTERM, handle_sigterm)
    daemon.start()


if __name__ == "__main__":
    main()

