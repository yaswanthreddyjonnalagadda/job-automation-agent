import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import remote_cli
from ssh_remote_client import SshRemoteClient, SshRemoteError


def test_cli_enqueue_then_list(tmp_path):
    out = remote_cli.run(["enqueue", "--url", "https://example.com/job/1"], base_dir=tmp_path)
    assert out["ok"] and out["job_id"].startswith("job_")
    jobs = remote_cli.run(["jobs"], base_dir=tmp_path)["jobs"]
    assert [j["job_url"] for j in jobs] == ["https://example.com/job/1"]


def test_cli_workers_empty(tmp_path):
    assert remote_cli.run(["workers"], base_dir=tmp_path) == {"ok": True, "workers": []}


def _client(runner):
    return SshRemoteClient(host="203.0.113.5", key_path="k.key", runner=runner)


def test_requires_host_and_key(monkeypatch):
    monkeypatch.delenv("JOB_SSH_HOST", raising=False)
    monkeypatch.delenv("JOB_SSH_KEY", raising=False)
    with pytest.raises(SshRemoteError):
        SshRemoteClient()


def test_dispatch_builds_safe_ssh_command():
    seen = {}

    def runner(cmd, **kw):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "job_id": "job_1"}), stderr="")

    ok, msg, job_id = _client(runner).dispatch_application("https://x.test/a b; rm -rf /", priority=2)
    assert ok and job_id == "job_1"
    assert seen["cmd"][0] == "ssh" and "BatchMode=yes" in seen["cmd"]
    remote = seen["cmd"][-1]
    assert "'https://x.test/a b; rm -rf /'" in remote  # quoted, not executable


def test_dispatch_reports_unreachable():
    def runner(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    ok, msg, job_id = _client(runner).dispatch_application("https://x.test/a")
    assert not ok and job_id is None and "UNREACHABLE" in msg


def test_list_jobs_failure_raises():
    def runner(cmd, **kw):
        return SimpleNamespace(returncode=255, stdout="", stderr="Permission denied")

    with pytest.raises(SshRemoteError):
        _client(runner).list_queue_jobs()
