"""Only the person at this computer can drive the dashboard: not a website they happen to visit.

The dashboard listens on 127.0.0.1:5000. Its forms carried a csrf_token field that nothing filled in or checked,
so any page open in the person's browser could post to it -- start an application, change their saved answers,
delete an application -- and a site that resolves its own name to 127.0.0.1 could read it too. Every request
must now be addressed to this computer by name, and every form sent must come from this dashboard and carry the
token it was given, which is made fresh each time the dashboard starts and never leaves this computer.
"""
from __future__ import annotations

import hmac
import secrets
from urllib.parse import urlparse

from flask import abort, request

TOKEN = secrets.token_urlsafe(32)
LOCAL_NAMES = {"127.0.0.1", "localhost", "[::1]"}
CHANGES = {"POST", "PUT", "PATCH", "DELETE"}


def _local(host: str) -> bool:
    name = (host or "").rsplit(":", 1)[0] if not (host or "").startswith("[") else (host or "").split("]")[0] + "]"
    return name.lower() in LOCAL_NAMES


def install(app) -> None:
    @app.context_processor
    def _token():
        return {"csrf_token": TOKEN}

    @app.before_request
    def _guard():
        # A page elsewhere that points its own name at 127.0.0.1 arrives with that name: refused.
        if not _local(request.host):
            abort(403)
        if request.method not in CHANGES:
            return None
        sender = request.headers.get("Origin") or request.headers.get("Referer") or ""
        if sender and urlparse(sender).netloc.lower() != request.host.lower():
            abort(403)                      # a form on another site, posting here
        if app.config.get("TESTING"):
            return None                     # the tests' own client; tests/test_web_guard.py checks the token
        if not hmac.compare_digest(str(request.form.get("csrf_token") or ""), TOKEN):
            abort(403)
        return None
