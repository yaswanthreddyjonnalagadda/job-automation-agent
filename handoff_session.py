"""Private handoff storage and notification contracts; never runtime authority.

Tokens identify a handoff invitation, not permission to submit or clear a challenge.
The shared RuntimeEvent/HandoffSession adapter is deliberately separate from storage.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

import diagnostics


def opaque_ref(value: str) -> str:
    """Stable correlation without persisting identifiers supplied by the caller."""
    if type(value) is not str or not value.strip() or len(value) > 4096:
        raise ValueError('invalid internal reference')
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _https_endpoint(value: str) -> str:
    try:
        parts = urlsplit(value)
        if (len(value) > 4096 or parts.scheme != 'https' or not parts.hostname
                or parts.username or parts.password or parts.fragment
                or any(ord(c) < 33 for c in value)):
            raise ValueError
        parts.port  # Reject malformed ports.
    except (TypeError, ValueError):
        raise ValueError('HTTPS endpoint required') from None
    return value


@dataclass(frozen=True)
class HandoffInvitation:
    handoff_id: str
    token: str = field(repr=False)


class HandoffStore:
    """Use the existing SQLite tracker path, or an explicitly supplied SQLite path.

    Only private invitation metadata is stored. No browser/session endpoint, user
    answer, credential, challenge response, or runtime checkpoint is accepted.
    SQLite serializes claims across workers; consumption destroys the token hash.
    """

    def __init__(self, db_path: Path, *, ttl_seconds: int = 600, clock=time.time):
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise ValueError('handoff TTL must be between 1 and 3600 seconds')
        self._path = Path(db_path)
        if not diagnostics._regular_path(self._path):
            raise ValueError('regular database path required')
        self._ttl, self._clock = ttl_seconds, clock
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS diagnostic_handoffs (
                handoff_id TEXT PRIMARY KEY, token_hash TEXT,
                application_ref TEXT NOT NULL, browser_ref TEXT NOT NULL,
                status TEXT NOT NULL, created_at REAL NOT NULL,
                expires_at REAL NOT NULL, updated_at REAL NOT NULL, session_data TEXT)''')
            if 'session_data' not in {r[1] for r in conn.execute('PRAGMA table_info(diagnostic_handoffs)')}:
                conn.execute('ALTER TABLE diagnostic_handoffs ADD COLUMN session_data TEXT')
            conn.execute('''CREATE INDEX IF NOT EXISTS diagnostic_handoffs_expiry
                ON diagnostic_handoffs(expires_at)''')

    @contextmanager
    def _connection(self):
        if not diagnostics._regular_path(self._path):
            raise ValueError('regular database path required')
        conn = sqlite3.connect(self._path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create(self, *, application_key: str, browser_session_ref: str,
               remote: bool = False, remote_origin: str | None = None) -> HandoffInvitation:
        # A remote transport must enforce authentication itself; this just refuses
        # insecure configuration. It neither starts a server nor exposes a browser.
        if remote:
            endpoint = _https_endpoint(remote_origin)
            parts = urlsplit(endpoint)
            if parts.path not in ('', '/') or parts.query:
                raise ValueError('remote origin must not contain a path or query')
        app_ref, browser_ref = opaque_ref(application_key), opaque_ref(browser_session_ref)
        # Distinct formats prevent a bearer token from accidentally being accepted
        # in the notification's public handoff-ID field.
        token, handoff_id = secrets.token_urlsafe(32), secrets.token_hex(32)
        now = self._clock()
        with self._connection() as conn:
            conn.execute('''INSERT INTO diagnostic_handoffs
                (handoff_id,token_hash,application_ref,browser_ref,status,created_at,expires_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)''',
                         (handoff_id, hashlib.sha256(token.encode()).hexdigest(), app_ref,
                          browser_ref, 'PENDING', now, now + self._ttl, now))
        return HandoffInvitation(handoff_id, token)

    def create_session(self, *, application_key: str, browser_session_ref: str,
                       category: str, reason_code, remote=False, remote_origin=None):
        """Create the shared HandoffSession with only fixed safe display facts.

        Returns (shared model, private invitation). The invitation token must be
        delivered separately over an authenticated channel, never in telemetry.
        """
        from runtime_events import HandoffSession, ReasonCode
        import handoff
        if category not in handoff._CATEGORIES or type(reason_code) is not ReasonCode:
            raise ValueError('known handoff category and shared reason enum required')
        invitation = self.create(application_key=application_key, browser_session_ref=browser_session_ref,
                                 remote=remote, remote_origin=remote_origin)
        scope = dict(application_key=application_key, browser_session_ref=browser_session_ref)
        row = self.inspect(invitation.handoff_id, **scope)
        model = HandoffSession(
            handoff_id=invitation.handoff_id, application_key=row['application_ref'],
            browser_session_ref=row['browser_ref'], category=category, reason=reason_code.value,
            required_action='Open the bound browser session and complete the owner action.',
            resume_condition=handoff.resume_condition_for(category),
            status=row['status'], created_at=row['created_at'], expires_at=row['expires_at'],
            updated_at=row['updated_at'])
        with self._connection() as conn:
            conn.execute('UPDATE diagnostic_handoffs SET session_data=? WHERE handoff_id=?',
                         (json.dumps(model.as_dict(), sort_keys=True), invitation.handoff_id))
        return model, invitation

    def get_session(self, handoff_id: str, *, application_key: str, browser_session_ref: str):
        """Reconstruct the shared model, reflecting current private session status."""
        from runtime_events import HandoffSession
        row = self.inspect(handoff_id, application_key=application_key, browser_session_ref=browser_session_ref)
        if row is None:
            return None
        with self._connection() as conn:
            stored = conn.execute('SELECT session_data FROM diagnostic_handoffs WHERE handoff_id=?',
                                  (handoff_id,)).fetchone()
            if not stored or not stored[0]:
                return None
            data = json.loads(stored[0])
        data.update(status=row['status'], updated_at=row['updated_at'])
        return HandoffSession(**data)

    def _bound(self, row, application_key, browser_session_ref) -> bool:
        return bool(row is not None
                    and hmac.compare_digest(row['application_ref'], opaque_ref(application_key))
                    and hmac.compare_digest(row['browser_ref'], opaque_ref(browser_session_ref)))

    def claim(self, handoff_id: str, token: str, *, application_key: str,
              browser_session_ref: str) -> bool:
        """Atomically consume the invitation once. No runtime state is changed."""
        if type(token) is not str or len(token) > 512:
            return False
        with self._connection() as conn:
            row = conn.execute('SELECT * FROM diagnostic_handoffs WHERE handoff_id=?',
                               (handoff_id,)).fetchone()
            if not self._bound(row, application_key, browser_session_ref):
                return False
            now = self._clock()
            if row['expires_at'] <= now:
                conn.execute("UPDATE diagnostic_handoffs SET status='EXPIRED',token_hash=NULL,updated_at=? WHERE handoff_id=? AND status IN ('PENDING','OPENED','IN_PROGRESS')", (now, handoff_id))
                return False
            if row['status'] != 'PENDING' or not row['token_hash']:
                return False
            if not hmac.compare_digest(row['token_hash'], hashlib.sha256(token.encode()).hexdigest()):
                return False
            conn.execute("UPDATE diagnostic_handoffs SET status='OPENED',token_hash=NULL,updated_at=? WHERE handoff_id=?", (now, handoff_id))
            return True

    def transition(self, handoff_id: str, status: str, *, application_key: str,
                   browser_session_ref: str) -> bool:
        allowed = {'PENDING': {'REVOKED'}, 'OPENED': {'IN_PROGRESS', 'REVOKED'},
                   'IN_PROGRESS': {'COMPLETED', 'REVOKED'}}
        with self._connection() as conn:
            row = conn.execute('SELECT * FROM diagnostic_handoffs WHERE handoff_id=?',
                               (handoff_id,)).fetchone()
            if not self._bound(row, application_key, browser_session_ref):
                return False
            now = self._clock()
            if row['expires_at'] <= now and row['status'] in allowed:
                conn.execute("UPDATE diagnostic_handoffs SET status='EXPIRED',token_hash=NULL,updated_at=? WHERE handoff_id=?", (now, handoff_id))
                return False
            if status not in allowed.get(row['status'], set()):
                return False
            conn.execute('UPDATE diagnostic_handoffs SET status=?,token_hash=NULL,updated_at=? WHERE handoff_id=?',
                         (status, now, handoff_id))
            return True

    def inspect(self, handoff_id: str, *, application_key: str, browser_session_ref: str) -> dict | None:
        """Safe private storage projection; never exports the token hash."""
        with self._connection() as conn:
            row = conn.execute('SELECT * FROM diagnostic_handoffs WHERE handoff_id=?',
                               (handoff_id,)).fetchone()
            if not self._bound(row, application_key, browser_session_ref):
                return None
            now = self._clock()
            if row['expires_at'] <= now and row['status'] in {'PENDING', 'OPENED', 'IN_PROGRESS'}:
                conn.execute("UPDATE diagnostic_handoffs SET status='EXPIRED',token_hash=NULL,updated_at=? WHERE handoff_id=?", (now, handoff_id))
                row = conn.execute('SELECT * FROM diagnostic_handoffs WHERE handoff_id=?', (handoff_id,)).fetchone()
            return {k: row[k] for k in ('handoff_id', 'application_ref', 'browser_ref',
                                        'status', 'created_at', 'expires_at', 'updated_at')}

    def cleanup(self) -> int:
        """Remove expired private invitations, including consumed/terminal sessions."""
        with self._connection() as conn:
            return conn.execute('DELETE FROM diagnostic_handoffs WHERE expires_at<=?',
                                (self._clock(),)).rowcount


@dataclass(frozen=True)
class NotificationRegistration:
    """Private push routing data. Never include this object in generic diagnostics.

    Persistence/encryption and sender authentication belong to a future push
    service; this foundation intentionally does not store or send subscriptions.
    """
    device_id: str = field(repr=False)
    push_endpoint: str = field(repr=False)
    subscription_info: dict = field(repr=False)
    created_at: str
    user_ref: str = field(repr=False)

    def __post_init__(self):
        opaque_ref(self.device_id)
        opaque_ref(self.user_ref)
        _https_endpoint(self.push_endpoint)
        info = self.subscription_info
        if (type(info) is not dict or set(info) != {'p256dh', 'auth'}
                or any(type(v) is not str or not 1 <= len(v) <= 512 for v in info.values())):
            raise ValueError('only Web Push public/auth subscription keys are accepted')
        try:
            parsed = datetime.fromisoformat(self.created_at.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                raise ValueError
        except (AttributeError, TypeError, ValueError):
            raise ValueError('timezone-aware registration timestamp required') from None
        object.__setattr__(self, 'subscription_info', dict(info))


@dataclass(frozen=True)
class NotificationPayload:
    handoff_id: str
    company_ref: str

    def __post_init__(self):
        # Opaque invitation IDs only; never bearer tokens or browser endpoints.
        if (type(self.handoff_id) is not str or len(self.handoff_id) != 64
                or any(c not in '0123456789abcdef' for c in self.handoff_id)):
            raise ValueError('opaque handoff ID required')
        if (type(self.company_ref) is not str or len(self.company_ref) != 64
                or any(c not in '0123456789abcdef' for c in self.company_ref)):
            raise ValueError('hashed company reference required')

    def as_dict(self) -> dict:
        return {'title': 'Action required',
                'body': f'CAPTCHA needs your attention for company {opaque_ref(self.company_ref)[:12]}',
                'data': {'handoff_id': self.handoff_id}}


class NotificationDispatcher(ABC):
    @abstractmethod
    def dispatch(self, registration: NotificationRegistration, payload: NotificationPayload) -> bool:
        """Deliver only the payload; never embed the invitation token in a push."""
