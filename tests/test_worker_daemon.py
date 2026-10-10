"""The worker daemon must never lose a failed job's reason, and must never hang
draining a chatty one.

_execute_job() used to discard the apply.py subprocess's combined stdout/stderr
entirely -- piped, but nothing ever read it. Two consequences: a failed job's
queue notes said only "Process exited 1", with no way to tell why; and a job
that logged more than the OS pipe buffer (commonly 64 KiB) would deadlock the
child on its own write() with nothing left to read it, hanging for the full
lease duration instead of finishing or failing (f143).
"""
import subprocess
import sys

from durable_queue import DurableJobQueue, JobState
from worker_daemon import WorkerDaemon


def make_daemon(tmp_path):
    (tmp_path / "data").mkdir(exist_ok=True)
    return WorkerDaemon(worker_id="test-worker", base_dir=tmp_path,
                        lease_duration_seconds=60.0)


def test_a_failed_jobs_notes_carry_a_sanitized_reason_not_just_the_exit_code(tmp_path):
    (tmp_path / "apply.py").write_text(
        "import sys\n"
        "print('STARTED: fake run')\n"
        "print('UNHANDLED_ERROR: could not read a job description, secret=hunter2')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    daemon = make_daemon(tmp_path)
    daemon.queue.enqueue(job_url="https://jobs.example.com/x", job_type="application")
    job = daemon.queue.claim_next_lease(daemon.worker_id, daemon.lease_duration)

    daemon._execute_job(job)

    updated = daemon.queue.get_job(job.job_id)
    assert updated.status == JobState.FAILED
    assert "Process exited 1" in updated.notes
    assert "UNHANDLED_ERROR" in updated.notes          # the category survives
    assert "hunter2" not in updated.notes              # the raw secret does not
    assert "could not read a job description" not in updated.notes


def test_a_successful_job_still_reports_plainly(tmp_path):
    (tmp_path / "apply.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    daemon = make_daemon(tmp_path)
    daemon.queue.enqueue(job_url="https://jobs.example.com/y", job_type="application")
    job = daemon.queue.claim_next_lease(daemon.worker_id, daemon.lease_duration)

    daemon._execute_job(job)

    updated = daemon.queue.get_job(job.job_id)
    assert updated.status == JobState.COMPLETED
    assert updated.notes == "Application completed"


def test_drain_output_keeps_up_with_a_job_that_logs_past_the_pipe_buffer(tmp_path):
    """Proof the deadlock this fixes is real: write well past a typical 64 KiB pipe
    buffer, draining concurrently with the wait as _execute_job now does, and the
    process must still exit promptly -- not hang because nothing read its output."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys\nfor _ in range(20000): print('x'*20)\nsys.exit(0)"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    )
    lines: list = []
    import threading
    reader = threading.Thread(target=WorkerDaemon._drain_output, args=(proc, lines), daemon=True)
    reader.start()
    proc.wait(timeout=15)          # would hang/timeout here before the fix
    reader.join(timeout=5)

    assert proc.returncode == 0
    assert len(lines) == 20000
