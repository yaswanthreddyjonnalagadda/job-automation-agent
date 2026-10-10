import pytest
from durable_queue import DurableJobQueue, JobState
from runtime_events import RuntimeEvent, EventName, _sanitize_metadata


def test_b1_worker_loss_cannot_duplicate_submit(tmp_path):
    """B1 Submission Authority: Worker restart or lease timeout must NOT duplicate submission."""
    queue = DurableJobQueue(db_path=tmp_path / "queue.db")

    job = queue.enqueue(job_url="https://jobs.example.com/apply")
    claimed = queue.claim_next_lease(worker_id="oracle-worker-1", lease_duration_seconds=-10.0)

    # Worker crashes / lease expires
    reconciled = queue.reconcile_expired_leases()
    assert reconciled == 1

    expired = queue.get_job(claimed.job_id)
    # MUST enter RECOVERY_REQUIRED, never automatically submit or auto-requeue
    assert expired.status == JobState.RECOVERY_REQUIRED


def test_b2_remote_captcha_no_bypass(tmp_path):
    """B2 Verification: CAPTCHA detected remotely must fail closed to human handoff."""
    from handoff import Handoff, CAPTCHA, resolve_active_handoff, set_active_handoff

    h = Handoff(
        category=CAPTCHA,
        reason="Cloudflare Turnstile challenge present on remote browser",
        required_action="Solve CAPTCHA in browser",
        resume_condition="Challenge completed",
    )
    set_active_handoff(h)

    # Verifier reports CAPTCHA is still on the page
    def mock_verifier():
        return False, "CAPTCHA widget still present on page"

    ok, msg = resolve_active_handoff(live_verification_fn=mock_verifier)
    assert ok is False
    assert "still present" in msg


def test_b3_b4_attempted_vs_verified():
    """B4 Invariant: Attempted actions must not be marked verified without positive evidence."""
    ev = RuntimeEvent.emit(
        event_name=EventName.NAVIGATION_ATTEMPTED,
        component="remote_worker",
        display_message="Navigating to employer page",
        is_verified=False,
    )
    assert ev.is_verified is False
    assert ev.evidence is None


def test_b5_remote_telemetry_sanitizes_secrets():
    """B5 Privacy: Remote worker telemetry must strip credentials, tokens, OTPs."""
    raw_meta = {
        "user_email": "candidate@example.com",
        "auth_token": "secret_jwt_xyz",
        "raw_html": "<div>password: 1234</div>",
        "safe_stage": "form_fill",
    }
    sanitized = _sanitize_metadata(raw_meta)
    assert sanitized["auth_token"] == "<redacted>"
    assert sanitized["raw_html"] == "<redacted>"
    assert sanitized["safe_stage"] == "form_fill"

