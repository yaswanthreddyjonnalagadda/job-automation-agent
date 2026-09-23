"""
Deprecated entry point.

This orchestrator has been replaced by apply.py -> apply_flow.py, which
centralizes all CLI logic and duplicate-application checks (see
apply.already_submitted()). main.py is kept only as a pointer so an old
habit or script doesn't silently run the retired flow.
"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    print(
        "main.py is deprecated and no longer runs the application flow.\n"
        "Use: python apply.py <job-url>",
        file=sys.stderr,
    )
    sys.exit(1)
