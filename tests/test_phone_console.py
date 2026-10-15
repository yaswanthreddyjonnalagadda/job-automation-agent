"""The phone console: the owner's phone sees the laptop's browser and can tap, type and carry on.

For a CAPTCHA, or any question the agent cannot answer, the agent stops and waits. The console is a small private
page, served only on the owner's Tailscale address behind a secret link, that shows the agent's browser and forwards
taps and typing to it. The agent carries on by itself when the CAPTCHA is gone or the blanks are filled (the
existing wait loop); the console only lets the owner act from a phone.

The two things that must hold: only the owner's own secret link gets in, and a tap from the phone can never press a
Submit button -- that stays a decision made at the laptop.
"""
import json
import urllib.error
import urllib.request

import pytest

import phone_console

TOKEN = "t" * 32


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 800, "height": 600})
    pg = context.new_page()
    yield pg
    context.close()


@pytest.fixture
def console(tmp_path):
    c = phone_console.PhoneConsole(tmp_path / "_signal_x.txt", {"kind": "captcha", "reasons": ["a CAPTCHA"],
                                                                "company": "Acme", "title": "Engineer"},
                                   host="127.0.0.1", port=0, token=TOKEN)
    c.start()
    yield c
    c.close()


def get(console, path, token=TOKEN):
    url = f"http://127.0.0.1:{console.port}/c/{token}/{path}"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def act(console, **body):
    request = urllib.request.Request(f"http://127.0.0.1:{console.port}/c/{TOKEN}/act", method="POST",
                                     data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


# ------------------------------------------------------------------ who gets in
def test_the_right_link_shows_the_page_and_states_what_is_needed(console):
    status, body, headers = get(console, "")
    assert status == 200 and b"Acme" in body
    assert "no-store" in headers.get("Cache-Control", "")
    status, body, _ = get(console, "state")
    assert json.loads(body)["needs"]["kind"] == "captcha"


@pytest.mark.parametrize("token", ["wrong", "", "t" * 31, "t" * 33])
def test_any_other_link_gets_nothing(console, token):
    for path in ("", "state", "shot.jpg"):
        status, body, _ = get(console, path, token)
        assert status == 404 and body == b""


def test_a_command_without_the_token_is_refused(console):
    request = urllib.request.Request(f"http://127.0.0.1:{console.port}/c/nope/act", method="POST",
                                     data=b'{"do":"continue"}')
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request, timeout=5)
    assert caught.value.code == 404
    assert not console.signal_path.exists()


def test_a_console_needs_a_long_enough_secret():
    with pytest.raises(ValueError):
        phone_console.PhoneConsole("x", {}, host="127.0.0.1", port=0, token="short")


# ------------------------------------------------------------------ where it listens
def test_only_a_tailscale_address_is_accepted(monkeypatch):
    monkeypatch.setenv("PHONE_CONSOLE_HOST", "100.101.102.103")
    assert phone_console.tailscale_address() == "100.101.102.103"
    for outside in ("192.168.1.20", "0.0.0.0", "8.8.8.8", "127.0.0.1", "not-an-address"):
        monkeypatch.setenv("PHONE_CONSOLE_HOST", outside)
        assert phone_console.tailscale_address() is None, outside


def test_it_stays_off_unless_switched_on_and_on_a_tailscale_address(monkeypatch, tmp_path):
    monkeypatch.delenv("PHONE_CONSOLE", raising=False)
    monkeypatch.setenv("PHONE_CONSOLE_HOST", "100.101.102.103")
    assert phone_console.open_for(tmp_path / "s.txt", {}) is None            # not switched on
    monkeypatch.setenv("PHONE_CONSOLE", "on")
    monkeypatch.setenv("PHONE_CONSOLE_HOST", "192.168.1.20")
    assert phone_console.open_for(tmp_path / "s.txt", {}) is None            # not a Tailscale address


def test_every_stop_gets_its_own_secret_and_it_ends_with_the_stop(monkeypatch, tmp_path):
    monkeypatch.setenv("PHONE_CONSOLE", "on")
    monkeypatch.setattr(phone_console, "tailscale_address", lambda: "127.0.0.1")   # a real address to listen on
    monkeypatch.setenv("PHONE_CONSOLE_PORT", "0")
    first = phone_console.open_for(tmp_path / "a.txt", {})
    second = phone_console.open_for(tmp_path / "b.txt", {})
    try:
        token = lambda c: c.url.rstrip("/").rsplit("/", 1)[1]
        assert token(first) != token(second) and len(token(first)) >= phone_console.MIN_SECRET
        assert first.url.startswith("http://127.0.0.1:")
    finally:
        first.close(); second.close()
    with pytest.raises(Exception):
        urllib.request.urlopen(first.url + "state", timeout=2)               # the link died with the stop


# ------------------------------------------------------------------ seeing the browser
def test_the_picture_is_taken_only_while_the_phone_is_looking(console, page):
    page.set_content("<body>hello</body>")
    console.service(page)
    assert get(console, "shot.jpg")[0] == 404                 # nobody has asked: no picture is taken
    get(console, "state")                                     # the phone opens the page
    console.service(page)
    status, body, headers = get(console, "shot.jpg")
    assert status == 200 and body[:2] == b"\xff\xd8" and headers["Content-Type"] == "image/jpeg"


# ------------------------------------------------------------------ acting from the phone
def test_a_tap_lands_where_it_was_made(console, page):
    page.set_content("<button id=b style='position:absolute;left:100px;top:100px;width:200px;height:80px' "
                     "onclick='window.hit=1'>Verify you are human</button>")
    get(console, "state")
    assert act(console, **{"do": "tap", "x": 200 / 800, "y": 140 / 600}) == 202
    console.service(page)
    assert page.evaluate("window.hit") == 1


def test_a_tap_on_a_submit_button_is_refused(console, page):
    page.set_content("<form onsubmit='window.sent=1;return false'>"
                     "<button style='position:absolute;left:100px;top:100px;width:200px;height:80px'>"
                     "Submit application</button></form>")
    get(console, "state")
    act(console, **{"do": "tap", "x": 200 / 800, "y": 140 / 600})
    console.service(page)
    assert page.evaluate("window.sent") is None
    assert "laptop" in json.loads(get(console, "state")[1])["message"]


def test_a_tap_on_any_submit_typed_control_is_refused(console, page):
    page.set_content("<form onsubmit='window.sent=1;return false'>"
                     "<input type=submit value='Save and Continue' "
                     "style='position:absolute;left:100px;top:100px;width:200px;height:80px'></form>")
    get(console, "state")
    act(console, **{"do": "tap", "x": 200 / 800, "y": 140 / 600})
    console.service(page)
    assert page.evaluate("window.sent") is None


def test_text_is_typed_into_the_box_that_has_the_focus(console, page):
    page.set_content("<input id=a autofocus>")
    page.focus("#a")
    get(console, "state")
    act(console, **{"do": "type", "text": "10 years"})
    console.service(page)
    assert page.input_value("#a") == "10 years"


def test_typing_is_bounded(console, page):
    page.set_content("<input id=a autofocus>")
    page.focus("#a")
    get(console, "state")
    act(console, **{"do": "type", "text": "x" * 5000})
    console.service(page)
    assert len(page.input_value("#a")) <= phone_console.TYPE_LIMIT


def test_enter_is_never_sent_from_the_phone_but_tab_is(console, page):
    page.set_content("<form onsubmit='window.sent=1;return false'><input id=a><input id=b></form>")
    page.focus("#a")
    get(console, "state")
    assert act(console, **{"do": "key", "key": "Enter"}) == 400
    assert act(console, **{"do": "key", "key": "Tab"}) == 202
    console.service(page)
    assert page.evaluate("document.activeElement.id") == "b" and page.evaluate("window.sent") is None


def test_the_page_can_be_scrolled(console, page):
    page.set_content("<div style='height:3000px'>tall</div>")
    get(console, "state")
    act(console, **{"do": "scroll", "dy": 400})
    console.service(page)
    assert page.evaluate("window.scrollY") > 0


def test_continue_writes_the_signal_the_wait_loop_reads(console):
    assert act(console, **{"do": "continue"}) == 202
    assert console.signal_path.read_text(encoding="utf-8").strip() == "continue"


def test_an_unknown_command_is_refused(console):
    assert act(console, **{"do": "evaluate", "js": "alert(1)"}) == 400


def test_closing_stops_the_server(tmp_path):
    c = phone_console.PhoneConsole(tmp_path / "s.txt", {}, host="127.0.0.1", port=0, token=TOKEN)
    c.start()
    port = c.port
    c.close()
    with pytest.raises(Exception):
        urllib.request.urlopen(f"http://127.0.0.1:{port}/c/{TOKEN}/state", timeout=2)
