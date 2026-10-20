"""Owner-enabled local evidence. Never registered as a safe/exportable artifact."""
from __future__ import annotations

from datetime import datetime, timezone
import copy
import json
import logging
import os
from pathlib import Path
import re
import threading
import uuid

from bs4 import BeautifulSoup
from perception import hide_secrets

ROOT = Path(__file__).resolve().parent / 'local_debug'
_LOCK = threading.Lock()
_SECRET = re.compile(r'password|passcode|otp|totp|one.?time|verification.?code|security.?code|api.?key|token|secret|cookie|authorization|session', re.I)
_MASK = 'input[type=password],input[autocomplete=one-time-code],input[type=hidden]'


def enabled() -> bool:
    return os.getenv('LOCAL_DEBUG_CAPTURE', '').strip().lower() in {'1', 'true', 'yes', 'on'}


def redact_secrets(text: str) -> str:
    for key, value in os.environ.items():
        if value and _SECRET.search(key):
            text = text.replace(value, '<redacted-secret>')
    # Keep personal answers and numbers; redact credential assignments and URLs.
    text = re.sub(r'https?://[^\s<>"\']+', _redact_url, text)
    text = re.sub(r'(?i)\b(?:bearer\s+\S+|(?:password|passcode|otp|totp|verification[ _-]?code|security[ _-]?code|api[ _-]?key|token|secret|cookie|authorization|session[ _-]?id)\s*[=:]\s*[^\n,;]+)', '<redacted-secret>', text)
    return re.sub(r'\b(?:sk-[\w-]+|eyJ[\w.-]+)\b', '<redacted-secret>', text)


def _redact_url(match):
    # Reuse the existing conservative token-URL policy, not personal-data rules.
    from diagnostics import sanitize_url
    return sanitize_url(match[0])


def _root() -> Path:
    if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()) for p in (ROOT, *ROOT.parents)):
        raise OSError('Local diagnostic path must not be a link')
    ROOT.mkdir(parents=True, exist_ok=True)
    return ROOT


def record_log(record: logging.LogRecord) -> None:
    """Copy before the export sanitizer mutates the record; never log recursively."""
    if not enabled():
        return
    try:
        message = logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s').format(copy.copy(record))
        message = redact_secrets(message)
        with _LOCK:
            path = _root() / f'runtime-{datetime.now(timezone.utc):%Y%m%d}-{os.getpid()}.log'
            with path.open('a', encoding='utf-8') as stream:
                stream.write(message + '\n')
    except Exception:
        pass  # Diagnostic failures must not change application behavior.


def _html(raw: str) -> str:
    soup = BeautifulSoup(raw, 'html.parser')
    for node in list(soup.find_all(['script', 'style', 'template', 'meta', 'link'])):
        node.decompose()
    for node in soup.find_all(True):
        descriptor = ' '.join(str(node.get(k, '')) for k in ('name', 'id', 'aria-label', 'autocomplete'))
        if node.name in {'input', 'textarea', 'select'}:
            label = soup.find('label', attrs={'for': node.get('id')}) if node.get('id') else None
            label = label if label is not None else node.find_parent('label')
            if label is not None:
                descriptor += ' ' + label.get_text(' ', strip=True)
        if node.get('type') in {'password', 'hidden'} or _SECRET.search(descriptor):
            node.attrs.pop('value', None)
            if node.name == 'textarea':
                node.clear()
        for key in list(node.attrs):
            if key.startswith(('on', 'data-')) or _SECRET.search(key):
                del node.attrs[key]
    return redact_secrets(str(soup))


def capture(page, source: str) -> None:
    """Pair readable pixels with frame snapshots and DOM at existing read/stop boundaries."""
    if not enabled():
        return
    try:
        folder = _root() / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_') + uuid.uuid4().hex[:10])
        folder.mkdir()
        errors = []
        console = getattr(page, '_console_logs', [])
        (folder / 'console.json').write_text(redact_secrets(json.dumps(console, default=str)), encoding='utf-8')
        masks = []
        frames = getattr(page, 'frames', None) or [page]
        for index, frame in enumerate(frames):
            stem = 'page' if index == 0 else f'frame_{index}'
            try:
                snapshot = frame.locator('body').aria_snapshot()
                (folder / f'{stem}.txt').write_text(redact_secrets(hide_secrets(snapshot)), encoding='utf-8')
                (folder / f'{stem}.html').write_text(_html(frame.content()), encoding='utf-8')
                masks.append(frame.locator(_MASK))
                # Accessible labels cover custom/revealed password and code fields.
                if hasattr(frame, 'get_by_role'):
                    masks.append(frame.get_by_role('textbox', name=_SECRET))
                    masks.append(frame.get_by_role('spinbutton', name=_SECRET))
                    masks.append(frame.get_by_label(_SECRET))
            except Exception as exc:
                errors.append(f'{stem}: {type(exc).__name__}')
        # If a frame could not be inspected, omit pixels rather than expose its credentials.
        if not errors:
            try:
                (folder / 'page.png').write_bytes(page.screenshot(full_page=True, mask=masks, mask_color='#202124', timeout=15000))
            except Exception as exc:
                errors.append(f'screenshot: {type(exc).__name__}')
        (folder / 'capture.json').write_text(json.dumps({
            'source': redact_secrets(str(source)), 'private_local_only': True,
            'errors': errors, 'frames': len(frames),
        }), encoding='utf-8')
    except Exception:
        pass
