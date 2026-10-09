"""Synthetic invitation security tests; no runtime or owner profile required."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sqlite3

import pytest

from handoff_session import (HandoffStore, NotificationDispatcher,
                             NotificationPayload, NotificationRegistration, opaque_ref)


@pytest.fixture
def store(tmp_path):
    return HandoffStore(tmp_path / 'existing-tracker.db')


SCOPE = dict(application_key='synthetic-application', browser_session_ref='synthetic-browser')


def test_only_hash_and_bound_private_metadata_are_stored(store):
    invitation = store.create(**SCOPE)
    assert invitation.token not in repr(invitation)
    with sqlite3.connect(store._path) as conn:
        row = conn.execute('SELECT * FROM diagnostic_handoffs').fetchone()
    serialized = json.dumps(row)
    assert invitation.token not in serialized
    assert SCOPE['application_key'] not in serialized
    assert SCOPE['browser_session_ref'] not in serialized
    assert hashlib.sha256(invitation.token.encode()).hexdigest() in row
    assert 'token_hash' not in store.inspect(invitation.handoff_id, **SCOPE)


def test_valid_claim_is_one_time_and_destroys_hash(store):
    invitation = store.create(**SCOPE)
    assert store.claim(invitation.handoff_id, invitation.token, **SCOPE)
    assert not store.claim(invitation.handoff_id, invitation.token, **SCOPE)
    assert store.inspect(invitation.handoff_id, **SCOPE)['status'] == 'OPENED'
    with sqlite3.connect(store._path) as conn:
        assert conn.execute('SELECT token_hash FROM diagnostic_handoffs').fetchone()[0] is None


def test_concurrent_claims_have_exactly_one_winner(store):
    invitation = store.create(**SCOPE)
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda _: store.claim(invitation.handoff_id, invitation.token, **SCOPE), range(8)))
    assert sum(results) == 1


@pytest.mark.parametrize('changes', [{'application_key': 'different-app'},
                                    {'browser_session_ref': 'different-browser'}])
def test_claim_and_status_updates_are_scope_bound(store, changes):
    invitation = store.create(**SCOPE)
    scope = {**SCOPE, **changes}
    assert not store.claim(invitation.handoff_id, invitation.token, **scope)
    assert not store.transition(invitation.handoff_id, 'REVOKED', **scope)
    assert store.inspect(invitation.handoff_id, **scope) is None
    assert store.claim(invitation.handoff_id, invitation.token, **SCOPE)


def test_wrong_token_does_not_consume_invitation(store):
    invitation = store.create(**SCOPE)
    assert not store.claim(invitation.handoff_id, 'wrong-token', **SCOPE)
    assert store.claim(invitation.handoff_id, invitation.token, **SCOPE)


def test_completion_and_revocation_are_terminal(store):
    invitation = store.create(**SCOPE)
    assert not store.transition(invitation.handoff_id, 'COMPLETED', **SCOPE)
    assert store.claim(invitation.handoff_id, invitation.token, **SCOPE)
    assert not store.transition(invitation.handoff_id, 'COMPLETED', **SCOPE)
    assert store.transition(invitation.handoff_id, 'IN_PROGRESS', **SCOPE)
    assert store.transition(invitation.handoff_id, 'COMPLETED', **SCOPE)
    assert not store.transition(invitation.handoff_id, 'PENDING', **SCOPE)
    assert not store.claim(invitation.handoff_id, invitation.token, **SCOPE)
    revoked = store.create(**SCOPE)
    assert store.transition(revoked.handoff_id, 'REVOKED', **SCOPE)
    assert not store.claim(revoked.handoff_id, revoked.token, **SCOPE)


@pytest.mark.parametrize('phase', ['PENDING', 'OPENED', 'IN_PROGRESS'])
def test_expiration_applies_to_every_active_phase_and_cleanup(tmp_path, phase):
    current = [1000.0]
    store = HandoffStore(tmp_path / 'tracker.db', clock=lambda: current[0], ttl_seconds=600)
    invitation = store.create(**SCOPE)
    if phase != 'PENDING':
        assert store.claim(invitation.handoff_id, invitation.token, **SCOPE)
    if phase == 'IN_PROGRESS':
        assert store.transition(invitation.handoff_id, phase, **SCOPE)
    current[0] = 1600.0
    assert store.inspect(invitation.handoff_id, **SCOPE)['status'] == 'EXPIRED'
    assert not store.claim(invitation.handoff_id, invitation.token, **SCOPE)
    assert not store.transition(invitation.handoff_id, 'COMPLETED', **SCOPE)
    assert store.cleanup() == 1
    assert store.inspect(invitation.handoff_id, **SCOPE) is None


@pytest.mark.parametrize('origin', [None, 'http://example.invalid',
    'https://user:password@example.invalid', 'https://example.invalid/?token=secret',
    'https://example.invalid/browser/cdp', 'https://example.invalid/#secret'])
def test_remote_mode_requires_https_origin_without_credentials_or_session_path(store, origin):
    with pytest.raises(ValueError):
        store.create(**SCOPE, remote=True, remote_origin=origin)


def test_https_remote_configuration_does_not_persist_origin(store):
    invitation = store.create(**SCOPE, remote=True, remote_origin='https://example.invalid')
    assert store.claim(invitation.handoff_id, invitation.token, **SCOPE)
    assert b'example.invalid' not in store._path.read_bytes()


@pytest.mark.parametrize('ttl', [0, -1, 3601, True, '600'])
def test_invalid_ttl_rejected(tmp_path, ttl):
    with pytest.raises(ValueError):
        HandoffStore(tmp_path / 'tracker.db', ttl_seconds=ttl)


def test_handoff_does_not_mutate_tracker_or_authority_tables(tmp_path):
    path = tmp_path / 'tracker.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE submission_effects (state TEXT)')
        conn.execute("INSERT INTO submission_effects VALUES ('UNCERTAIN')")
    store = HandoffStore(path)
    invitation = store.create(**SCOPE)
    assert store.claim(invitation.handoff_id, invitation.token, **SCOPE)
    store.transition(invitation.handoff_id, 'IN_PROGRESS', **SCOPE)
    store.transition(invitation.handoff_id, 'COMPLETED', **SCOPE)
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT state FROM submission_effects').fetchone()[0] == 'UNCERTAIN'


def test_notification_contract_exposes_only_opaque_handoff_reference(store):
    invitation = store.create(**SCOPE)
    registration = NotificationRegistration('device', 'https://push.example.invalid/secret',
        {'p256dh': 'private-subscription-key', 'auth': 'private-subscription-auth'},
        '2026-10-09T19:00:00+00:00', 'user')
    assert 'private-subscription' not in repr(registration)
    assert 'push.example.invalid' not in repr(registration)
    payload = NotificationPayload(invitation.handoff_id, opaque_ref('synthetic-company')).as_dict()
    assert payload['title'] == 'Action required'
    assert payload['data'] == {'handoff_id': invitation.handoff_id}
    assert invitation.token not in json.dumps(payload)
    with pytest.raises(TypeError):
        NotificationDispatcher()
    with pytest.raises(ValueError):
        NotificationPayload(invitation.token, opaque_ref('synthetic-company'))


@pytest.mark.parametrize('info', [{'cookies': 'secret'}, {'p256dh': 'ok', 'auth': 'ok', 'otp': '123456'}])
def test_notification_registration_rejects_non_push_secrets(info):
    with pytest.raises(ValueError):
        NotificationRegistration('device', 'https://push.example.invalid', info,
                                 '2026-10-09T19:00:00Z', 'user')
