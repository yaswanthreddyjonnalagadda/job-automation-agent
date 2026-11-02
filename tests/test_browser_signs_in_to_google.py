"""The agent's browser must not announce itself as automated, so the owner can sign in to Google in it (f181).

The agent reads an account-verification email from the Gmail its own browser is signed in to. Playwright starts its
browser with --enable-automation, and Google refuses to sign anyone in on a browser that says it is automated
("This browser or app may not be secure"), so the owner could never sign that Gmail in and the agent had to stop at every
"verify your email" step. A person still does the signing in; the agent never types a Google password (CLAUDE.md s.5).
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from browser_automation import JobApplicationAssistant


def launched_with(channel, tmp_path):
    seen = {}

    def launch_persistent_context(**kwargs):
        seen.update(kwargs)
        return "context"

    a = JobApplicationAssistant.__new__(JobApplicationAssistant)
    a._playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch_persistent_context))
    a._config = SimpleNamespace(browser_headless=False)
    assert a._launch(channel, tmp_path) == "context"
    return seen


@pytest.mark.parametrize("channel", ["chrome", "", "chromium"])
def test_the_browser_never_starts_with_the_automation_flag(tmp_path, channel):
    seen = launched_with(channel, tmp_path)
    assert "--enable-automation" in seen["ignore_default_args"]
    assert "--disable-blink-features=AutomationControlled" in seen["args"]


def test_real_chrome_is_used_when_asked_and_the_bundled_build_otherwise(tmp_path):
    assert launched_with("chrome", tmp_path)["channel"] == "chrome"
    assert "channel" not in launched_with("", tmp_path)
    assert "channel" not in launched_with("chromium", tmp_path)


def test_the_window_stays_visible_and_the_sandbox_on(tmp_path):
    seen = launched_with("chrome", tmp_path)
    assert seen["headless"] is False and seen["chromium_sandbox"] is True


def test_the_sign_in_helper_loads_and_only_opens_the_browser_when_run():
    import sign_in_browser
    assert callable(sign_in_browser.main) and sign_in_browser.SIGN_IN_URL.startswith("https://accounts.google.com/")


def test_the_helper_offers_to_add_the_job_account_not_to_continue_as_an_old_one():
    import sign_in_browser
    assert "AddSession" in sign_in_browser.SIGN_IN_URL
