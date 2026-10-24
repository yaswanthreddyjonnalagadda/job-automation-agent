"""A second worker must never kill a browser owned by a live worker."""
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from browser_profile_lease import BrowserProfileBusy, BrowserProfileLease


def test_same_profile_is_exclusive_and_reusable(tmp_path):
    first = BrowserProfileLease(tmp_path)
    first.acquire()
    try:
        with pytest.raises(BrowserProfileBusy):
            BrowserProfileLease(tmp_path / ".").acquire()
        other = BrowserProfileLease(tmp_path / "other")
        other.acquire()
        other.release()
    finally:
        first.release()
    second = BrowserProfileLease(tmp_path)
    second.acquire()
    second.release()
    second.release()


def test_os_releases_lease_after_worker_dies(tmp_path):
    script = "from browser_profile_lease import BrowserProfileLease; import sys,time; lease=BrowserProfileLease(sys.argv[1]); lease.acquire(); print('ready',flush=True); time.sleep(60)"
    process = subprocess.Popen([sys.executable, "-c", script, str(tmp_path)], stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "ready"
        with pytest.raises(BrowserProfileBusy):
            BrowserProfileLease(tmp_path).acquire()
    finally:
        process.kill()
        process.wait(timeout=10)
        process.stdout.close()
    lease = BrowserProfileLease(tmp_path)
    lease.acquire()
    lease.release()


def test_live_profile_refused_before_leftover_cleanup(tmp_path, monkeypatch):
    import browser_automation as module
    monkeypatch.setattr(module.visible_desktop, "refuse_if_invisible", lambda: None)
    assistant = module.JobApplicationAssistant(SimpleNamespace(browser_profile_dir=tmp_path))
    cleanup = Mock()
    monkeypatch.setattr(assistant, "_close_leftover_browsers", cleanup)
    lease = BrowserProfileLease(tmp_path)
    lease.acquire()
    try:
        with pytest.raises(BrowserProfileBusy):
            assistant.__enter__()
        cleanup.assert_not_called()
    finally:
        lease.release()


def test_launch_failure_releases_profile_and_stops_playwright(tmp_path, monkeypatch):
    import browser_automation as module
    monkeypatch.setattr(module.visible_desktop, "refuse_if_invisible", lambda: None)
    playwright = Mock()
    monkeypatch.setattr(module, "sync_playwright", lambda: SimpleNamespace(start=lambda: playwright))
    assistant = module.JobApplicationAssistant(SimpleNamespace(browser_profile_dir=tmp_path))
    monkeypatch.setattr(assistant, "_close_leftover_browsers", lambda _: None)
    monkeypatch.setattr(assistant, "_choose_channel", lambda: "")
    monkeypatch.setattr(assistant, "_launch", Mock(side_effect=RuntimeError("launch failed")))
    with pytest.raises(RuntimeError, match="launch failed"):
        assistant.__enter__()
    playwright.stop.assert_called_once()
    lease = BrowserProfileLease(tmp_path)
    lease.acquire()
    lease.release()


def test_second_worker_leaves_live_browser_and_unsaved_input_intact(tmp_path, monkeypatch):
    import browser_automation as module
    monkeypatch.setattr(module.visible_desktop, "refuse_if_invisible", lambda: None)
    monkeypatch.setattr(module.JobApplicationAssistant, "_choose_channel", lambda _: "")
    monkeypatch.setattr(module.JobApplicationAssistant, "_debug_port_args", staticmethod(lambda: []))
    cleanup = Mock()
    monkeypatch.setattr(module.JobApplicationAssistant, "_close_leftover_browsers", cleanup)
    config = SimpleNamespace(browser_profile_dir=tmp_path, browser_headless=True)
    with module.JobApplicationAssistant(config) as first:
        page = first._context.new_page()
        page.set_content('<label>Draft answer<input id="draft"></label>')
        page.locator('#draft').fill('Unsaved synthetic answer')
        with pytest.raises(BrowserProfileBusy):
            with module.JobApplicationAssistant(config):
                pytest.fail("Second worker unexpectedly opened")
        assert page.locator('#draft').input_value() == 'Unsaved synthetic answer'
        cleanup.assert_called_once()
    lease = BrowserProfileLease(tmp_path)
    lease.acquire()
    lease.release()
