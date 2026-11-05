"""Poller Service: polls active companies across all ATS platforms and sends batches to the Diff Engine."""
from __future__ import annotations

import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import requests

from services.diff_engine.diff_engine import process_poll_batch
from services.diff_engine.storage import JobStorage
from services.poller.ashby import read_ashby_board
from services.poller.bamboohr import read_bamboohr_board
from services.poller.greenhouse import read_greenhouse_board
from services.poller.lever import read_lever_board
from services.poller.smartrecruiters import read_smartrecruiters_board
from services.poller.workday import read_workday_board

logger = logging.getLogger("poller.service")


def poll_single_company(company: Dict[str, Any], session: Optional[requests.Session] = None) -> List[Dict[str, Any]]:
    platform = company.get("ats_platform", "").lower()
    slug = company.get("board_slug", "")
    board_url = company.get("board_url", "")

    if platform == "greenhouse":
        return read_greenhouse_board(slug, session=session).postings
    elif platform == "lever":
        return read_lever_board(slug, session=session).postings
    elif platform == "ashby":
        return read_ashby_board(slug, session=session).postings
    elif platform == "workday":
        return read_workday_board(board_url or company.get("api_endpoint", ""), session=session).postings
    elif platform == "smartrecruiters":
        return read_smartrecruiters_board(slug, session=session).postings
    elif platform == "bamboohr":
        return read_bamboohr_board(slug, company_name=company.get("company_name", ""), session=session).postings
    elif platform in ("generic", "custom", "html"):
        from services.poller.generic_scraper import scrape_career_page
        return scrape_career_page(board_url or company.get("api_endpoint", ""), session=session)
    else:
        logger.warning("Unsupported ATS platform: %s for %s", platform, slug)
        return []


def run_poll_cycle(storage: JobStorage, ats_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    companies = storage.get_companies_to_poll(ats_platform=ats_filter)
    logger.info("Starting poll cycle for %d companies", len(companies))
    session = requests.Session()
    results: List[Dict[str, Any]] = []

    for comp in companies:
        slug = comp.get("board_slug", "")
        platform = comp.get("ats_platform", "")
        try:
            jobs = poll_single_company(comp, session=session)
            batch = {
                "ats_platform": platform,
                "company_slug": slug,
                "polled_at": datetime.now(timezone.utc).isoformat(),
                "jobs": jobs,
            }
            diff_res = process_poll_batch(batch, storage)
            results.append(diff_res)
            time.sleep(0.3)  # 300ms polite delay between companies
        except Exception as exc:
            logger.error("Failed to poll %s (%s): %s", slug, platform, exc)

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


def run_poller_daemon(
    storage: Optional[JobStorage] = None,
    poll_interval_seconds: int = 1800,
    query_discovery_interval_seconds: int = 14400,
) -> None:
    """Continuous poller daemon for systemd/Docker service runner."""
    store = storage or JobStorage()
    logger.info("Poller daemon started (poll_interval=%ds, query_discovery_interval=%ds)",
                poll_interval_seconds, query_discovery_interval_seconds)

    last_query_discovery_time = 0.0

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        logger.info("Poller daemon received shutdown signal %s", sig)
        running = False

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    while running:
        try:
            # 1. ATS Board Polling Cycle
            logger.info("Triggering ATS company poll cycle...")
            run_poll_cycle(store)

            # 2. Real-Time Query Discovery Cycle (runs every 4 hours by default)
            now = time.time()
            if now - last_query_discovery_time >= query_discovery_interval_seconds:
                logger.info("Triggering real-time query discovery cycle...")
                run_query_discovery_cycle(store)
                last_query_discovery_time = now

        except Exception as exc:
            logger.error("Error during poller daemon cycle: %s", exc, exc_info=True)

        for _ in range(poll_interval_seconds):
            if not running:
                break
            time.sleep(1)

    logger.info("Poller daemon exited cleanly.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    run_poller_daemon()
