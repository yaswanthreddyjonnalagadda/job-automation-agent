"""Evidence is captured one way, and nothing secret is kept in it.

Review of 1 October: the forensic dumper and the review package wrote screenshots and raw HTML without the masking
the page text had. Local synthetic pages only.
"""
import os
import time

import pytest

import evidence


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    pg = context.new_page()
    yield pg
    context.close()


FORM = """<html><head><meta name="csrf-token" content="TOKEN-123"><script>window.session = "SESSION-456";</script></head>
<body><form><label>Email <input id="email" value="owner@example.com"></label>
<label>Password <input type="password" id="pw" value="Hunter2!pw"></label>
<label>Verification code <input name="otp" id="otp"></label>
<input type="hidden" name="authenticity" value="HIDDEN-789">
<label>City <input id="city"></label></form></body></html>"""


def test_saved_html_keeps_no_secret_token_or_script(page):
    page.set_content(FORM)
    page.fill("#otp", "482913")
    page.fill("#city", "Fairfax")
    html = evidence.html(page)
    for secret in ("Hunter2!pw", "482913", "HIDDEN-789", "TOKEN-123", "SESSION-456"):
        assert secret not in html, secret
    assert "City" in html and 'data-hidden="secret"' in html


def test_every_secret_box_is_masked_in_a_screenshot(page):
    page.set_content(FORM)
    assert page.locator(evidence.SECRET_BOXES).count() == 2         # the password and the code, not email or city
    assert evidence.screenshot(page, None, full_page=False)[:4] == b"\x89PNG"


def test_capture_writes_the_three_side_by_side(page, tmp_path):
    page.set_content(FORM)
    page.fill("#pw", "Typed-Secret-9")
    written = evidence.capture(page, tmp_path, "stop")
    assert set(written) == {"screenshot", "html", "text"}
    for path in written.values():
        assert "Typed-Secret-9" not in open(path, "rb").read().decode("utf-8", "ignore")


def _aged(path, days):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    old = time.time() - days * 86_400
    os.utime(path, (old, old))
    return path


def test_old_screenshots_and_page_copies_go_and_the_replay_pages_stay(tmp_path):
    shot = _aged(tmp_path / "output" / "Acme" / "evidence_1" / "page.png", 40)
    copy = _aged(tmp_path / "output" / "Acme" / "review_page.html", 40)
    replay_page = _aged(tmp_path / "output" / "Acme" / "pages" / "page_01.txt", 40)
    document = _aged(tmp_path / "output" / "Acme" / "Resume.pdf", 40)
    dump = _aged(tmp_path / "runs" / "20260901_x" / "axtree_dump.json", 40)
    recent = _aged(tmp_path / "runs" / "20260930_y" / "screenshot.png", 1)
    assert evidence.prune(30, root=tmp_path) == 3
    assert not shot.exists() and not copy.exists() and not dump.exists()
    assert replay_page.exists() and document.exists() and recent.exists()
    assert not (tmp_path / "runs" / "20260901_x").exists()
