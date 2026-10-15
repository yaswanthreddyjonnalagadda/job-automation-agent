"""The installer must exercise the browser mode used by real cloud runs."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts.remote.verify_browser_startup import verify_browser_startup


def test_worker_probe_requires_sandboxed_headed_persistent_context(tmp_path):
    context = Mock()
    context.new_page.return_value.url = "about:blank"
    browser = SimpleNamespace(launch_persistent_context=Mock(return_value=context))
    verify_browser_startup(browser, tmp_path)
    browser.launch_persistent_context.assert_called_once_with(
        user_data_dir=str(tmp_path), headless=False, chromium_sandbox=True, no_viewport=True,
    )
    context.close.assert_called_once()


def test_worker_probe_propagates_launch_failure(tmp_path):
    browser = SimpleNamespace(launch_persistent_context=Mock(side_effect=RuntimeError("No usable sandbox")))
    with pytest.raises(RuntimeError, match="No usable sandbox"):
        verify_browser_startup(browser, tmp_path)


def test_worker_probe_closes_context_on_navigation_failure(tmp_path):
    context = Mock()
    context.new_page.return_value.goto.side_effect = RuntimeError("navigation failed")
    browser = SimpleNamespace(launch_persistent_context=Mock(return_value=context))
    with pytest.raises(RuntimeError, match="navigation failed"):
        verify_browser_startup(browser, tmp_path)
    context.close.assert_called_once()
