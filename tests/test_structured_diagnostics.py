"""Storage contracts use a synthetic schema, not a fabricated runtime_events.py.

Actual shared-schema integration is a separate required acceptance check.
"""
from dataclasses import dataclass, field
from enum import Enum
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import diagnostics
from handoff_session import opaque_ref
from run_diagnostics import LEGACY_REPORT, read_report
from stop_cause import INVESTIGATION
from structured_logging import MAX_EVENTS, SchemaBinding, StructuredLogConsumer


SCENARIOS = [
    ('APPLICATION_ENTRY_NOT_OPENED', 'APPLICATION_ENTRY', {}),
    ('APPLICATION_ENTRY_BLOCKED', 'APPLICATION_ENTRY', {}),
    ('APPLICATION_ENTRY_UNVERIFIED', 'APPLICATION_ENTRY', {}),
    ('ANSWER_CORRECTED', 'ANSWER_SELECTION', {}),
    ('FIELD_WRITE_REJECTED', 'FORM_FIELD', {}),
    ('NEXT_NO_CHANGE', 'FORM_FIELD', {}),
    ('LOOP_FINGERPRINT_REPEATED', 'LOOP_STALL', {'fingerprint': 'synthetic-page', 'repeat_count': 3}),
    ('EMAIL_VERIFICATION_NOT_STARTED', 'EMAIL_VERIFICATION', {'email_state': 'NOT_STARTED'}),
    ('MAIL_LOOKUP_TIMED_OUT', 'EMAIL_VERIFICATION', {'email_state': 'TIMED_OUT'}),
    ('EMAIL_CHANNEL_UNPROVEN', 'EMAIL_VERIFICATION', {'email_state': 'REFUSED'}),
    ('CAPTCHA_WAITING', 'CAPTCHA', {'handoff_state': 'CAPTCHA'}),
    ('CAPTCHA_STILL_PRESENT', 'CAPTCHA', {'handoff_state': 'CAPTCHA'}),
    ('MFA_OWNER_REQUIRED', 'MFA', {'handoff_state': 'MFA'}),
    ('RECOVERY_LIVE_STATE_DIFFERENT', 'RECOVERY', {'checkpoint_state': 'DIFFERENT'}),
    ('SUBMISSION_UNCERTAIN', 'SUBMISSION', {'submission_state': 'UNCERTAIN'}),
    ('RUNTIME_EXCEPTION', 'INTERNAL_RUNTIME', {'exception_category': 'TypeError', 'submission_state': 'DISPATCHED'}),
]
EVENTS = {item[0] for item in SCENARIOS} | {
    'NAVIGATION_VERIFIED', 'APPLICATION_ENTRY_CLICK_ATTEMPTED', 'FIELD_WRITE_ATTEMPTED',
    'FIELD_WRITE_VERIFIED', 'FIELD_WRITE_FAILED', 'ANSWER_CORRECTION_VERIFIED',
    'RUN_FAILED', 'RUN_STOPPED', 'OWNER_HANDOFF_CREATED', 'RETRY_SCHEDULED',
    'MAIL_LOOKUP_STARTED', 'STALL_WARNING', 'SUBMISSION_DISPATCH_ATTEMPTED',
    'APPLY_FOUND', 'NEXT_ATTEMPTED', 'EMAIL_VERIFICATION_ATTEMPTED',
    'CAPTCHA_CHECK_ATTEMPTED', 'MFA_CHECK_ATTEMPTED', 'RECOVERY_RECONCILIATION_ATTEMPTED',
}
EventKind = Enum('EventKind', {v: v for v in EVENTS})
Fact = Enum('Fact', {v: v for v in {
    'NORMAL', 'COMPLETED', 'APPLICATION', 'REVIEW', 'NOT_STARTED', 'NOT_REQUIRED',
    'SEARCHING', 'MATCHED', 'TIMED_OUT', 'REFUSED', 'UNCERTAIN', 'DISPATCHED',
    'AUTHORIZED', 'CONFIRMED', 'NONE', 'CAPTCHA', 'MFA', 'DIFFERENT',
    'FIELD_WRITE', 'APPLICATION_ENTRY_CLICK', 'page_transition', 'validation_text',
    'field_value', 'TypeError', 'EMAIL_VERIFICATION_NOT_STARTED', 'NEXT_NO_CHANGE',
    'TARGET_CHANGED', 'SIGN_IN_FORM', 'AUTHENTICATED', 'PENDING', 'EMAIL_CODE',
}})
RootCauseCategory = Enum('RootCauseCategory', {k: k for k in INVESTIGATION})


@dataclass
class SyntheticEvent:
    run_id: str = 'synthetic-run'
    application_key: str = 'synthetic-application'
    event_type: EventKind = EventKind.NAVIGATION_VERIFIED
    timestamp: str = '2026-10-09T19:00:00+00:00'
    stage: str = 'APPLICATION'
    reason_code: str | None = None
    state_before: str | None = None
    state_after: str | None = None
    details: dict = field(default_factory=dict)
    password: str = 'SECRET-PASSWORD-SENTINEL'
    raw_body: str = 'SECRET-APPLICANT-BODY-SENTINEL'


@pytest.fixture
def binding():
    schema = SimpleNamespace(RuntimeEvent=SyntheticEvent, RootCauseCategory=RootCauseCategory,
                             EventKind=EventKind, Fact=Fact)
    return SchemaBinding(schema, field_map={
        'run_id': 'run_id', 'application_key': 'application_key', 'event': 'event_type',
        'timestamp': 'timestamp', 'stage': 'stage', 'reason_code': 'reason_code',
        'state_before': 'state_before', 'state_after': 'state_after', 'details': 'details'})


@pytest.fixture
def consumer(tmp_path, binding):
    return StructuredLogConsumer(tmp_path / 'output', binding)


def emit(consumer, name, second=0, **kwargs):
    return consumer.consume(SyntheticEvent(event_type=EventKind[name],
        timestamp=f'2026-10-09T19:{second // 60:02d}:{second % 60:02d}+00:00', **kwargs))


def folder(consumer, run='synthetic-run', app='synthetic-application'):
    return consumer.output_dir / opaque_ref(app) / 'diagnostics' / opaque_ref(run)


def read_json(path):
    return json.loads(diagnostics.read_safe_artifact(path))


@pytest.mark.parametrize('reason,category,details', SCENARIOS, ids=[v[0] for v in SCENARIOS])
def test_sixteen_failure_classes_explain_stop_from_summary_alone(consumer, reason, category, details):
    assert emit(consumer, 'NAVIGATION_VERIFIED', details={'evidence_kind': 'page_transition'})
    if category == 'APPLICATION_ENTRY':
        assert emit(consumer, 'APPLY_FOUND', 1, details={'target': 'Submit application'})
    if reason == 'ANSWER_CORRECTED':
        assert emit(consumer, 'FIELD_WRITE_ATTEMPTED', 1, details={'action_id': 'write'})
        assert emit(consumer, 'ANSWER_CORRECTION_VERIFIED', 2, details={'evidence_kind': 'field_value'})
    attempt = {
        'APPLICATION_ENTRY': 'APPLICATION_ENTRY_CLICK_ATTEMPTED',
        'ANSWER_SELECTION': 'NEXT_ATTEMPTED', 'FORM_FIELD': 'FIELD_WRITE_ATTEMPTED',
        'LOOP_STALL': 'NEXT_ATTEMPTED', 'EMAIL_VERIFICATION': 'EMAIL_VERIFICATION_ATTEMPTED',
        'CAPTCHA': 'CAPTCHA_CHECK_ATTEMPTED', 'MFA': 'MFA_CHECK_ATTEMPTED',
        'RECOVERY': 'RECOVERY_RECONCILIATION_ATTEMPTED',
        'SUBMISSION': 'SUBMISSION_DISPATCH_ATTEMPTED', 'INTERNAL_RUNTIME': 'SUBMISSION_DISPATCH_ATTEMPTED',
    }[category]
    if reason == 'NEXT_NO_CHANGE':
        attempt = 'NEXT_ATTEMPTED'
    if reason == 'MAIL_LOOKUP_TIMED_OUT':
        attempt = 'MAIL_LOOKUP_STARTED'
    assert emit(consumer, attempt, 3, details={'action_id': 'attempt'})
    assert emit(consumer, reason, 4, reason_code=reason,
                details={**details, 'evidence_kind': 'validation_text'})
    terminal = 'OWNER_HANDOFF_CREATED' if category in {'CAPTCHA', 'MFA'} else 'RUN_FAILED'
    assert emit(consumer, terminal, 5, reason_code=reason)
    summary = read_json(folder(consumer) / 'stop_cause' / 'summary.json')
    assert summary['root_cause_category'] == category
    assert summary['last_verified']['event'] == ('ANSWER_CORRECTION_VERIFIED' if reason == 'ANSWER_CORRECTED' else 'NAVIGATION_VERIFIED')
    assert summary['attempted_next']['event'] == attempt
    report = read_report(folder(consumer))
    for value in (reason, category, attempt, 'APPLICATION', 'validation_text',
                  INVESTIGATION[category][0], 'Last verified progress', 'RECOMMENDED INVESTIGATION'):
        assert value in report
    for key, value in details.items():
        if key in {'submission_state', 'email_state', 'handoff_state', 'checkpoint_state'}:
            assert summary[key] == value
    if reason == 'RUNTIME_EXCEPTION':
        assert 'do not infer permission to retry' in report
    assert summary['run_id'] == opaque_ref('synthetic-run')
    assert summary['application_key'] == opaque_ref('synthetic-application')
    assert summary['advisory_only'] is True
    if reason == 'EMAIL_VERIFICATION_NOT_STARTED':
        assert 'MAIL_LOOKUP_STARTED' not in report
    if category in {'CAPTCHA', 'MFA'}:
        assert summary['final_status'] == 'OWNER_REQUIRED'


@pytest.mark.parametrize('terminal', ['RUN_FAILED', 'RUN_STOPPED', 'OWNER_HANDOFF_CREATED'])
def test_abnormal_terminal_events_create_complete_manifested_bundle(consumer, terminal):
    assert emit(consumer, terminal, reason_code='CAPTCHA_WAITING')
    files = {p.name for p in (folder(consumer) / 'stop_cause').iterdir()}
    assert files == (diagnostics.STRUCTURED_FILES - {'run_events.jsonl'}) | {'manifest.json'}
    assert all(diagnostics.is_safe_artifact(folder(consumer) / 'stop_cause' / name)
               for name in files - {'manifest.json'})


def test_normal_stop_does_not_create_abnormal_bundle(consumer):
    assert emit(consumer, 'RUN_STOPPED', reason_code='NORMAL')
    assert not (folder(consumer) / 'stop_cause').exists()


def test_full_schema_shape_preserved_but_free_text_is_withheld(consumer):
    assert emit(consumer, 'RUN_FAILED', reason_code='FIELD_WRITE_REJECTED', details={
        'message': 'OTP=482193 Password=private Cookie=secret Authorization=Bearer abc',
        'target': 'Private Applicant Name', 'required_blank_fields': ['Email', 'Private Field'],
        'url': 'https://example.invalid/verify?token=private#secret',
        'cookies': {'session': 'secret'}, 'otp': '482193', 'answer': 'PRIVATE-ANSWER',
        'exception_category': 'TypeError', 'browser_session_ref': 'ws://private/browser/cdp'})
    raw = diagnostics.read_safe_artifact(folder(consumer) / 'run_events.jsonl')
    stored = json.loads(raw)
    assert set(stored['event_data']) == set(SyntheticEvent.__dataclass_fields__)
    for p in folder(consumer).rglob('*'):
        if p.is_file():
            text = p.read_text()
            for sentinel in ('482193', 'Password=private', 'Cookie=secret', 'Bearer abc',
                             'Private Applicant', 'Private Field', 'PRIVATE-ANSWER',
                             'SECRET-PASSWORD', 'SECRET-APPLICANT', 'ws://private', '?token=private'):
                assert sentinel not in text
    assert 'FACTS' in read_report(folder(consumer), copy_for_agent=True)
    assert 'LIKELY CAUSE' in read_report(folder(consumer), copy_for_agent=True)


def test_invalid_or_missing_run_and_wrong_object_are_omitted(consumer):
    for run in ('', 'default', None, 123):
        assert not emit(consumer, 'RUN_FAILED', run_id=run)
    assert not consumer.consume({'run_id': 'raw'})
    assert not consumer.output_dir.exists()


def test_unknown_schema_body_field_is_omitted_even_when_nested_keys_look_structural(consumer):
    incoming = SyntheticEvent(event_type=EventKind.RUN_FAILED)
    incoming.raw_body = {'attempt': 482193, 'target': 'Email', 'submission_state': 'CONFIRMED'}
    assert consumer.consume(incoming)
    stored = json.loads(diagnostics.read_safe_artifact(folder(consumer) / 'run_events.jsonl'))
    assert stored['event_data']['raw_body'] == diagnostics.PLACEHOLDER
    assert '482193' not in json.dumps(stored)


def test_attempt_result_duration_and_state_history_are_separate(consumer):
    assert emit(consumer, 'FIELD_WRITE_ATTEMPTED', 0, details={'action_id': 'one', 'target': 'Email'})
    assert emit(consumer, 'FIELD_WRITE_VERIFIED', 2, details={'action_id': 'one', 'evidence_kind': 'field_value'},
                state_before='SIGN_IN_FORM', state_after='AUTHENTICATED')
    assert emit(consumer, 'RUN_FAILED', 3, reason_code='NEXT_NO_CHANGE')
    records = [json.loads(line) for line in diagnostics.read_safe_artifact(folder(consumer) / 'run_events.jsonl').splitlines()]
    assert records[0]['event'] == 'FIELD_WRITE_ATTEMPTED'
    assert records[1]['event'] == 'FIELD_WRITE_VERIFIED'
    assert records[1]['action_result']['elapsed_ms'] == 2000
    history = read_json(folder(consumer) / 'stop_cause' / 'action_history.json')
    assert [a['result'] for a in history] == ['ATTEMPTED', 'VERIFIED']
    assert history[1]['elapsed_ms'] == 2000
    state = read_json(folder(consumer) / 'stop_cause' / 'state_transitions.json')[0]
    assert state['previous_state'] == 'SIGN_IN_FORM' and state['new_state'] == 'AUTHENTICATED'
    summary = read_json(folder(consumer) / 'stop_cause' / 'summary.json')
    assert summary['attempted_next'] is None


def test_ambiguous_or_absent_action_ids_never_create_invented_durations(consumer):
    for second in (0, 1):
        assert emit(consumer, 'FIELD_WRITE_ATTEMPTED', second, details={'action_id': 'duplicate'})
    assert emit(consumer, 'FIELD_WRITE_VERIFIED', 2, details={'action_id': 'duplicate'})
    assert emit(consumer, 'RUN_FAILED', 3)
    history = read_json(folder(consumer) / 'stop_cause' / 'action_history.json')
    assert all('elapsed_ms' not in a for a in history)


def test_identical_retry_and_loop_history_are_summarized_and_bounded(consumer):
    assert emit(consumer, 'NAVIGATION_VERIFIED')
    for second in range(1, 301):
        assert emit(consumer, 'RETRY_SCHEDULED', second, details={
            'operation': 'FIELD_WRITE', 'attempt': 2, 'max_attempts': 3,
            'previous_reason_code': 'FIELD_WRITE_REJECTED', 'what_changed': 'TARGET_CHANGED'})
    assert emit(consumer, 'LOOP_FINGERPRINT_REPEATED', 301, details={'fingerprint': 'page', 'repeat_count': 3})
    assert emit(consumer, 'LOOP_FINGERPRINT_REPEATED', 302, details={'fingerprint': 'page', 'repeat_count': 3})
    assert emit(consumer, 'RUN_FAILED', 303)
    summary = read_json(folder(consumer) / 'stop_cause' / 'summary.json')
    assert summary['retry_count_retained'] == 300
    assert summary['loop_history'][0]['count'] == 2
    assert summary['retry_history'][0]['details']['max_attempts'] == 3
    assert len(diagnostics.read_safe_artifact(folder(consumer) / 'run_events.jsonl').splitlines()) == 4


def test_restart_reads_only_correlated_manifest_verified_events(consumer, binding):
    assert emit(consumer, 'NAVIGATION_VERIFIED')
    restarted = StructuredLogConsumer(consumer.output_dir, binding)
    assert emit(restarted, 'RUN_FAILED', 2, reason_code='CAPTCHA_WAITING')
    assert read_json(folder(consumer) / 'stop_cause' / 'summary.json')['last_verified']['event'] == 'NAVIGATION_VERIFIED'
    assert not emit(restarted, 'RUN_FAILED', application_key='different-app')


def test_old_missing_or_tampered_runs_degrade_without_reading_raw(consumer):
    assert read_report(folder(consumer)) == LEGACY_REPORT
    assert emit(consumer, 'RUN_FAILED')
    report_path = folder(consumer) / 'stop_cause' / 'stop_summary.txt'
    report_path.write_text('RAW-SECRET-UNMANIFESTED')
    assert read_report(folder(consumer)) == LEGACY_REPORT


def test_sanitization_failure_omits_artifacts_without_raw_fallback(consumer, monkeypatch):
    monkeypatch.setattr(consumer.binding, 'project', lambda _: (_ for _ in ()).throw(ValueError('RAW-SECRET')))
    assert not emit(consumer, 'RUN_FAILED')
    assert not consumer.output_dir.exists()


def test_artifact_io_failure_omits_unmanifested_artifact(consumer, monkeypatch):
    monkeypatch.setattr(diagnostics, '_manifest', lambda *a, **k: (_ for _ in ()).throw(OSError('private')))
    assert not emit(consumer, 'RUN_FAILED')
    assert not list(consumer.output_dir.rglob('*.jsonl'))


def test_partial_bundle_failure_cannot_serve_a_previous_stop_report(consumer, monkeypatch):
    assert emit(consumer, 'RUN_FAILED', reason_code='CAPTCHA_WAITING')
    import stop_cause
    original = stop_cause.write_artifact
    monkeypatch.setattr(stop_cause, 'write_artifact',
        lambda path, content: False if path.name == 'summary.json' else original(path, content))
    assert not emit(consumer, 'RUN_FAILED', 1, reason_code='MFA_OWNER_REQUIRED')
    assert read_report(folder(consumer)) == LEGACY_REPORT


def test_nonidentical_history_is_bounded_with_verified_evidence_retained(consumer, monkeypatch):
    import structured_logging
    monkeypatch.setattr(structured_logging, 'MAX_EVENTS', 8)
    assert emit(consumer, 'NAVIGATION_VERIFIED')
    for second in range(1, 20):
        assert emit(consumer, 'FIELD_WRITE_ATTEMPTED', second, details={'action_id': str(second)})
    assert emit(consumer, 'RUN_FAILED', 20, reason_code='FIELD_WRITE_REJECTED')
    records = diagnostics.read_safe_artifact(folder(consumer) / 'run_events.jsonl').splitlines()
    assert len(records) <= 8
    facts = read_json(folder(consumer) / 'stop_cause' / 'summary.json')
    assert facts['last_verified']['event'] == 'NAVIGATION_VERIFIED'
    assert facts['history_truncated'] is True


def test_timeline_keeps_current_stage_visit_when_wizard_returns_to_previous_stage(consumer, monkeypatch):
    import structured_logging
    monkeypatch.setattr(structured_logging, 'MAX_EVENTS', 5)
    assert emit(consumer, 'NAVIGATION_VERIFIED', 0, stage='APPLICATION')
    assert emit(consumer, 'NEXT_ATTEMPTED', 1, stage='REVIEW')
    assert emit(consumer, 'NEXT_ATTEMPTED', 2, stage='APPLICATION')
    for second in range(3, 10):
        assert emit(consumer, 'FIELD_WRITE_ATTEMPTED', second, stage='APPLICATION')
    assert emit(consumer, 'RUN_FAILED', 10, reason_code='FIELD_WRITE_REJECTED')
    timeline = [json.loads(line) for line in diagnostics.read_safe_artifact(folder(consumer) / 'stop_cause' / 'timeline.jsonl').splitlines()]
    assert any(r['sequence'] == 3 for r in timeline)
    assert timeline[-1]['stage_entry_sequence'] == 3


def test_compressed_first_stage_observation_remains_a_timeline_anchor(consumer, monkeypatch):
    import structured_logging
    monkeypatch.setattr(structured_logging, 'MAX_EVENTS', 5)
    for second in range(3):
        assert emit(consumer, 'STALL_WARNING', second, details={'repeat_count': 2})
    for second in range(3, 10):
        assert emit(consumer, 'FIELD_WRITE_ATTEMPTED', second)
    assert emit(consumer, 'RUN_FAILED', 10)
    timeline = [json.loads(line) for line in diagnostics.read_safe_artifact(folder(consumer) / 'stop_cause' / 'timeline.jsonl').splitlines()]
    anchor = next(r for r in timeline if r['event'] == 'STALL_WARNING')
    assert anchor['first_seen_at'] == '2026-10-09T19:00:00+00:00'
    assert anchor['represented_count'] == 3


def test_loop_context_never_uses_progress_that_occurred_after_the_warning(consumer):
    assert emit(consumer, 'NAVIGATION_VERIFIED', 0)
    assert emit(consumer, 'NEXT_ATTEMPTED', 1)
    assert emit(consumer, 'STALL_WARNING', 2)
    assert emit(consumer, 'FIELD_WRITE_VERIFIED', 3)
    assert emit(consumer, 'RUN_FAILED', 4)
    facts = read_json(folder(consumer) / 'stop_cause' / 'summary.json')
    assert facts['last_verified']['timestamp'] == '2026-10-09T19:00:03+00:00'
    assert facts['loop_history'][0]['last_verified_at'] == '2026-10-09T19:00:00+00:00'
    assert facts['loop_history'][0]['actions_since_progress'] == 1


def test_retention_enters_only_structured_run_paths_and_preserves_active_and_materials(consumer):
    assert emit(consumer, 'RUN_FAILED')
    root = consumer.output_dir.parent
    material = folder(consumer) / 'resume.pdf'
    material.write_bytes(b'private-material')
    assert diagnostics.cleanup_expired_diagnostics(root, days=0, active_dir=folder(consumer)) == 0
    assert diagnostics.cleanup_expired_diagnostics(root, days=0) > 0
    assert material.read_bytes() == b'private-material'
    assert not (folder(consumer) / 'stop_cause' / 'summary.json').exists()
