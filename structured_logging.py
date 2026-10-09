"""Bounded, storage-only RuntimeEvent consumption. No action/policy imports.

SchemaBinding makes the shared schema an explicit read-only dependency. Tests
can supply a synthetic schema; production must supply runtime_events itself.
Unknown/free-text values are structurally withheld, never pattern-only trusted.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import importlib
import json
import os
from pathlib import Path
import threading

import diagnostics
from handoff_session import opaque_ref


MAX_EVENTS = 256
MAX_BYTES = 1024 * 1024
MAX_RUNS = 32
REDACTED = diagnostics.PLACEHOLDER
_LOCK = threading.RLock()
_DETAIL_KEYS = frozenset({
    'operation', 'action', 'action_id', 'operation_id', 'attempt', 'max_attempts',
    'previous_reason_code', 'what_changed', 'fingerprint', 'repeat_count',
    'page_classification', 'required_blank_count', 'required_blank_fields',
    'handoff_state', 'account_state', 'email_state', 'submission_state',
    'checkpoint_state', 'pending_action', 'uncertain_count', 'next_action',
    'evidence_kind', 'result', 'exception_category', 'message', 'url',
    'job_id', 'company_id', 'browser_session_ref', 'handoff_id', 'target',
    'root_cause_category', 'reason_code', 'state_before', 'state_after'})
_REF_KEYS = frozenset({'run_id', 'application_key', 'job_id', 'company_id',
    'browser_session_ref', 'handoff_id', 'action_id', 'operation_id', 'fingerprint'})
_COUNT_KEYS = frozenset({'attempt', 'max_attempts', 'repeat_count',
    'required_blank_count', 'uncertain_count', 'elapsed_ms'})
_STATE_KEYS = frozenset({'operation', 'action', 'stage', 'state_before', 'state_after',
    'page_classification', 'handoff_state', 'account_state', 'email_state',
    'submission_state', 'checkpoint_state', 'next_action', 'pending_action',
    'reason_code', 'previous_reason_code', 'what_changed', 'evidence_kind', 'result',
    'root_cause_category', 'exception_category', 'level'})
_FACTS = {
    'stage': frozenset({'application', 'authentication', 'submission', 'review', 'navigation',
                       'verification', 'recovery', 'questions', 'experience', 'education', 'entry'}),
    'email_state': frozenset({'NOT_REQUIRED', 'NOT_STARTED', 'SEARCHING', 'MATCHED', 'TIMED_OUT', 'REFUSED'}),
    'submission_state': frozenset({'AUTHORIZED', 'DISPATCHED', 'CONFIRMED', 'UNCERTAIN', 'NONE'}),
    'handoff_state': frozenset({'PENDING', 'OPENED', 'IN_PROGRESS', 'COMPLETED', 'EXPIRED', 'REVOKED',
        'CAPTCHA', 'SMS_MFA', 'AUTHENTICATOR_MFA', 'SECURITY_KEY', 'PUSH_APPROVAL', 'OWNER_REVIEW'}),
    'account_state': frozenset({'signed_in', 'locked', 'wrong_password', 'account_exists', 'code_entry',
        'mfa_required', 'verify_email', 'create_form', 'sign_in_form', 'email_first', 'chooser', 'loading', 'none'}),
    'evidence_kind': frozenset({'input_value', 'is_checked', 'selected_option', 'committed_tag',
        'attachment_filename', 'step_indicator', 'entry_count', 'validation_text', 'page_text_diff',
        'field_value', 'page_transition'}),
    'component': frozenset({'apply_flow', 'browser_automation', 'page_agent', 'account_state',
        'emailed_codes', 'submission_guard', 'recovery', 'handoff', 'state_machine', 'interaction'}),
    'exception_category': frozenset({'TypeError', 'ValueError', 'RuntimeError', 'OSError',
        'TimeoutError', 'ConnectionError', 'AssertionError', 'KeyError', 'AttributeError'}),
    'checkpoint_state': frozenset({'COMPATIBLE', 'MIGRATION_REQUIRED', 'UNSUPPORTED',
        'MATCH', 'AHEAD', 'BEHIND', 'AUTH_REQUIRED', 'SUBMITTED', 'NO_CHECKPOINT',
        'DIFFERENT_APPLICATION', 'APPLICATION_NOT_FOUND'}),
    'operation': frozenset(diagnostics.EVENT_ENUMS['action']),
}
_FACTS['state_before'] = _FACTS['state_after'] = frozenset().union(
    *(_FACTS[k] for k in ('account_state', 'email_state', 'submission_state', 'handoff_state', 'checkpoint_state')))


def _timestamp(value) -> str:
    if type(value) is str:
        if len(value) > 40:
            raise ValueError('invalid event timestamp')
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('timezone-aware event timestamp required')
    return value.astimezone(timezone.utc).isoformat()


class SchemaBinding:
    """Explicit semantic mapping onto an existing schema, never a replacement.

    field_map maps canonical diagnostic names to declared RuntimeEvent fields.
    Code vocabularies come ONLY from the supplied module's Enum definitions.
    Free-string reason/state fields not represented by enums remain withheld.
    """

    def __init__(self, schema=None, *, field_map: dict | None = None):
        if schema is None:
            try:
                schema = importlib.import_module('runtime_events')
            except ImportError:
                raise RuntimeError('shared runtime_events schema is unavailable') from None
        self.event_type = schema.RuntimeEvent
        self.root_cause_type = schema.RootCauseCategory
        self.schema = schema
        if field_map is None:
            field_map = {name: name for name in ('run_id', 'application_key', 'event', 'timestamp',
                                                'stage', 'reason_code', 'is_verified', 'evidence', 'component')}
            field_map['details'] = 'safe_metadata'
        if is_dataclass(self.event_type):
            self.field_names = frozenset(f.name for f in fields(self.event_type) if f.init and not f.name.startswith('_'))
        elif hasattr(self.event_type, 'model_fields'):
            self.field_names = frozenset(self.event_type.model_fields)
        else:
            raise ValueError('declared dataclass or Pydantic RuntimeEvent required')
        if not {'run_id', 'application_key', 'event', 'timestamp'} <= set(field_map):
            raise ValueError('correlation, event and timestamp mappings are required')
        if not set(field_map.values()) <= self.field_names:
            raise ValueError('mapping must reference declared RuntimeEvent fields')
        if len(self.field_names) > 100:
            raise ValueError('event schema exceeds bounded field count')
        self.field_map = dict(field_map)
        enum_types = [v for v in vars(schema).values()
                      if isinstance(v, type) and issubclass(v, Enum)]
        self.codes = frozenset(str(member.value) for cls in enum_types for member in cls)
        self.codes |= frozenset(member.name for cls in enum_types for member in cls)

    def code(self, value):
        value = value.value if isinstance(value, Enum) else value
        return value if type(value) is str and value in self.codes else REDACTED

    def value(self, key, value, depth=0):
        if depth > 4:
            return REDACTED
        if value is None:
            return None
        if key in _REF_KEYS:
            return opaque_ref(value) if type(value) is str and value and value not in {REDACTED, '<redacted>'} else None
        if key == 'timestamp':
            return _timestamp(value)
        if key == 'stage' and value == '':
            return None
        if key in _COUNT_KEYS:
            return value if type(value) is int and 0 <= value <= 1000000 else None
        if key == 'url':
            return diagnostics.sanitize_url(value) if type(value) is str else None
        if key == 'target':
            return diagnostics.safe_label(value) if type(value) is str else REDACTED
        if key == 'required_blank_fields':
            return [diagnostics.safe_label(v) for v in value[:50] if type(v) is str] if type(value) is list else []
        if key == 'is_verified':
            return value if type(value) is bool else False
        if key == 'evidence':
            return value if type(value) is str and value in _FACTS['evidence_kind'] else REDACTED
        if key in _FACTS and type(value) is str and value in _FACTS[key]:
            return value
        if key in _STATE_KEYS or key == 'event':
            return self.code(value)
        if type(value) is dict and key in {'details', 'safe_metadata'}:
            if len(value) > 100:
                raise ValueError('metadata exceeds bounded field count')
            return {k: self.value(k, v, depth + 1) for k, v in value.items() if k in _DETAIL_KEYS}
        # Unknown fields keep their declared schema position, without arbitrary text,
        # numbers (which can be OTPs), boolean claims, or nested applicant bodies.
        return REDACTED

    def project(self, event) -> dict:
        if type(event) is not self.event_type:
            raise ValueError('RuntimeEvent instance required')
        raw = {name: getattr(event, name) for name in self.field_names}
        canonical = {name: raw[source] for name, source in self.field_map.items()}
        if not canonical['run_id'] or canonical['run_id'] == 'default' or not canonical['application_key']:
            raise ValueError('run/application correlation required')
        record = {name: self.value(name, value) for name, value in canonical.items()}
        if record['event'] == REDACTED:
            raise ValueError('event kind must belong to the shared enum vocabulary')
        reverse = {source: name for name, source in self.field_map.items()}
        record['event_data'] = {name: self.value(reverse.get(name, name), value)
                                for name, value in raw.items()}
        details = record.setdefault('details', {}) or {}
        for name in ('state_before', 'state_after'):
            if name not in record and name in details:
                record[name] = details[name]
        if record.get('evidence') and record['evidence'] != REDACTED:
            details.setdefault('evidence_kind', record['evidence'])
        record['details'] = details
        record['level'] = severity(record['event'])
        if record['event'] == 'RUN_STOPPED' and record.get('reason_code') not in {'NORMAL', 'COMPLETED', 'SUCCESS'}:
            record['level'] = 'ERROR'
        return record


def verified_event(record):
    """Preserve the runtime's explicit result flag where the schema provides it."""
    candidate = record['event'].endswith('_VERIFIED') or record['event'] in {
        'PAGE_OPENED', 'PAGE_CLASSIFIED', 'ACCOUNT_STATE_DETECTED', 'ANSWER_WRITTEN',
        'ANSWER_CORRECTED', 'EMAIL_MATCHED', 'HANDOFF_RESOLVED', 'SUBMISSION_CONFIRMED'}
    return candidate and (record.get('is_verified') is True or
                          ('is_verified' not in record and record['event'].endswith('_VERIFIED')))


def severity(event: str) -> str:
    if 'INVARIANT' in event or 'PRIVACY' in event:
        return 'CRITICAL'
    if event in {'RUN_FAILED', 'OWNER_HANDOFF_CREATED', 'HANDOFF_CREATED', 'EMAIL_TIMEOUT'} or event.endswith(('_FAILED', '_EXCEPTION')):
        return 'ERROR'
    if any(word in event for word in ('RETRY', 'STALL', 'LOOP', 'CIRCUIT_BREAKER', 'NO_CHANGE', 'BLOCKED', 'CAPTCHA', 'MFA', 'REPEATED', 'REJECTED', 'UNCERTAIN')):
        return 'WARNING'
    return 'INFO'


def write_artifact(path: Path, content: str) -> bool:
    """Only structurally projected callers. Failed writes are never exportable."""
    try:
        if len(content.encode('utf-8')) > MAX_BYTES:
            return False
        if diagnostics.write_safe_text(path, content, MAX_BYTES):
            return True
        # Do not leave a partial/unmanifested new artifact after an I/O failure.
        if diagnostics._regular_path(path) and path.name in diagnostics.STRUCTURED_FILES:
            path.unlink(missing_ok=True)
    except Exception:
        pass
    return False


def _operation(record):
    event = record['event']
    if event.startswith('RUN_') or event.endswith('_NOT_STARTED'):
        return None
    for suffix in ('_ATTEMPTED', '_STARTED', '_VERIFIED', '_FAILED', '_REJECTED', '_FINISHED', '_BLOCKED', '_NO_CHANGE'):
        if event.endswith(suffix):
            return event[:-len(suffix)]
    return None


def action_history(records):
    """Pair only explicit operation IDs; ambiguity never becomes success evidence."""
    pending, history = {}, []
    for r in records:
        op = _operation(r)
        if not op:
            continue
        details = r.get('details') or {}
        identifier = details.get('operation_id') or details.get('action_id')
        key = (op, identifier)
        item = {'event': r['event'], 'operation': op, 'timestamp': r['timestamp'], 'sequence': r['sequence'],
                'evidence': details.get('evidence_kind'), 'target': details.get('target'),
                'result': 'ATTEMPTED' if r['event'].endswith(('_ATTEMPTED', '_STARTED')) else
                          'VERIFIED' if verified_event(r) else
                          'FAILED' if r['event'].endswith(('_FAILED', '_REJECTED', '_BLOCKED', '_NO_CHANGE')) else 'OBSERVED'}
        if item['result'] == 'ATTEMPTED' and identifier:
            pending[key] = r['timestamp'] if key not in pending else None
        elif identifier and pending.get(key):
            started = pending.pop(key)
            elapsed = (datetime.fromisoformat(r['timestamp']) - datetime.fromisoformat(started)).total_seconds() * 1000
            if elapsed >= 0:
                item.update(started_at=started, finished_at=r['timestamp'], elapsed_ms=round(elapsed))
        history.append(item)
    return history


def transitions(records):
    return [{'previous_state': r['state_before'], 'new_state': r['state_after'],
             'trigger_event': r['event'], 'evidence': (r.get('details') or {}).get('evidence_kind'),
             'timestamp': r['timestamp']} for r in records
            if r.get('state_before') is not None and r.get('state_after') is not None]


def stage_entry(records):
    """Anchor the current visit to a stage, including when a wizard goes back."""
    if not records:
        return None
    anchor = records[-1].get('stage_entry_sequence')
    if anchor is not None:
        return next((r for r in records if r.get('first_sequence', r['sequence']) <= anchor <= r['sequence']), None)
    stage = next((r.get('stage') for r in reversed(records) if r.get('stage') not in {None, REDACTED}), None)
    candidate = None
    for r in reversed(records):
        observed = r.get('stage')
        if observed in {None, REDACTED}:
            continue
        if observed != stage:
            break
        candidate = r
    return candidate


class StructuredLogConsumer:
    """One owner per run; serial calls in-process, bounded replay on reconstruction.

    A run cannot be shared by concurrent processes. The runtime must create one
    consumer per execution and pass emit events to consume(). No emitter patching,
    implicit global hooks, tracker writes or runtime state mutation occurs here.
    """

    def __init__(self, output_dir: Path, binding: SchemaBinding):
        self.output_dir, self.binding = Path(output_dir).absolute(), binding
        # Run/app hashes make long, intentionally opaque paths. Windows' legacy
        # MAX_PATH would otherwise silently omit bundles on long install paths.
        if os.name == 'nt' and not str(self.output_dir).startswith('\\\\?\\'):
            text = str(self.output_dir)
            self.output_dir = Path('\\\\?\\UNC\\' + text[2:] if text.startswith('\\\\') else '\\\\?\\' + text)
        self._runs = {}
        self._lifecycle_lock = threading.Lock()

    def _folder(self, record):
        return self.output_dir / record['application_key'] / 'diagnostics' / record['run_id']

    def consume(self, event) -> bool:
        try:
            record = self.binding.project(event)
            with _LOCK:
                return self._consume(record)
        except Exception:
            # Missing/invalid schema, privacy or I/O failures never fall back to raw
            # logs, propagate into authority, or return a permissive action result.
            return False

    def _consume(self, record):
        key = record['run_id']
        folder = self._folder(record)
        path = folder / 'run_events.jsonl'
        if key not in self._runs:
            if len(self._runs) >= MAX_RUNS:
                self._runs.pop(next(iter(self._runs)))
            content = diagnostics.read_safe_artifact(path)
            old = [json.loads(line) for line in content.decode().splitlines()] if content else []
            if any(r['application_key'] != record['application_key'] or r['run_id'] != key for r in old):
                return False
            self._runs[key] = old[-MAX_EVENTS:]
        records = self._runs[key]
        if records and records[0]['application_key'] != record['application_key']:
            return False
        record['sequence'] = (records[-1]['sequence'] if records else 0) + 1
        record['represented_count'] = 1
        previous = records[-1] if records else {}
        active_stage = record.get('stage')
        if active_stage in {None, REDACTED}:
            active_stage = previous.get('active_stage', previous.get('stage'))
        record['active_stage'] = active_stage
        old_stage = previous.get('active_stage', previous.get('stage'))
        prior_anchor = stage_entry(records)
        record['stage_entry_sequence'] = (prior_anchor['sequence'] if prior_anchor and active_stage == old_stage
                                           else record['sequence'])
        # Consecutive identical retry/loop observations retain first/last timestamps
        # and exact count. Attempts/results are always separate records.
        repeatable = any(word in record['event'] for word in ('RETRY', 'REPEATED', 'STALL_WARNING'))
        if repeatable and records and all(records[-1].get(k) == record.get(k)
                for k in ('event', 'stage', 'reason_code', 'details', 'state_before', 'state_after')):
            previous = records.pop()
            record['first_seen_at'] = previous.get('first_seen_at', previous['timestamp'])
            record['first_sequence'] = previous.get('first_sequence', previous['sequence'])
            record['represented_count'] = previous['represented_count'] + 1
        records.append(record)
        latest_action = action_history(records)
        if latest_action and latest_action[-1]['sequence'] == record['sequence']:
            record['action_result'] = latest_action[-1]
        if len(records) > MAX_EVENTS:
            # Preserve the last verified evidence and stage entry alongside the tail.
            anchor = stage_entry(records)
            anchors = [anchor] if anchor else []
            last = next((r for r in reversed(records) if verified_event(r)), None)
            if last:
                anchors.append(last)
            selected = {r['sequence']: r for r in anchors + records[-(MAX_EVENTS-2):]}
            records[:] = sorted(selected.values(), key=lambda r: r['sequence'])
        encoded = '\n'.join(json.dumps(r, sort_keys=True) for r in records) + '\n'
        while len(encoded.encode()) > MAX_BYTES and len(records) > 1:
            anchor = stage_entry(records)
            verified = next((r for r in reversed(records) if verified_event(r)), None)
            protected = {r['sequence'] for r in (anchor, verified, records[-1]) if r}
            remove = next((i for i, r in enumerate(records) if r['sequence'] not in protected), None)
            if remove is None:
                return False
            records.pop(remove)
            encoded = '\n'.join(json.dumps(r, sort_keys=True) for r in records) + '\n'
        if not write_artifact(path, encoded):
            return False
        if record['event'] in {'RUN_FAILED', 'OWNER_HANDOFF_CREATED', 'HANDOFF_CREATED'} or (
                record['event'] == 'RUN_STOPPED' and record.get('reason_code') not in {'NORMAL', 'COMPLETED', 'SUCCESS'}):
            from stop_cause import generate_bundle
            return generate_bundle(folder, records, self.binding.root_cause_type)
        return True

    def start(self):
        """Explicit opt-in bus registration. Calling twice does not duplicate writes."""
        # The shared bus invokes listeners while holding its own lock. Never take
        # the storage lock before acquiring that bus lock (lock-order inversion).
        with self._lifecycle_lock:
            if not getattr(self, '_listening', False):
                self.binding.event_type.register_listener(self.consume)
                self._listening = True
        return self

    def close(self):
        with self._lifecycle_lock:
            if getattr(self, '_listening', False):
                self.binding.event_type.unregister_listener(self.consume)
                self._listening = False
