"""The phone console: the owner's phone sees the agent's browser on the laptop, and can tap, type and carry on.

When the agent stops for a CAPTCHA, or for a question it cannot answer, the owner need not be at the laptop. This
serves one small page, only on the laptop's Tailscale address and only behind a secret link, that shows the
agent's browser and forwards taps and typing to it. It decides nothing: the agent's own wait loop carries on by
itself once the CAPTCHA is gone or the blanks are filled, and the page's Continue button writes the same signal
the dashboard's does.

Off unless PHONE_CONSOLE=on is set and the laptop has a Tailscale address (PHONE_CONSOLE_HOST can name it). It
never listens on any other address. Every stop gets its own random secret in its link, so the link in an alert
(which passes through the alert server) stops working when that stop ends. A tap on a Submit button is
refused whatever the guard is doing: sending the application stays a decision made at the laptop. Enter is never
sent. The browser is only ever touched from the thread that owns it (service(), called from the wait loop);
the server thread only queues what the phone asked for.
"""
from __future__ import annotations

import hmac
import html
import ipaddress
import json
import logging
import os
import queue
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

import safety

logger = logging.getLogger(__name__)

MIN_SECRET = 24
TYPE_LIMIT = 300
BODY_LIMIT = 16_384
LOOKING_FOR = 20.0           # seconds a phone counts as looking after its last request
SHOT_EVERY = 0.7             # seconds between pictures while it looks
KEYS = frozenset({"Tab", "Backspace", "Escape", "ArrowDown", "ArrowUp"})     # never Enter: it can send a form
TAILSCALE = ipaddress.ip_network("100.64.0.0/10")

_AT_POINT_JS = """([x, y]) => {
  const e = document.elementFromPoint(x, y);
  if (!e) return {text: '', submit: false};
  const c = e.closest('button, input, a, [role=button]') || e;
  const text = (c.innerText || c.value || c.getAttribute('aria-label') || '').trim();
  // A control that would send a form when pressed: a submit input/button, or an untyped button inside a form.
  const sends = !!c.form && c.matches('button:not([type]), button[type=submit], input[type=submit], input[type=image]');
  return {text: text, submit: sends};
}"""


def tailscale_address() -> Optional[str]:
    """The laptop's Tailscale address, or None. Nothing outside 100.64.0.0/10 is ever accepted."""
    host = os.environ.get("PHONE_CONSOLE_HOST", "").strip()
    if not host:
        try:
            found = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=3).stdout.split()
            host = found[0] if found else ""
        except (OSError, subprocess.SubprocessError):
            return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    return str(address) if address in TAILSCALE else None


def open_for(signal_path, needs: dict) -> Optional["PhoneConsole"]:
    """A running console for this stop, or None when it is not set up (the owner then goes to the laptop)."""
    if os.environ.get("PHONE_CONSOLE", "").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    host = tailscale_address()
    if not host:
        return None
    base = int(os.environ.get("PHONE_CONSOLE_PORT", "8765") or 8765)
    token = secrets.token_urlsafe(24)               # this stop's own secret: it ends with the stop
    for port in range(base, base + 10):
        console = PhoneConsole(signal_path, needs, host=host, port=port, token=token)
        try:
            console.start()
            return console
        except OSError:
            continue
    logger.info("PHONE_CONSOLE: no free port from %d", base)
    return None


def begin(signal_path, needs: dict) -> Optional["PhoneConsole"]:
    """A stop that needs the owner: open the console if it is set up, and tell the phone (with the link when
    there is one). Both are optional; with neither set up this does nothing."""
    import phone_alert
    console = open_for(signal_path, needs)
    title, message, priority, tags = phone_alert.describe_need(
        needs.get("company", ""), needs.get("title", ""), needs.get("kind", ""), needs.get("reasons", []))
    phone_alert.send(title, message, priority=priority, tags=tags, click_url=console.url if console else "")
    return console


class PhoneConsole:
    def __init__(self, signal_path, needs: dict, *, host: str, port: int, token: str):
        if len(token or "") < MIN_SECRET:
            raise ValueError(f"the phone console's secret must be at least {MIN_SECRET} characters")
        self.signal_path, self.needs, self.host, self.port, self._token = Path(signal_path), needs, host, port, token
        self._lock = threading.Lock()
        self._commands: queue.Queue = queue.Queue()
        self._jpeg: Optional[bytes] = None
        self._shot_at = 0.0
        self._viewer_at = 0.0
        self._size = [0, 0]
        self._message = ""
        self._server: Optional[ThreadingHTTPServer] = None

    # -- the server ---------------------------------------------------------------------------------------
    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/c/{self._token}/"

    def start(self) -> str:
        console = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):        # the path holds the secret: never logged
                pass

            def _route(self) -> Optional[str]:
                parts = self.path.split("?", 1)[0].split("/")       # ['', 'c', token, rest...]
                if len(parts) < 3 or parts[1] != "c" or not hmac.compare_digest(
                        parts[2].encode(), console._token.encode()):
                    return None
                return "/".join(parts[3:])

            def _send(self, status: int, body: bytes = b"", content_type: str = "text/plain") -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                route = self._route()
                if route is None:
                    return self._send(404)
                console._viewer_at = time.time()
                if route in ("", "index.html"):
                    return self._send(200, console._page().encode(), "text/html; charset=utf-8")
                if route == "state":
                    return self._send(200, json.dumps(console._state()).encode(), "application/json")
                if route == "shot.jpg":
                    with console._lock:
                        jpeg = console._jpeg
                    return self._send(200, jpeg, "image/jpeg") if jpeg else self._send(404)
                return self._send(404)

            def do_POST(self):
                # The body is read before anything is answered: replying to a request whose body is still unread
                # resets the connection on Windows and the phone saw an error instead of the answer.
                try:
                    declared = int(self.headers.get("Content-Length") or 0)
                    body = self.rfile.read(min(declared, BODY_LIMIT))
                    self.close_connection = declared > BODY_LIMIT
                except (ValueError, OSError):
                    return self._send(400)
                if self._route() != "act":
                    return self._send(404)
                try:
                    command = json.loads(body or b"{}")
                except ValueError:
                    return self._send(400)
                console._viewer_at = time.time()
                return self._send(202 if console._accept(command) else 400)

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True, name="phone-console").start()
        logger.info("PHONE_CONSOLE: listening on the Tailscale address")
        return self.url

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    # -- what the phone asks for (server thread) -----------------------------------------------------------
    def _accept(self, command: Any) -> bool:
        if not isinstance(command, dict):
            return False
        what = command.get("do")
        if what == "continue":
            self.signal_path.write_text("continue", encoding="utf-8")
            return True
        if what == "tap":
            try:
                x, y = float(command["x"]), float(command["y"])
            except (KeyError, TypeError, ValueError):
                return False
            if not (0 <= x <= 1 and 0 <= y <= 1):
                return False
            self._commands.put(("tap", x, y))
            return True
        if what == "type":
            text = command.get("text")
            if not isinstance(text, str) or not text:
                return False
            self._commands.put(("type", text[:TYPE_LIMIT]))
            return True
        if what == "key":
            if command.get("key") not in KEYS:
                return False
            self._commands.put(("key", command["key"]))
            return True
        if what == "scroll":
            try:
                dy = max(-800, min(800, int(command.get("dy"))))
            except (TypeError, ValueError):
                return False
            self._commands.put(("scroll", dy))
            return True
        return False

    def _state(self) -> dict:
        with self._lock:
            return {"needs": self.needs, "message": self._message, "size": self._size,
                    "shot_age": round(time.time() - self._shot_at, 1) if self._shot_at else None}

    def _page(self) -> str:
        who = html.escape(f"{self.needs.get('company', '')} - {self.needs.get('title', '')}".strip(" -"))
        return _PAGE.replace("@@WHO@@", who)

    # -- the browser (the thread that owns it) -------------------------------------------------------------
    def service(self, page) -> None:
        """Takes the picture while the phone is looking and does what it asked. Called from the wait loop."""
        now = time.time()
        if now - self._viewer_at > LOOKING_FOR:
            while not self._commands.empty():           # nobody is looking: old taps are not carried out later
                self._commands.get_nowait()
            return
        while not self._commands.empty():
            try:
                self._do(page, self._commands.get_nowait())
            except Exception as exc:
                self._say(f"that did not work ({type(exc).__name__})")
        if now - self._shot_at >= SHOT_EVERY:
            try:
                width, height = page.evaluate("[innerWidth, innerHeight]")
                jpeg = page.screenshot(type="jpeg", quality=55, scale="css")
                with self._lock:
                    self._jpeg, self._shot_at, self._size = jpeg, time.time(), [width, height]
            except Exception:
                pass

    def _do(self, page, command: tuple) -> None:
        kind = command[0]
        if kind == "tap":
            width, height = page.evaluate("[innerWidth, innerHeight]")
            x, y = command[1] * width, command[2] * height
            there = page.evaluate(_AT_POINT_JS, [x, y])
            if there.get("submit") or safety.is_submit_label(there.get("text", "")):
                self._say("Submit is only pressed at the laptop")
                return
            page.mouse.click(x, y)
            self._say("")
        elif kind == "type":
            page.keyboard.insert_text(command[1])
        elif kind == "key":
            page.keyboard.press(command[1])
        elif kind == "scroll":
            page.mouse.wheel(0, command[1])

    def _say(self, text: str) -> None:
        with self._lock:
            self._message = text


_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer">
<title>Job agent</title><style>
body{margin:0;font:16px system-ui;background:#111;color:#eee}
header{padding:10px 12px;background:#222}header b{display:block}small{color:#aaa}
#wrap{padding:6px}img{width:100%;display:block;border:1px solid #444;touch-action:manipulation}
.row{display:flex;gap:6px;padding:6px}.row>*{flex:1;font:inherit;padding:12px 6px;border-radius:8px;border:0}
button{background:#2d6cdf;color:#fff}button.go{background:#1f9d55}input{flex:3;background:#fff;color:#000}
#msg{padding:0 12px;color:#fc6;min-height:1.4em}
</style></head><body>
<header><b>@@WHO@@</b><small id="need">Waiting for you</small></header>
<div id="msg"></div>
<div id="wrap"><img id="shot" alt="the agent's browser"></div>
<div class="row"><button data-s="-400">Up</button><button data-s="400">Down</button>
<button data-k="Tab">Tab</button><button data-k="Backspace">Back</button></div>
<div class="row"><input id="txt" placeholder="Type into the selected box" autocomplete="off">
<button id="send">Type</button></div>
<div class="row"><button class="go" id="go">Done - carry on</button></div>
<script>
const base=location.pathname;
const post=b=>fetch(base+'act',{method:'POST',body:JSON.stringify(b)}).then(refresh);
const img=document.getElementById('shot');
function refresh(){img.src=base+'shot.jpg?'+Date.now();
 fetch(base+'state').then(r=>r.json()).then(s=>{
  document.getElementById('msg').textContent=s.message||'';
  const n=s.needs||{};document.getElementById('need').textContent=
   n.kind==='captcha'?'Solve the CAPTCHA, then it carries on by itself':(n.reasons||[]).slice(0,3).join(' | ')||'Waiting for you';});}
img.addEventListener('click',e=>{const r=img.getBoundingClientRect();
 post({do:'tap',x:(e.clientX-r.left)/r.width,y:(e.clientY-r.top)/r.height});});
document.querySelectorAll('[data-s]').forEach(b=>b.onclick=()=>post({do:'scroll',dy:+b.dataset.s}));
document.querySelectorAll('[data-k]').forEach(b=>b.onclick=()=>post({do:'key',key:b.dataset.k}));
document.getElementById('send').onclick=()=>{const t=document.getElementById('txt');
 if(t.value){post({do:'type',text:t.value});t.value='';}};
document.getElementById('go').onclick=()=>post({do:'continue'});
setInterval(refresh,1000);refresh();
</script></body></html>"""
