"""Human and Copy-for-Agent reports; observations never grant runtime authority."""
from __future__ import annotations

import json
from pathlib import Path

import diagnostics


LEGACY_REPORT = ('# Run Diagnostic\n\nFACTS\nStructured diagnostic summary unavailable. '
                 'This may be a legacy run or an omitted/invalid artifact.\n\n'
                 'RECOMMENDED INVESTIGATION\nInspect existing sanitized tracker events. '
                 'Missing logs do not prove that an action did not occur.\n'
                 'Sensitive values: REDACTED / OMITTED\nAdvisory only.\n')


def render_report(summary: dict, *, copy_for_agent=False) -> str:
    """Internal only: input must be a freshly constructed structural summary.

    Readers should use read_report(), which serves only the manifest-verified
    already-projected report bytes; raw historical logs are never interpolated.
    """
    verified = summary.get('last_verified') or {}
    attempted = summary.get('attempted_next') or {}
    lines = ['# Run Diagnostic', '', 'FACTS',
        f"Run ID: {summary['run_id']}", f"Application: {summary['application_key']}",
        f"When: {summary['stopped_at']}", f"Where: {summary['stage']}",
        f"Final status: {summary['final_status']}", f"Final event: {summary['final_event']}", f"Stop reason: {summary['reason_code']}",
        f"Last verified progress: {verified.get('event', 'UNKNOWN')} at {verified.get('timestamp', 'UNKNOWN')}",
        f"Last verified evidence: {verified.get('evidence', 'UNKNOWN')}",
        f"What was attempted next: {attempted.get('event', 'UNKNOWN')}",
        f"Retries in retained history: {summary['retry_count_retained']}",
        f"History truncated: {summary['history_truncated']}",
        f"Account/auth state: {summary['account_state']}", f"Email state: {summary['email_state']}",
        f"Handoff state: {summary['handoff_state']}", f"Checkpoint state: {summary['checkpoint_state']}",
        f"Pending action: {summary['pending_action']}", f"Submission state: {summary['submission_state']}",
        'What was observed:']
    for observation in summary['observed']:
        lines.append(f"- {observation['event']}; reason={observation['reason_code']}; evidence={json.dumps(observation['details'], sort_keys=True)}")
    if summary['email_state'] == 'UNKNOWN':
        lines.append('Email lookup state is unknown; absence in retained history does not prove it never started.')
    lines += ['', 'LIKELY CAUSE', f"Subsystem: {summary['diagnostic_subsystem']}",
        f"Shared root-cause category: {summary['root_cause_category']}; schema gap: {summary['schema_category_gap']}",
        f"Basis: observed event {summary['confidence_basis']['source_event']} (deterministic; advisory).",
        '', 'RECOMMENDED INVESTIGATION', f"Relevant production paths: {summary['production_paths']}",
        summary['next_safe_action'], '', 'Sensitive values: REDACTED / OMITTED',
        'Advisory only. Live runtime evidence and existing authority gates remain authoritative.']
    return '\n'.join(lines) + '\n'


def read_report(run_folder: Path, *, copy_for_agent=False) -> str:
    name = 'copy_for_agent.txt' if copy_for_agent else 'stop_summary.txt'
    content = diagnostics.read_safe_artifact(Path(run_folder) / 'stop_cause' / name)
    if content is None:
        return LEGACY_REPORT
    try:
        return content.decode('utf-8')
    except UnicodeDecodeError:
        return LEGACY_REPORT
