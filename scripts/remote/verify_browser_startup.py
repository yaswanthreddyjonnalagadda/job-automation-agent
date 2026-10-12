"""Verify a sandboxed, headed persistent Chromium context on the worker."""
from pathlib import Path
from tempfile import TemporaryDirectory


def verify_browser_startup(browser_type, profile_dir: Path) -> None:
    context = browser_type.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=False,
        chromium_sandbox=True,
        no_viewport=True,
    )
    try:
        page = context.new_page()
        page.goto("about:blank")
        assert page.url == "about:blank"
    finally:
        context.close()


def main() -> None:
    from playwright.sync_api import sync_playwright

    with TemporaryDirectory(prefix="job-agent-browser-check-") as folder:
        with sync_playwright() as playwright:
            verify_browser_startup(playwright.chromium, Path(folder))
    print("SUCCESS: sandboxed headed persistent Chromium startup verified")


if __name__ == "__main__":
    main()
