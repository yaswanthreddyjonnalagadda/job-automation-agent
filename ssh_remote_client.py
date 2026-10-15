"""Laptop-side client that reaches the Oracle worker over SSH.

Mirrors the methods the dashboard uses on RemoteControlClient, but reads and
writes the queue that lives on the VM by running remote_cli.py there. Nothing
is exposed publicly: authentication is by SSH key only.

Configuration (environment variables):
    JOB_SSH_HOST         VM public IP or DNS name (required)
    JOB_SSH_USER         default "ubuntu"
    JOB_SSH_KEY          path to the private key file (required)
    JOB_REMOTE_DIR       default "/opt/job-automation-agent"
    JOB_REMOTE_PYTHON    default "<JOB_REMOTE_DIR>/.venv/bin/python"
    JOB_REMOTE_RUN_AS    default "jobagent" (the service user that owns the queue files);
                         set empty to run as the SSH user
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple


class SshRemoteError(RuntimeError):
    pass


class SshRemoteClient:
    def __init__(
        self,
        host: Optional[str] = None,
        user: Optional[str] = None,
        key_path: Optional[str] = None,
        remote_dir: Optional[str] = None,
        python_path: Optional[str] = None,
        runner: Callable[..., Any] = subprocess.run,
        timeout: int = 30,
        run_as: Optional[str] = None,
    ):
        self.host = host or os.environ.get("JOB_SSH_HOST", "")
        self.user = user or os.environ.get("JOB_SSH_USER", "ubuntu")
        self.key_path = key_path or os.environ.get("JOB_SSH_KEY", "")
        self.remote_dir = remote_dir or os.environ.get("JOB_REMOTE_DIR", "/opt/job-automation-agent")
        self.python_path = (
            python_path
            or os.environ.get("JOB_REMOTE_PYTHON")
            or f"{self.remote_dir}/.venv/bin/python"
        )
        self.run_as = os.environ.get("JOB_REMOTE_RUN_AS", "jobagent") if run_as is None else run_as
        self.runner = runner
        self.timeout = timeout
        if not self.host or not self.key_path:
            raise SshRemoteError("JOB_SSH_HOST and JOB_SSH_KEY must be set.")

    def _ssh_command(self, remote_args: Sequence[str]) -> List[str]:
        remote = (
            f"cd {shlex.quote(self.remote_dir)} && "
            + ("sudo -n -u " + shlex.quote(self.run_as) + " " if self.run_as else "")
            + " ".join(shlex.quote(a) for a in [self.python_path, "remote_cli.py", *remote_args])
        )
        return [
            "ssh",
            "-i", self.key_path,
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=10",
            f"{self.user}@{self.host}",
            remote,
        ]

    def _call(self, remote_args: Sequence[str]) -> Dict[str, Any]:
        try:
            proc = self.runner(
                self._ssh_command(remote_args),
                capture_output=True, text=True, timeout=self.timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SshRemoteError(f"Cannot reach the Oracle worker: {exc}") from exc
        out = (proc.stdout or "").strip().splitlines()
        try:
            payload = json.loads(out[-1]) if out else {}
        except json.JSONDecodeError:
            payload = {}
        if proc.returncode != 0 or not payload.get("ok"):
            detail = payload.get("error") or (proc.stderr or "").strip() or "no output"
            raise SshRemoteError(f"Oracle worker command failed: {detail}")
        return payload

    def get_system_workers(self) -> List[Dict[str, Any]]:
        return self._call(["workers"])["workers"]

    def list_queue_jobs(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self._call(["jobs", "--limit", str(int(limit))])["jobs"]

    def dispatch_application(
        self, job_url: str, application_key: Optional[str] = None, priority: int = 0
    ) -> Tuple[bool, str, Optional[str]]:
        args = ["enqueue", "--url", job_url, "--priority", str(int(priority))]
        if application_key:
            args += ["--key", application_key]
        try:
            job_id = self._call(args)["job_id"]
        except SshRemoteError as exc:
            return False, f"ORACLE WORKER UNREACHABLE: {exc}", None
        return True, f"Application queued for remote worker (Job ID: {job_id})", job_id
