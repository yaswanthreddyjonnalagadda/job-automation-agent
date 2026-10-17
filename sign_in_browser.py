"""Opens the agent's own browser, with its saved profile, so you can sign in to Google/Gmail once.

The agent reads an account-verification email from the Gmail its browser is signed in to (CLAUDE.md s.5). That sign-in
is yours: it is typed here by you, never by the agent, and it stays in the agent's profile for later runs.

    python sign_in_browser.py      (or double-click "Sign In Agent Browser.bat")

Close the browser window when you are done. Do not run it while the agent is working on a job: the browser profile can
only be open once.
"""
from __future__ import annotations

import sys

import config
from browser_automation import JobApplicationAssistant

SIGN_IN_URL = "https://accounts.google.com/ServiceLogin?service=mail&continue=https://mail.google.com/mail/"


def main() -> int:
    assistant = JobApplicationAssistant.__new__(JobApplicationAssistant)
    assistant._config = config.get_app_config()
    with assistant:
        context = assistant._context
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(SIGN_IN_URL, wait_until="domcontentloaded")
        print("Sign in to the Gmail your job accounts use, in the window that opened.")
        print("Then close that window; your sign-in stays in the agent's browser.")
        try:
            context.wait_for_event("close", timeout=0)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
