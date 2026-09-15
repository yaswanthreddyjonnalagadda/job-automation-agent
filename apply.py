"""
One-command entry point: give it a job URL, it does the rest.

    python apply.py <job-url>
    python apply.py                # works through data/job_queue.txt

It fetches the job description, tailors the resume and cover letter, then
drives the application form to the Review step and stops there. It never
submits -- that click stays yours.

Only company career pages and ATS sites are accepted. LinkedIn/Indeed/Dice
posting URLs are rejected with a pointer to the employer's own page, per the
sourcing rule for this project.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote


from config import DATA_DIR, get_app_config, get_user_profile
from job_sources import resolve_job

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("apply")


def already_submitted(job: dict) -> bool:
    """True when this posting has already been submitted.

    The same job arrives with different tracking parameters and sometimes a
    differently-worded title, so the tracker is asked by URL and by
    company+title. Sending an employer a second copy is one of the things the
    assistant must never do."""
    try:
        from db import get_tracker
        tracker = get_tracker()
    except Exception as exc:  # no database: fall through rather than block a run
        logger.warning("Could not check for duplicates (%s)", str(exc).splitlines()[0][:100])
        return False
    found = tracker.find_submitted(url=job.get("url", ""), company=job.get("company", ""),
                                   title=job.get("title", ""))
    if found:
        logger.error("DUPLICATE: %s at %s was already submitted (%s) -- not applying again",
                     found.title, found.company, found.updated_at)
    return bool(found)


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:40] or "job"


def run_one(url: str, auto: bool = True) -> int:
    job = resolve_job(url)
    if not job:
        logger.error("Could not read a job description from %s", url)
        return 1

    logger.info("Job: %s @ %s (%s)", job["title"], job["company"], job["location"] or "location n/a")

    if already_submitted(job):
        return 3

    key = slug(f"{job['company']}_{job['title']}")
    job_path = DATA_DIR / f"_job_{key}.json"
    job_path.write_text(json.dumps(job, indent=2), encoding="utf-8")

    signal_path = DATA_DIR / f"_signal_{key}.txt"
    signal_path.unlink(missing_ok=True)

    cmd = [
        sys.executable, "apply_flow.py", str(job_path),
        "--signal-file", str(signal_path),
        "--timeout", "3600",
    ]
    experience = DATA_DIR / "_experience.json"
    if experience.is_file():
        cmd += ["--experience-json", str(experience)]
    if auto:
        cmd.append("--auto")

    logger.info("Starting the application. It will stop at Review and wait for you.")
    return subprocess.call(cmd, cwd=str(Path(__file__).parent))


def main() -> int:
    get_app_config()  # fails fast with a clear message if the API key is missing
    profile = get_user_profile()
    logger.info("Applying as %s <%s>", profile.full_name, profile.email)

    urls = sys.argv[1:]
    if not urls:
        queue = DATA_DIR / "job_queue.txt"
        if not queue.is_file():
            logger.error("Give me a job URL:  python apply.py <url>")
            return 2
        urls = [
            line.strip() for line in queue.read_text(encoding="utf-8-sig").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if not urls:
            logger.error("No URLs in %s", queue)
            return 2

    failures = 0
    for url in urls:
        if run_one(unquote(url) if "%" not in url else url) != 0:
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
