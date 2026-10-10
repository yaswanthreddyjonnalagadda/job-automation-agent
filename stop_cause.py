"""Deterministic, advisory stop-cause bundles from sanitized event observations."""
from __future__ import annotations

import json
from pathlib import Path

from structured_logging import action_history, stage_entry, transitions, verified_event, write_artifact
import diagnostics


# Static investigation references, never runtime instructions or guessed fixes.
INVESTIGATION = {
    'NAVIGATION': ('browser_automation.py', 'Inspect navigation result and page settlement evidence.'),
    'APPLICATION_ENTRY': ('browser_automation.py', 'Compare the Apply target, containment decision and verified application entry evidence.'),
    'PAGE_CLASSIFICATION': ('page_agent.py', 'Inspect live page classification and its supporting evidence.'),
    'FORM_FIELD': ('interaction.py', 'Compare field write attempts with portal validation and readback.'),
    'ANSWER_SELECTION': ('page_agent.py', 'Inspect the approved answer source and correction evidence.'),
    'ANSWER_VALIDATION': ('action_result.py', 'Compare approved answers with verified field values.'),
    'AUTHENTICATION': ('account_state.py', 'Inspect account classification and authorized credential handling.'),
    'EMAIL_VERIFICATION': ('page_agent.complete_account_code; account_state.py; emailed_codes.py',
        'Inspect email challenge classification, channel authorization and whether authorized mail lookup started.'),
    'CAPTCHA': ('handoff.py', 'Inspect owner handoff and fresh challenge evidence after Check & Resume.'),
    'MFA': ('handoff.py', 'Inspect the owner-required challenge and its live verification evidence.'),
    'RECOVERY': ('recovery.py; checkpoint.py', 'Compare checkpoint facts with the independently observed live state.'),
    'LOOP_STALL': ('state_machine.py', 'Compare repeated fingerprints with the last verified progress and retry changes.'),
    'SUBMISSION': ('submission_guard.py', 'Reconcile the durable submission effect with independent confirmation evidence; do not infer permission to retry.'),
    'NETWORK': ('browser_automation.py', 'Inspect bounded network failure and navigation evidence.'),
    'PORTAL_CHANGED': ('sites/', 'Compare portal adapter assumptions with sanitized live structure.'),
    'INTERNAL_RUNTIME': ('apply_flow.py', 'Inspect the exception category and last consequential action evidence before considering recovery.'),
}

_PREFIXES = (
    ('SUBMISSION', ('SUBMISSION', 'SUBMIT')),
    ('CAPTCHA', ('CAPTCHA',)), ('MFA', ('MFA', 'SMS_MFA')),
    ('EMAIL_VERIFICATION', ('EMAIL', 'MAIL_LOOKUP', 'GMAIL')),
    ('RECOVERY', ('RECOVERY', 'CHECKPOINT')),
    ('LOOP_STALL', ('LOOP', 'STALL', 'PAGE_REPEATED')),
    ('APPLICATION_ENTRY', ('APPLICATION_ENTRY', 'APPLY_')),
    ('ANSWER_VALIDATION', ('ANSWER_VALIDATION',)),
    ('ANSWER_SELECTION', ('ANSWER',)),
    ('FORM_FIELD', ('FIELD', 'UNSUPPORTED_FIELD', 'CONTINUE', 'NEXT')),
    ('AUTHENTICATION', ('AUTH', 'ACCOUNT', 'LOGIN')),
    ('PAGE_CLASSIFICATION', ('PAGE_CLASSIFICATION',)),
    ('NAVIGATION', ('NAVIGATION', 'NAVIGATE', 'PAGE_SETTLE')),
    ('NETWORK', ('NETWORK',)), ('PORTAL_CHANGED', ('PORTAL_CHANGED',)),
    ('INTERNAL_RUNTIME', ('RUNTIME_EXCEPTION', 'INTERNAL_RUNTIME', 'INTERNAL_ERROR')),
)


def _shared_category(category, root_cause_type):
    # The supplied schema currently has a smaller, incompatible enum. Preserve
    # it verbatim and explicitly report the finer diagnostic subsystem separately.
    if category in root_cause_type.__members__:
        return root_cause_type[category].name
    return root_cause_type['INTERNAL'].name


def classify(records, root_cause_type):
    """Observed stable reasons first, then the nearest diagnostic event; no AI."""
    for record in reversed(records):
        details = record.get('details') or {}
        explicit = details.get('root_cause_category')
        if explicit in INVESTIGATION:
            return _shared_category(explicit, root_cause_type), record['event'], explicit
        reason, event = record.get('reason_code') or '', record['event']
        for candidate in (reason, event):
            for category, prefixes in _PREFIXES:
                if candidate.startswith(prefixes):
                    # Read the real shared enum: an incompatible schema fails closed.
                    return _shared_category(category, root_cause_type), event, category
    return _shared_category('INTERNAL_RUNTIME', root_cause_type), records[-1]['event'], 'INTERNAL_RUNTIME'


def _latest_fact(records, key):
    for record in reversed(records):
        value = (record.get('details') or {}).get(key)
        if value is not None:
            return value
    return 'UNKNOWN'


def _loop_context(records, loop):
    observed = [r for r in records if r['sequence'] <= loop['sequence']]
    progress = next((r for r in reversed(observed) if verified_event(r)), None)
    after = progress['sequence'] if progress else 0
    return dict(details=loop.get('details'), count=loop['represented_count'],
                last_verified_at=progress['timestamp'] if progress else None,
                actions_since_progress=sum(1 for r in observed if r['sequence'] > after
                                           and r['event'].endswith(('_ATTEMPTED', '_STARTED'))
                                           and not r['event'].endswith('_NOT_STARTED')))


def build_summary(records, root_cause_type):
    last = records[-1]
    verified = next((r for r in reversed(records) if verified_event(r)), None)
    actions = action_history(records)
    attempted = next((a for a in reversed(actions) if a['result'] == 'ATTEMPTED'
                      and (not verified or a['sequence'] > verified['sequence'])), None)
    shared_category, source, category = classify(records, root_cause_type)
    stage = next((r.get('stage') for r in reversed(records) if r.get('stage')), 'UNKNOWN')
    retries = [r for r in records if 'RETRY' in r['event']]
    retry_count = sum(r['represented_count'] for r in retries)
    loops = [r for r in records if r['event'] in {'LOOP_FINGERPRINT_REPEATED', 'STALL_WARNING', 'LOOP_DETECTED'}]
    # Missing telemetry never proves absence of mail lookups/submission effects.
    email = _latest_fact(records, 'email_state')
    if email == 'UNKNOWN' and any(r['event'] == 'MAIL_LOOKUP_STARTED' for r in records):
        email = 'SEARCHING'
    submission = _latest_fact(records, 'submission_state')
    if email == 'UNKNOWN':
        for r in reversed(records):
            state = {'EMAIL_POLLING': 'SEARCHING', 'EMAIL_MATCHED': 'MATCHED', 'EMAIL_TIMEOUT': 'TIMED_OUT'}.get(r['event'])
            if state:
                email = state
                break
    if submission == 'UNKNOWN':
        submission = next((r['event'].removeprefix('SUBMISSION_') for r in reversed(records)
                           if r['event'] in {'SUBMISSION_AUTHORIZED', 'SUBMISSION_DISPATCHED',
                                             'SUBMISSION_CONFIRMED', 'SUBMISSION_UNCERTAIN'}), 'UNKNOWN')
    next_action = INVESTIGATION[category][1]
    if submission in {'UNCERTAIN', 'DISPATCHED'}:
        next_action = INVESTIGATION['SUBMISSION'][1]
    return {
        'run_id': last['run_id'], 'application_key': last['application_key'],
        'stopped_at': last['timestamp'], 'final_event': last['event'],
        'final_status': 'OWNER_REQUIRED' if last['event'] in {'HANDOFF_CREATED', 'OWNER_HANDOFF_CREATED'} else
                        'FAILED' if last['event'] == 'RUN_FAILED' else 'STOPPED',
        'stage': stage, 'root_cause_category': shared_category, 'diagnostic_subsystem': category,
        'schema_category_gap': shared_category != category,
        'reason_code': last.get('reason_code') or 'UNKNOWN',
        'confidence_basis': {'source_event': source, 'method': 'DETERMINISTIC_OBSERVED_EVENT'},
        'last_verified': ({'event': verified['event'], 'timestamp': verified['timestamp'],
                           'stage': verified.get('stage'), 'evidence': (verified.get('details') or {}).get('evidence_kind')}
                          if verified else None),
        'attempted_next': attempted,
        'observed': [dict(event=r['event'], timestamp=r['timestamp'],
                          reason_code=r.get('reason_code'), details=r.get('details')) for r in records[-8:]],
        'retry_count_retained': retry_count,
        'retry_history': [dict(event=r['event'], details=r.get('details'), count=r['represented_count']) for r in retries[-50:]],
        'loop_history': [_loop_context(records, r) for r in loops[-20:]],
        'account_state': _latest_fact(records, 'account_state'), 'email_state': email,
        'handoff_state': _latest_fact(records, 'handoff_state'),
        'checkpoint_state': _latest_fact(records, 'checkpoint_state'),
        'pending_action': _latest_fact(records, 'pending_action'),
        'uncertain_count': _latest_fact(records, 'uncertain_count'),
        'submission_state': submission,
        'history_truncated': records[0]['sequence'] > records[0].get('represented_count', 1) or
                             any(b['sequence'] - a['sequence'] > b.get('represented_count', 1) for a, b in zip(records, records[1:])),
        'next_safe_action': next_action,
        'production_paths': INVESTIGATION[category][0],
        'advisory_only': True,
    }


def generate_bundle(run_folder: Path, records, root_cause_type) -> bool:
    """No raw fallback or side effects beyond diagnostic artifacts."""
    folder = Path(run_folder) / 'stop_cause'
    if not diagnostics._regular_path(folder):
        return False
    try:
        summary = build_summary(records, root_cause_type)
        entry = stage_entry(records)
        anchors = [entry] if entry else []
        verified = next((r for r in reversed(records) if verified_event(r)), None)
        if verified:
            anchors.append(verified)
        failed_action = (summary['attempted_next'] or {}).get('operation')
        retries = [r for r in records if 'RETRY' in r['event'] and
                   (r.get('details') or {}).get('operation') == failed_action]
        timeline = sorted({r['sequence']: r for r in anchors + retries + records[-50:]}.values(), key=lambda r: r['sequence'])
        artifacts = {
            'summary.json': summary, 'state_transitions.json': transitions(records),
            'action_history.json': action_history(records), 'stop_context.json': records[-1],
            'safe_errors.json': [dict(event=r['event'], exception_category=(r.get('details') or {}).get('exception_category'),
                                     message='Exception details withheld; inspect sanitized developer evidence.')
                                 for r in records if (r.get('details') or {}).get('exception_category')],
            'checkpoint_summary.json': {k: summary[k] for k in ('checkpoint_state', 'pending_action', 'uncertain_count', 'advisory_only')},
            'handoff_summary.json': {k: summary[k] for k in ('run_id', 'application_key', 'handoff_state', 'advisory_only')},
        }
        results = [write_artifact(folder / name, json.dumps(value, sort_keys=True)) for name, value in artifacts.items()]
        results.append(write_artifact(folder / 'timeline.jsonl', '\n'.join(json.dumps(r, sort_keys=True) for r in timeline) + '\n'))
        from run_diagnostics import render_report
        results.append(write_artifact(folder / 'stop_summary.txt', render_report(summary)))
        results.append(write_artifact(folder / 'copy_for_agent.txt', render_report(summary, copy_for_agent=True)))
        if all(results):
            return True
    except Exception:
        pass
    # A partial bundle must not mix new facts with a previous stop's report.
    # Remove only this generator's fixed artifacts, never unrelated materials.
    for name in diagnostics.STRUCTURED_FILES - {'run_events.jsonl'} | {'manifest.json'}:
        path = folder / name
        try:
            if diagnostics._regular_path(path):
                path.unlink(missing_ok=True)
        except OSError:
            pass
    return False
