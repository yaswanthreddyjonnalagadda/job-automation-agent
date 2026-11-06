"""Poller Service: polls active companies across all ATS platforms and sends batches to the Diff Engine."""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import requests

from services.diff_engine.diff_engine import apply_read_result
from services.diff_engine.storage import JobStorage
from services.poller.ashby import read_ashby_board
from services.poller.bamboohr import read_bamboohr_board
from services.poller.greenhouse import read_greenhouse_board
from services.poller.icims import read_icims_board
from services.poller.lever import read_lever_board
from services.poller.reader_result import ReaderResult
from services.poller.smartrecruiters import read_smartrecruiters_board
from services.poller.workday import read_workday_board

logger = logging.getLogger("poller.service")


def poll_single_company(company: Dict[str, Any], session: Optional[requests.Session] = None, storage: Optional[JobStorage] = None) -> ReaderResult:
    platform = company.get("ats_platform", "").lower()
    slug = company.get("board_slug", "")
    board_url = company.get("board_url", "")

    if platform == "greenhouse":
        return read_greenhouse_board(slug, session=session)
    elif platform == "lever":
        return read_lever_board(slug, session=session)
    elif platform == "ashby":
        return read_ashby_board(slug, session=session)
    elif platform == "workday":
        known_ids = None
        if storage is not None:
            known_ids = lambda eid: storage.has_description("workday", eid)  # noqa: E731
        return read_workday_board(board_url or company.get("api_endpoint", ""), session=session,
                                  known_ids=known_ids)
    elif platform == "smartrecruiters":
        return read_smartrecruiters_board(slug, session=session)
    elif platform == "bamboohr":
        return read_bamboohr_board(slug, company_name=company.get("company_name", ""), session=session)
    elif platform == "icims":
        return read_icims_board(slug or board_url, session=session)
    elif platform in ("generic", "custom", "html"):
        from services.poller.generic_scraper import scrape_career_page
        try:
            postings = scrape_career_page(board_url or company.get("api_endpoint", ""), session=session)
            return ReaderResult(postings=postings, complete=True if postings else False)
        except Exception as exc:
            return ReaderResult(postings=[], complete=False, error=str(exc))
    else:
        logger.warning("Unsupported ATS platform: %s for %s", platform, slug)
        return ReaderResult(postings=[], complete=False, error=f"Unsupported ATS platform: {platform}")


def run_poll_cycle(storage: JobStorage, ats_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    companies = storage.get_companies_to_poll(ats_platform=ats_filter)
    logger.info("Starting poll cycle for %d companies", len(companies))
    session = requests.Session()
    results: List[Dict[str, Any]] = []

    now = datetime.now(timezone.utc)
    run_id = storage.start_crawl_run(ats_filter or "all", now=now)
    boards_complete = 0
    boards_failed = 0

    for comp in companies:
        slug = comp.get("board_slug", "")
        platform = comp.get("ats_platform", "")
        try:
            reader_result = poll_single_company(comp, session=session, storage=storage)
            diff_res = apply_read_result(reader_result, platform, slug, storage, run_id=run_id, now=now)
            results.append(diff_res)
            if diff_res.get("complete"):
                boards_complete += 1
            else:
                boards_failed += 1
            time.sleep(0.3)  # 300ms polite delay between companies
        except Exception as exc:
            logger.error("Failed to poll %s (%s): %s", slug, platform, exc)
            boards_failed += 1

    storage.finish_crawl_run(
        run_id, boards_total=len(companies), boards_complete=boards_complete, boards_failed=boards_failed,
        now=datetime.now(timezone.utc),
    )
    return results


def run_query_discovery_cycle(storage: JobStorage, titles: Optional[List[str]] = None) -> int:
    """Discovers fresh jobs posted today matching candidate titles via aggregators and seeds new ATS targets."""
    from services.poller.query_discovery import search_all_fresh_jobs
    from services.poller.ats_resolver import resolve_and_seed_job

    target_titles = titles
    search_spec = storage.get_active_search_spec()
    if not search_spec:
        try:
            from services.matcher.search_spec_parser import bootstrap_search_spec_from_profile
            search_spec = bootstrap_search_spec_from_profile()
            storage.save_search_spec(search_spec)
        except Exception as exc:
            logger.warning("Could not bootstrap SearchSpec from profile: %s", exc)

    if not target_titles and search_spec and search_spec.target_titles:
        target_titles = search_spec.target_titles

    if not target_titles:
        target_titles = [
            "Network Engineer",
            "Senior Network Engineer",
            "Cloud Network Engineer",
            "Network Security Engineer",
            "Data Center Network Engineer",
        ]

    logger.info("Starting real-time query discovery cycle for %d target titles...", len(target_titles))
    fresh_jobs = search_all_fresh_jobs(queries=target_titles, limit_per_source=20)
    logger.info("Discovered %d candidate postings matching target titles", len(fresh_jobs))

    seeded_count = 0
    for job in fresh_jobs:
        try:
            record = resolve_and_seed_job(job, storage)
            if record:
                seeded_count += 1
        except Exception as exc:
            logger.debug("Could not resolve/seed job %s: %s", job.apply_url, exc)

    logger.info("Query discovery cycle completed: %d new ATS targets/jobs seeded", seeded_count)
    return seeded_count


def main() -> int:
    """Container entry: run the full crawl every JOB_SOURCE_INTERVAL_HOURS hours (default 12)."""
    try:
        interval_hours = float(os.environ.get("JOB_SOURCE_INTERVAL_HOURS", "12"))
    except ValueError:
        interval_hours = 12.0
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    logger.info("Job-source scheduler started (every %.2f hours)", interval_hours)

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        logger.info("Scheduler received shutdown signal %s", sig)
        running = False

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    while running:
        try:
            subprocess.run([sys.executable, "-m", "services.poller.run", "--all"], check=False)
        except Exception as exc:
            logger.error("Scheduled crawl failed: %s", exc, exc_info=True)
        deadline = time.time() + interval_hours * 3600
        while running and time.time() < deadline:
            time.sleep(1)

    logger.info("Scheduler exited cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
