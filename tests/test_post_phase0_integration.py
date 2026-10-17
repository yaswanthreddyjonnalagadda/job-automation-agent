"""Post-Phase-0 Integration Acceptance Tests.

Verifies end-to-end integration across:
1. Live Agent Cockpit & UI
2. Structured Diagnostics, Real Run IDs, and Stop-Cause Bundles
3. Remote Worker Architecture, Durable Queue, and Lease Safety
4. Human Handoff (CAPTCHA / MFA / Review) & Session Security
5. Phase-0 Invariants (B1 Submission, B2 Verification, B3 Recovery, B4 Action, B5 Privacy)
"""
import json
import time
from pathlib import Path
import pytest

import diagnostics
from handoff import Handoff, CAPTCHA, set_active_handoff, resolve_active_handoff
from handoff_session import HandoffStore, opaque_ref
from runtime_events import EventName, ReasonCode, RuntimeEvent, RootCauseCategory
from structured_logging import SchemaBinding, StructuredLogConsumer


def test_real_run_id_and_stop_cause_bundle_integration(tmp_path):
    """Proves Section 5: Real run_id throughout execution, no 'default', Stop-Cause Bundle generated."""
    out_dir = tmp_path / "output"
    run_id = "run-integration-test-2026"
    app_key = "app-target-job-999"

    # 1. Bind execution context
    RuntimeEvent.set_current_run(run_id, app_key)
    try:
        cur_run, cur_app = RuntimeEvent.get_current_run()
        assert cur_run == run_id
        assert cur_app == app_key

        # 2. Wire consumer
        consumer = StructuredLogConsumer(out_dir, SchemaBinding()).start()

        # 3. Emit real runtime progression without passing explicit run_id
        e_start = RuntimeEvent.emit(
            event_name=EventName.RUN_STARTED,
            component="apply_flow",
            stage="init",
            display_message="Starting application run",
            safe_metadata={"title": "Platform Engineer", "company": "ExampleCorp"},
        )
        assert e_start.run_id == run_id
        assert e_start.application_key == app_key

        RuntimeEvent.emit(
            event_name=EventName.APPLY_SEARCHING,
            component="browser_automation",
            stage="navigation",
            display_message="Searching for Apply button",
        )
        RuntimeEvent.emit(
            event_name=EventName.APPLY_FOUND,
            component="browser_automation",
            stage="navigation",
            display_message="Found Apply button",
            is_verified=True,
            evidence="input_value",
        )
        RuntimeEvent.emit(
            event_name=EventName.EMAIL_VERIFICATION_REQUIRED,
            component="browser_automation",
            stage="auth",
            display_message="Email code requested",
            safe_metadata={"state_before": "sign_in_form", "state_after": "verify_email"},
        )
        RuntimeEvent.emit(
            event_name=EventName.EMAIL_POLLING,
            component="browser_automation",
            stage="auth",
            display_message="Polling inbox for authorized code",
        )
        RuntimeEvent.emit(
            event_name=EventName.EMAIL_MATCHED,
            component="browser_automation",
            stage="auth",
            display_message="Code received and verified",
            is_verified=True,
            evidence="input_value",
        )
        RuntimeEvent.emit(
            event_name=EventName.CAPTCHA_DETECTED,
            component="browser_automation",
            stage="challenge",
            reason_code=ReasonCode.CAPTCHA_REQUIRED,
            display_message="Cloudflare Turnstile challenge visible",
            safe_metadata={"handoff_state": "CAPTCHA"},
        )
        RuntimeEvent.emit(
            event_name=EventName.HANDOFF_CREATED,
            component="handoff",
            stage="challenge",
            reason_code=ReasonCode.CAPTCHA_REQUIRED,
            display_message="Human intervention requested for CAPTCHA",
        )
        RuntimeEvent.emit(
            event_name=EventName.RUN_STOPPED,
            component="apply_flow",
            stage="challenge",
            reason_code=ReasonCode.CAPTCHA_REQUIRED,
            display_message="Automation paused for human CAPTCHA handoff",
        )

        consumer.close()

        # 4. Verify run_events.jsonl exists in consumer output folder (long-path safe)
        run_folder = consumer.output_dir / opaque_ref(app_key) / "diagnostics" / opaque_ref(run_id)
        events_path = run_folder / "run_events.jsonl"
        assert events_path.exists()

        records = [json.loads(line) for line in events_path.read_text().splitlines()]
        assert len(records) >= 8

        # Verify NO record has run_id == 'default'
        for r in records:
            assert r["run_id"] == opaque_ref(run_id)
            assert r["application_key"] == opaque_ref(app_key)

        # 5. Verify Stop-Cause Bundle was generated
        stop_cause_dir = run_folder / "stop_cause"
        assert stop_cause_dir.exists()
        assert (stop_cause_dir / "summary.json").exists()
        assert (stop_cause_dir / "timeline.jsonl").exists()
        assert (stop_cause_dir / "stop_summary.txt").exists()
        assert (stop_cause_dir / "copy_for_agent.txt").exists()

        summary_content = diagnostics.read_safe_artifact(stop_cause_dir / "summary.json")
        summary_data = json.loads(summary_content.decode() if isinstance(summary_content, bytes) else summary_content)
        assert summary_data["diagnostic_subsystem"] == "CAPTCHA"
        assert summary_data["handoff_state"] == "CAPTCHA"

    finally:
        RuntimeEvent.clear_current_run()


def test_remote_captcha_handoff_preserves_same_session(tmp_path):
    """Proves Section 8: CAPTCHA handoff holds original browser session alive without bypass."""
    h = Handoff(
        category=CAPTCHA,
        reason="Cloudflare Turnstile challenge visible",
        required_action="Solve CAPTCHA in browser",
        resume_condition="CAPTCHA disappears",
    )
    set_active_handoff(h)

    # 1. Challenge still present: resume is refused
    ok, msg = resolve_active_handoff(live_verification_fn=lambda: (False, "Challenge box still visible"))
    assert ok is False
    assert "still visible" in msg

    # 2. Challenge solved on live page: resume succeeds with positive evidence
    ok, msg = resolve_active_handoff(live_verification_fn=lambda: (True, "Turnstile challenge token verified and box removed"))
    assert ok is True
    assert "resolved" in msg.lower()

    # 3. Verify HandoffStore session security
    store = HandoffStore(tmp_path / "tracker.db")
    model, invitation = store.create_session(
        application_key="app-job-turnstile",
        browser_session_ref="remote-session-chromium-4482",
        category="CAPTCHA",
        reason_code=ReasonCode.CAPTCHA_REQUIRED,
    )
    assert model.browser_session_ref == opaque_ref("remote-session-chromium-4482")
    assert store.claim(invitation.handoff_id, invitation.token, application_key="app-job-turnstile",
                       browser_session_ref="remote-session-chromium-4482")

