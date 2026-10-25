"""
A CAPTCHA stop ends when the page has moved on, even if a CAPTCHA widget is still on it (f196).

Mutual of Enumclaw (iCIMS), 18 October: the owner solved hCaptcha on "Create your profile"; iCIMS created the profile and
opened the application (Home / Mobile Phone, ...). The run sat at its CAPTCHA stop: the new page still carried something
captcha_visible counts, so "seen, then gone" never happened. The wait now carries on when the page it stopped on is gone:
its password boxes have gone, or its address is a different page.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

import safety
from browser_automation import JobApplicationAssistant

WIDGET = ('<iframe src="https://newassets.hcaptcha.com/captcha/v1/x/static/hcaptcha.html#frame=checkbox" '
          'width="303" height="78"></iframe>')
PROFILE = ('<h2>Create your profile</h2><input type="email" value="o@example.com"><input type="password">'
           '<input type="password"><button>Submit Profile</button>' + WIDGET)
APPLICATION = '<h2>Contact Information</h2><label>Mobile Phone<input></label>' + WIDGET


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    pg = browser.new_page()
    yield pg
    pg.close()


def assistant():
    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a._profile = None
    a.raise_window = lambda _page: None
    a._sleep_for_phone = lambda seconds, _page: _page.wait_for_timeout(int(seconds * 1000))
    return a


def test_the_stop_ends_once_the_account_form_is_gone(page, tmp_path):
    page.route("https://jobs-tenant.icims.com/**", lambda r: r.fulfill(
        content_type="text/html", body=APPLICATION if "candidate" in r.request.url else PROFILE))
    page.goto("https://jobs-tenant.icims.com/jobs/1/login")
    assert safety.captcha_visible(page) is True          # the widget stays, solved or not
    page.evaluate("setTimeout(() => location.href = '/jobs/1/candidate?from=profilebuilder', 600)")
    signal = tmp_path / "_signal_test.txt"
    result = assistant()._wait_for_signal(signal, poll_seconds=0.5, timeout_seconds=20, page=page, for_captcha=True)
    assert result == "refresh"


def test_the_stop_waits_while_the_same_page_holds_the_captcha(page, tmp_path):
    page.route("https://jobs-tenant.icims.com/**", lambda r: r.fulfill(content_type="text/html", body=PROFILE))
    page.goto("https://jobs-tenant.icims.com/jobs/1/login")
    signal = tmp_path / "_signal_test.txt"
    threading.Timer(2.0, lambda: signal.write_text("continue", encoding="utf-8")).start()
    result = assistant()._wait_for_signal(signal, poll_seconds=0.5, timeout_seconds=20, page=page, for_captcha=True)
    assert result == "continue"                           # nothing moved on: only the owner's Continue ends it
