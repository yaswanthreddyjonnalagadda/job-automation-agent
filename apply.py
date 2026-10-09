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


import diagnostics
diagnostics.install_log_privacy()

from config import DATA_DIR, get_app_config, get_user_profile
from job_sources import resolve_job
import run_outcomes

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("apply")


def already_submitted(job: dict) -> bool:
    """True when this posting has already been submitted.

    The same job arrives with different tracking parameters and sometimes a
    differently-worded title, so the tracker is asked by URL and by
    company+title. Sending an employer a second copy is one of the things the
    assistant must never do.

    When the Postgres tracker is unreachable (e.g. Docker is not running) this
    falls back to the SQLite tracker so the run is never blocked by a DB outage."""
    import tracking
    try:
        tracker = tracking.open_tracker()
        found = tracker.find_submitted(url=job.get("url", ""), company=job.get("company", ""),
                                       title=job.get("title", ""))
    except Exception as exc:
        raise RuntimeError(f"Could not check for duplicate applications: {exc}") from exc
    if found:
        logger.error("DUPLICATE: %s at %s was already submitted (%s) -- not applying again",
                     found.title, found.company, found.updated_at)
    return bool(found)


def skipped_for_sponsorship(job: dict) -> bool:
    """True when the posting says it will not sponsor a visa and the user
    needs one: nothing is opened, and the job is tracked as skipped with
    the posting's own words as the reason."""
    import safety

    profile = get_user_profile()
    if not getattr(profile, "requires_visa_sponsorship", False):
        return False
    said = safety.no_sponsorship_statement(job.get("raw_text", ""))
    if not said:
        return False
    logger.warning("SKIPPED: %s at %s does not sponsor visas -- %r", job.get("title"), job.get("company"), said)
    try:
        import tracking
        from jd_analyzer import dedup_key_for_url

        tracker = tracking.open_tracker()
        key = dedup_key_for_url(job.get("url", ""))
        tracker.create(dedup_key=key, title=job.get("title") or "Unknown role",
                       company=job.get("company") or "Unknown", location=job.get("location") or "",
                       url=job.get("url", ""))
        tracker.update_status(key, "skipped", notes=f"Skipped: no visa sponsorship -- {said}")
    except Exception as exc:
        raise RuntimeError(f"Could not record the skipped application: {exc}") from exc
    return True


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:40] or "job"


def saved_job(url: str) -> dict | None:
    """The posting as an earlier run of it read and saved (data/_job_<company>_<title>.json), matched by the
    posting's address without its tracking parameters."""
    from safety import canonical_url
    wanted = canonical_url(url)
    newest = None
    for path in DATA_DIR.glob("_job_*.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(job, dict) and job.get("url") and job.get("title") \
                and canonical_url(job["url"]) == wanted:
            if newest is None or path.stat().st_mtime > newest[0]:
                newest = (path.stat().st_mtime, job)
    return newest[1] if newest else None


def read_job(url: str) -> dict | None:
    """The posting: read now, a second time if the first read found nothing, else as an earlier run saved it.

    Secunetics (BambooHR), 29 September: the page is built by script, one read came back empty, and the run
    stopped at 'Could not read a job description' although the same posting had been read an hour before and
    was saved."""
    for attempt in (1, 2):
        job = resolve_job(url)
        if job:
            return job
        logger.info("The posting could not be read (try %d of 2)", attempt)
    job = saved_job(url)
    if job:
        logger.info("READ_FROM_EARLIER_RUN: %s @ %s -- the posting would not load now; using the copy saved "
                    "when it was read before", job.get("title"), job.get("company"))
    return job


def run_one(url: str, auto: bool = True, open_url: str = "") -> int:
    job = read_job(url)
    if not job:
        logger.error("Could not read a job description from %s", url)
        return 1

    logger.info("Job: %s @ %s (%s)", job["title"], job["company"], job["location"] or "location n/a")

    if already_submitted(job):
        return run_outcomes.ALREADY_SUBMITTED

    if skipped_for_sponsorship(job):
        return run_outcomes.SKIPPED_SPONSORSHIP

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
    if open_url:
        cmd += ["--open-url", open_url]
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

    # --open-url <address>: resume an application on the page it reached,
    # rather than walking the posting again from the start.
    open_url = ""
    if "--open-url" in urls:
        at = urls.index("--open-url")
        open_url = urls[at + 1] if at + 1 < len(urls) else ""
        urls = urls[:at] + urls[at + 2:]

    auto = False
    if "--auto" in urls:
        auto = True
        urls = [u for u in urls if u != "--auto"]

    outcomes = []
    for url in urls:
        outcomes.append(run_one(unquote(url) if "%" not in url else url, auto=auto or True, open_url=open_url))
    if len(outcomes) == 1:
        return outcomes[0]
    return 1 if any(code != 0 and code not in run_outcomes.EXPECTED_STOPS for code in outcomes) else 0


if __name__ == "__main__":
    sys.exit(main())
