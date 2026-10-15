"""update_worker.sh must match how the Oracle VM is actually deployed to (f144).

An earlier version of this script assumed `git fetch && git checkout` would work
on the remote worker VM, and that `pkill` as the `ubuntu` SSH user could stop the
daemon. Neither held: /opt/job-automation-agent has no .git directory by design
(deploy_to_vm.ps1 packs and rsyncs the repo from Windows instead), and the daemon
runs as `jobagent` under systemd, not as `ubuntu` -- confirmed live against the
real Oracle worker, where both the git-fetch step and the pkill step failed
outright (git: "fatal: not a git repository"; pkill: "Operation not permitted").
"""
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "remote" / "update_worker.sh"


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_the_update_script_never_assumes_a_git_checkout_on_the_vm():
    text = _text()
    assert "git fetch" not in text
    assert "git checkout" not in text


def test_the_update_script_signals_the_daemon_through_systemd_not_a_bare_pkill():
    text = _text()
    assert "pkill" not in text
    assert "systemctl kill" in text or "systemctl stop" in text


def test_the_update_script_still_health_checks_and_restarts():
    text = _text()
    assert "health_check.sh" in text
    assert "systemctl restart" in text
