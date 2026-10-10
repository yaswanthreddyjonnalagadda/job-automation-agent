"""Acceptance tests against the actual, unmodified shared telemetry schema."""
import ast
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
import json
from pathlib import Path

import pytest

import diagnostics
from handoff_session import HandoffStore, opaque_ref
from run_diagnostics import read_report
from runtime_events import EventName, HandoffSession, ReasonCode, RuntimeEvent
from structured_logging import SchemaBinding, StructuredLogConsumer


@pytest.fixture
def consumer(tmp_path):
    return StructuredLogConsumer(tmp_path / 'output', SchemaBinding())


def event(name, **kw):
    return RuntimeEvent(run_id='run', application_key='app', event=name,
        timestamp=kw.pop('timestamp', '2026-10-09T19:00:00Z'), component='page_agent',
        stage='application', **kw)


def folder(consumer):
    return consumer.output_dir / opaque_ref('app') / 'diagnostics' / opaque_ref('run')


def summary(consumer):
    return json.loads(diagnostics.read_safe_artifact(folder(consumer) / 'stop_cause' / 'summary.json'))


def test_shared_schema_full_public_fields_preserved_private_ring_buffer_excluded(consumer):
    assert consumer.consume(event(EventName.NAVIGATION_VERIFIED, is_verified=True,
        evidence='page_transition', safe_metadata={'state_before': 'sign_in_form',
            'state_after': 'signed_in', 'message': 'PRIVATE-OTP-482193', 'job_id': 'job'}))
    stored = json.loads(diagnostics.read_safe_artifact(folder(consumer) / 'run_events.jsonl'))
    assert set(stored['event_data']) == {f.name for f in fields(RuntimeEvent) if f.init}
    assert stored['is_verified'] is True
    assert stored['evidence'] == 'page_transition'
    assert stored['state_before'] == 'sign_in_form' and stored['state_after'] == 'signed_in'
    assert stored['event_data']['display_message'] == diagnostics.PLACEHOLDER
    assert '_buffer_lock' not in stored['event_data']
    assert 'PRIVATE-OTP' not in json.dumps(stored)


def test_shared_bus_registration_and_shutdown_are_explicit_and_idempotent(consumer):
    consumer.start().start()
    try:
        RuntimeEvent.emit(EventName.NAVIGATION_VERIFIED, component='page_agent',
                          run_id='run', application_key='app', is_verified=True, evidence='page_transition')
        RuntimeEvent.emit(EventName.RUN_STOPPED, component='apply_flow', run_id='run',
                          application_key='app', reason_code=ReasonCode.NETWORK_TIMEOUT)
        records = diagnostics.read_safe_artifact(folder(consumer) / 'run_events.jsonl').splitlines()
        assert len(records) == 2
        assert summary(consumer)['diagnostic_subsystem'] == 'NETWORK'
        # The shared schema has a different enum. Do not invent a shared category.
        assert summary(consumer)['root_cause_category'] == 'INTERNAL'
        assert summary(consumer)['schema_category_gap'] is True
    finally:
        consumer.close()
        consumer.close()
    RuntimeEvent.emit(EventName.RUN_STOPPED, component='apply_flow', run_id='run', application_key='app')
    assert len(diagnostics.read_safe_artifact(folder(consumer) / 'run_events.jsonl').splitlines()) == 2


@pytest.mark.parametrize('state,name', [('SEARCHING', EventName.EMAIL_POLLING),
    ('MATCHED', EventName.EMAIL_MATCHED), ('TIMED_OUT', EventName.EMAIL_TIMEOUT)])
def test_shared_email_progression_is_observed_not_inferred_absent(consumer, state, name):
    assert consumer.consume(event(name))
    assert consumer.consume(event(EventName.RUN_STOPPED, reason_code=ReasonCode.EMAIL_VERIFICATION_TIMEOUT))
    assert summary(consumer)['email_state'] == state


@pytest.mark.parametrize('state', ['AUTHORIZED', 'DISPATCHED', 'CONFIRMED', 'UNCERTAIN'])
def test_shared_submission_effect_remains_an_observation(consumer, state):
    assert consumer.consume(event(EventName['SUBMISSION_' + state]))
    assert consumer.consume(event(EventName.RUN_STOPPED, reason_code=ReasonCode.SUBMISSION_UNCERTAIN))
    assert summary(consumer)['submission_state'] == state
    assert summary(consumer)['advisory_only'] is True


def test_handoff_created_builds_bundle_and_verified_flag_cannot_promote_attempt(consumer):
    assert consumer.consume(event(EventName.NAVIGATION_VERIFIED, is_verified=True, evidence='page_transition'))
    assert consumer.consume(event(EventName.APPLY_CLICK_ATTEMPTED, is_verified=True))
    assert consumer.consume(event(EventName.CAPTCHA_DETECTED, reason_code=ReasonCode.CAPTCHA_REQUIRED,
                                  safe_metadata={'handoff_state': 'CAPTCHA'}))
    assert consumer.consume(event(EventName.HANDOFF_CREATED, reason_code=ReasonCode.CAPTCHA_REQUIRED))
    facts = summary(consumer)
    assert facts['last_verified']['event'] == 'NAVIGATION_VERIFIED'
    assert facts['attempted_next']['event'] == 'APPLY_CLICK_ATTEMPTED'
    assert facts['handoff_state'] == 'CAPTCHA'
    assert facts['diagnostic_subsystem'] == 'CAPTCHA'


def test_successful_normal_stop_and_unlisted_events_are_not_fabricated(consumer):
    assert consumer.consume(event(EventName.RUN_STOPPED, reason_code=ReasonCode.SUCCESS))
    assert not (folder(consumer) / 'stop_cause').exists()
    assert not consumer.consume(event('RUN_FAILED'))
    assert not consumer.consume(event('SECRET-EVENT-NAME'))


def test_shared_handoff_session_round_trip_and_status_are_storage_only(tmp_path):
    store = HandoffStore(tmp_path / 'tracker.db')
    scope = dict(application_key='app', browser_session_ref='browser')
    model, invitation = store.create_session(**scope, category='CAPTCHA', reason_code=ReasonCode.CAPTCHA_REQUIRED)
    assert type(model) is HandoffSession
    assert invitation.token not in json.dumps(model.as_dict())
    assert model.application_key == opaque_ref('app')
    assert model.browser_session_ref == opaque_ref('browser')
    assert store.claim(invitation.handoff_id, invitation.token, **scope)
    assert store.get_session(invitation.handoff_id, **scope).status == 'OPENED'
    assert store.transition(invitation.handoff_id, 'IN_PROGRESS', **scope)
    assert store.transition(invitation.handoff_id, 'COMPLETED', **scope)
    assert store.get_session(invitation.handoff_id, **scope).status == 'COMPLETED'


def test_diagnostics_modules_have_no_imports_into_runtime_authority():
    root = Path(__file__).resolve().parents[1]
    forbidden = {'safety', 'submission_guard', 'apply_flow', 'browser_automation',
                 'page_agent', 'checkpoint', 'recovery', 'web_ui'}
    for name in ('structured_logging.py', 'stop_cause.py', 'run_diagnostics.py', 'handoff_session.py'):
        tree = ast.parse((root / name).read_text())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add((node.module or '').split('.')[0])
        assert not imports & forbidden


def test_generic_summary_material_is_not_eligible_for_structured_retention(tmp_path):
    material = tmp_path / 'output' / 'job' / 'summary.json'
    material.parent.mkdir(parents=True)
    material.write_text('private job material')
    diagnostics.cleanup_expired_diagnostics(tmp_path, days=0)
    assert material.read_text() == 'private job material'


def test_bus_lifecycle_never_holds_storage_lock_while_waiting_for_bus(consumer, monkeypatch):
    import structured_logging

    def check_lock_order(callback):
        def competing_emission():
            acquired = structured_logging._LOCK.acquire(timeout=1)
            if acquired:
                structured_logging._LOCK.release()
            return acquired
        with ThreadPoolExecutor(max_workers=1) as worker:
            assert worker.submit(competing_emission).result(timeout=2), 'bus/storage lock inversion'

    monkeypatch.setattr(RuntimeEvent, 'register_listener', staticmethod(check_lock_order))
    monkeypatch.setattr(RuntimeEvent, 'unregister_listener', staticmethod(check_lock_order))
    consumer.start()
    consumer.close()
