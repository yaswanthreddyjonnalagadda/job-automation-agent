"""Checks the agent against one real posting from each kind of career site.

A change made for one employer's form can quietly break another's. This runs
the agent's own first steps on a real posting per site type -- read the job,
find the form (inside a frame if need be), press Apply, get past the "how do
you want to apply" chooser -- and reports where it got to. It fills nothing,
signs in to nothing and submits nothing, and runs in a hidden browser.

    venv\\Scripts\\python.exe check_sites.py           # every site
    venv\\Scripts\\python.exe check_sites.py icims     # sites whose name matches

The postings are in tests/site_links.json. Postings close; a closed one is
reported as such, not as a failure -- replace it with a live posting from the
same kind of site.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

import safety
from browser_automation import JobApplicationAssistant
from job_sources import resolve_job

LINKS = Path(__file__).resolve().parent / "tests" / "site_links.json"

CLOSED = re.compile(
    r"no longer (available|accepting|open|active)|position (has been|is) (filled|closed)|"
    r"job (posting )?(is )?(closed|expired|not found)|page (you are looking for )?(was )?not (be )?found|"
    r"this job is no longer|requisition .{0,30}closed", re.IGNORECASE)


def where_it_got(agent: JobApplicationAssistant, page) -> tuple[str, bool]:
    """A plain description of the page, and whether that counts as reaching
    the application."""
    url = page.url.lower()
    if "accounts.google.com" in url:
        return "Google sign-in (the application is behind it)", True
    try:
        if page.locator("input[type=password]:visible").count():
            return "the site's sign-in / create-account page", True
        files = page.locator("input[type=file]").count()
    except Exception:
        files = 0
    inputs = agent.visible_input_count(page)
    # Amazon and ADP ask for the email alone first; the password comes next.
    if inputs and re.search(r"login|log-in|signin|sign-in|auth|passport|account", url):
        return "the site's sign-in page (email first)", True
    if inputs >= 3 or files:
        return f"the application form ({inputs} fields{', resume upload' if files else ''})", True
    if agent.on_job_description(page):
        return "still on the job posting -- Apply was not pressed", False
    return f"an unrecognised page ({inputs} fields) at {page.url[:80]}", False


def read(entry: dict) -> str:
    """How the posting was read. Done before the check's own browser opens:
    the reader may open a browser of its own, and two cannot run at once."""
    job = resolve_job(entry["url"])
    if not job:
        return "could not read the posting"
    wrong = []
    if entry.get("title") and entry["title"].lower() not in job["title"].lower():
        wrong.append(f"title {job['title']!r}")
    if entry.get("company") and entry["company"].lower() not in (job["company"] or "").lower():
        wrong.append(f"employer {job['company']!r}")
    return ("read wrongly: " + ", ".join(wrong)) if wrong else f"read as {job['title']!r} at {job['company']!r}"


def check(browser, entry: dict, how_read: str) -> dict:
    outcome = {"site": entry["site"], "read": how_read, "reached": "", "ok": False, "closed": False}

    agent = JobApplicationAssistant.__new__(JobApplicationAssistant)
    agent.values = safety.AgentValues()
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(entry["url"], wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            page.wait_for_timeout(4_000)
        try:
            if CLOSED.search(page.inner_text("body", timeout=5_000)):
                outcome.update(closed=True, reached="the posting is closed")
                return outcome
        except Exception:
            pass
        page = agent.open_embedded_form(page)
        if agent.on_job_description(page):
            page = agent.click_apply_button(page)
            page = agent.dismiss_apply_chooser(page)
            page = agent.open_embedded_form(page)
        # Dayforce shows grey placeholder bars for several seconds before its
        # form appears.
        for _ in range(25):
            if agent.visible_input_count(page):
                break
            page.wait_for_timeout(1_000)
        outcome["reached"], reached = where_it_got(agent, page)
        outcome["ok"] = reached and not outcome["read"].startswith(("could not", "read wrongly"))
    except Exception as exc:
        outcome["reached"] = f"stopped with an error: {str(exc).splitlines()[0][:100]}"
    finally:
        context.close()
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("only", nargs="?", default="", help="check only sites whose name contains this")
    parser.add_argument("--show", action="store_true", help="show the browser window")
    parser.add_argument("--verbose", action="store_true", help="print the agent's own log lines")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.ERROR, format="    %(message)s")

    entries = [e for e in json.loads(LINKS.read_text(encoding="utf-8"))
               if args.only.lower() in e["site"].lower()]
    if not entries:
        print(f"No site in {LINKS.name} matches {args.only!r}")
        return 2

    readings = [read(entry) for entry in entries]

    from playwright.sync_api import sync_playwright
    failed = closed = 0
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=not args.show, channel="chrome")
        except Exception:
            browser = p.chromium.launch(headless=not args.show)
        try:
            for entry, how_read in zip(entries, readings):
                result = check(browser, entry, how_read)
                mark = "CLOSED" if result["closed"] else ("ok" if result["ok"] else "FAILED")
                failed += mark == "FAILED"
                closed += mark == "CLOSED"
                print(f"[{mark:^6}] {result['site']}")
                print(f"         {result['read']}")
                print(f"         reached {result['reached']}")
        finally:
            browser.close()

    print(f"\n{len(entries) - failed - closed} of {len(entries)} sites fine"
          f"{f', {failed} failed' if failed else ''}{f', {closed} postings closed' if closed else ''}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
