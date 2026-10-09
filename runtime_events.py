"""Shared telemetry schema and in-memory event bus for runtime events.

This schema is shared across execution, UI cockpit, and diagnostics.
Events emitted through RuntimeEvent.emit() adhere to B5 privacy invariants:
never include credentials, OTPs, auth tokens, cookies, private values,
raw form content, or uncontrolled HTML in safe_metadata.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
import threading
import time
from typing import Any, Dict, List, Optional


class EventName(str, Enum):
    # Run lifecycle
    RUN_STARTED = "RUN_STARTED"
    RUN_STOPPED = "RUN_STOPPED"
    RUN_PAUSED = "RUN_PAUSED"
    RUN_RESUMED = "RUN_RESUMED"

    # Page classification & navigation
    PAGE_OPENED = "PAGE_OPENED"
    PAGE_CLASSIFIED = "PAGE_CLASSIFIED"
    NAVIGATION_ATTEMPTED = "NAVIGATION_ATTEMPTED"
    NAVIGATION_VERIFIED = "NAVIGATION_VERIFIED"
    NAVIGATION_NO_CHANGE = "NAVIGATION_NO_CHANGE"

    # Apply button interaction
    APPLY_SEARCHING = "APPLY_SEARCHING"
    APPLY_FOUND = "APPLY_FOUND"
    APPLY_CLICK_ATTEMPTED = "APPLY_CLICK_ATTEMPTED"
    APPLY_CLICK_VERIFIED = "APPLY_CLICK_VERIFIED"
    APPLY_CLICK_BLOCKED = "APPLY_CLICK_BLOCKED"
    APPLY_NAV_NOT_OBSERVED = "APPLY_NAV_NOT_OBSERVED"

    # Authentication & Account state
    ACCOUNT_STATE_DETECTED = "ACCOUNT_STATE_DETECTED"
    EMAIL_VERIFICATION_REQUIRED = "EMAIL_VERIFICATION_REQUIRED"
    EMAIL_POLLING = "EMAIL_POLLING"
    EMAIL_MATCHED = "EMAIL_MATCHED"
    EMAIL_TIMEOUT = "EMAIL_TIMEOUT"

    # Challenges / Human intervention
    CAPTCHA_DETECTED = "CAPTCHA_DETECTED"
    MFA_DETECTED = "MFA_DETECTED"
    HANDOFF_CREATED = "HANDOFF_CREATED"
    HANDOFF_RESOLVED = "HANDOFF_RESOLVED"

    # Form answering & verification
    QUESTION_DETECTED = "QUESTION_DETECTED"
    ANSWER_SELECTED = "ANSWER_SELECTED"
    ANSWER_WRITTEN = "ANSWER_WRITTEN"
    ANSWER_CORRECTED = "ANSWER_CORRECTED"

    # Loop / Stall detection
    STALL_WARNING = "STALL_WARNING"
    LOOP_DETECTED = "LOOP_DETECTED"
    CIRCUIT_BREAKER_TRIGGERED = "CIRCUIT_BREAKER_TRIGGERED"

    # Submission Gateway
    SUBMISSION_AUTHORIZED = "SUBMISSION_AUTHORIZED"
    SUBMISSION_DISPATCHED = "SUBMISSION_DISPATCHED"
    SUBMISSION_CONFIRMED = "SUBMISSION_CONFIRMED"
    SUBMISSION_UNCERTAIN = "SUBMISSION_UNCERTAIN"


class ReasonCode(str, Enum):
    NAVIGATION_FAILED = "NAVIGATION_FAILED"
    APPLY_CONTROL_NOT_FOUND = "APPLY_CONTROL_NOT_FOUND"
    ACTION_VALIDATION_FAILED = "ACTION_VALIDATION_FAILED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    EMAIL_VERIFICATION_TIMEOUT = "EMAIL_VERIFICATION_TIMEOUT"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    MFA_REQUIRED = "MFA_REQUIRED"
    PORTAL_CHANGED = "PORTAL_CHANGED"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    UNSUPPORTED_FIELD = "UNSUPPORTED_FIELD"
    STALL_DETECTED = "STALL_DETECTED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    SUBMISSION_UNCERTAIN = "SUBMISSION_UNCERTAIN"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SUCCESS = "SUCCESS"


class AnswerSource(str, Enum):
    PROFILE = "PROFILE"
    RESUME = "RESUME"
    DETERMINISTIC_RULE = "DETERMINISTIC_RULE"
    AI_INFERENCE = "AI_INFERENCE"
    USER_OVERRIDE = "USER_OVERRIDE"
    SITE_DEFAULT = "SITE_DEFAULT"


class RootCauseCategory(str, Enum):
    SELECTOR_CHANGED = "SELECTOR_CHANGED"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    FIELD_UNSUPPORTED = "FIELD_UNSUPPORTED"
    STALL = "STALL"
    BOT_DETECTION = "BOT_DETECTION"
    USER_ACTION_REQUIRED = "USER_ACTION_REQUIRED"
    INTERNAL = "INTERNAL"


_SENSITIVE_KEY_SUBSTRINGS = (
    "password", "passphrase", "secret", "token", "otp", "code",
    "cookie", "session", "credential", "auth_header", "private_key",
    "raw_html", "html_content", "body_html"
)


def _sanitize_metadata(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not meta or not isinstance(meta, dict):
        return {}
    clean: Dict[str, Any] = {}
    for k, v in meta.items():
        if not isinstance(k, str):
            continue
        lower_k = k.lower()
        if any(sub in lower_k for sub in _SENSITIVE_KEY_SUBSTRINGS):
            clean[k] = "<redacted>"
            continue
        if isinstance(v, (str, int, float, bool)) or v is None:
            clean[k] = v
        elif isinstance(v, list):
            clean[k] = [
                str(item)[:200] if not isinstance(item, (int, float, bool)) else item
                for item in v[:50]
            ]
        elif isinstance(v, dict):
            clean[k] = _sanitize_metadata(v)
        else:
            clean[k] = str(v)[:200]
    return clean


@dataclass
class HandoffSession:
    handoff_id: str
    application_key: str
    category: str
    reason: str
    required_action: str
    work_completed: str = ""
    resume_condition: str = ""
    status: str = "PENDING"
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    updated_at: float = field(default_factory=time.time)
    browser_session_ref: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RuntimeEvent:
    run_id: str
    application_key: str
    event: EventName
    timestamp: str
    component: str
    stage: str = ""
    display_message: str = ""
    reason_code: Optional[ReasonCode] = None
    is_verified: bool = False
    evidence: Optional[str] = None
    safe_metadata: Dict[str, Any] = field(default_factory=dict)

    # In-memory thread-safe event storage ring buffer
    _buffer_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _events_buffer: deque = field(default_factory=lambda: deque(maxlen=1000), init=False, repr=False)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "application_key": self.application_key,
            "event": self.event.value if isinstance(self.event, Enum) else str(self.event),
            "timestamp": self.timestamp,
            "component": self.component,
            "stage": self.stage,
            "display_message": self.display_message,
            "reason_code": self.reason_code.value if isinstance(self.reason_code, Enum) else (self.reason_code or ""),
            "is_verified": self.is_verified,
            "evidence": self.evidence or "",
            "safe_metadata": self.safe_metadata,
        }

    @classmethod
    def emit(
        cls,
        event_name: EventName | str,
        component: str,
        stage: str = "",
        display_message: str = "",
        reason_code: ReasonCode | str | None = None,
        safe_metadata: Optional[Dict[str, Any]] = None,
        application_key: Optional[str] = None,
        run_id: Optional[str] = None,
        is_verified: bool = False,
        evidence: Optional[str] = None,
    ) -> "RuntimeEvent":
        """Emit a structured RuntimeEvent into the shared telemetry bus."""
        now_iso = datetime.now(timezone.utc).isoformat()
        
        # Parse event enum
        if isinstance(event_name, EventName):
            ev_enum = event_name
        else:
            try:
                ev_enum = EventName(str(event_name))
            except ValueError:
                # Fallback to string if unlisted
                ev_enum = event_name  # type: ignore

        # Parse reason enum
        rc_enum: Optional[ReasonCode] = None
        if reason_code:
            if isinstance(reason_code, ReasonCode):
                rc_enum = reason_code
            else:
                try:
                    rc_enum = ReasonCode(str(reason_code))
                except ValueError:
                    rc_enum = None

        clean_meta = _sanitize_metadata(safe_metadata)

        ev = cls(
            run_id=run_id or "default",
            application_key=application_key or "",
            event=ev_enum,
            timestamp=now_iso,
            component=component,
            stage=stage,
            display_message=display_message,
            reason_code=rc_enum,
            is_verified=is_verified,
            evidence=evidence,
            safe_metadata=clean_meta,
        )

        with _GLOBAL_BUFFER_LOCK:
            _GLOBAL_EVENT_BUFFER.append(ev)
            # Notify any registered listeners
            for listener in list(_EVENT_LISTENERS):
                try:
                    listener(ev)
                except Exception:
                    pass

        return ev

    @classmethod
    def get_recent(
        cls,
        limit: int = 50,
        application_key: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> List["RuntimeEvent"]:
        """Retrieve recent events filtered by application or run."""
        with _GLOBAL_BUFFER_LOCK:
            events = list(_GLOBAL_EVENT_BUFFER)
        
        if application_key:
            events = [e for e in events if e.application_key == application_key]
        if run_id:
            events = [e for e in events if e.run_id == run_id]
        
        return events[-limit:]

    @classmethod
    def register_listener(cls, callback) -> None:
        """Register a callback for real-time SSE streaming or live notification."""
        with _GLOBAL_BUFFER_LOCK:
            _EVENT_LISTENERS.append(callback)

    @classmethod
    def unregister_listener(cls, callback) -> None:
        with _GLOBAL_BUFFER_LOCK:
            if callback in _EVENT_LISTENERS:
                _EVENT_LISTENERS.remove(callback)

    @classmethod
    def clear_buffer(cls) -> None:
        """Test helper to reset buffer."""
        with _GLOBAL_BUFFER_LOCK:
            _GLOBAL_EVENT_BUFFER.clear()


_GLOBAL_BUFFER_LOCK = threading.RLock()
_GLOBAL_EVENT_BUFFER: deque[RuntimeEvent] = deque(maxlen=2000)
_EVENT_LISTENERS: List[Any] = []
