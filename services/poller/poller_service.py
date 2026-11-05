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
from services.poller.ashby import poll_ashby
from services.poller.greenhouse import poll_greenhouse
from services.poller.lever import poll_lever
from services.poller.workday import poll_workday

logger = logging.getLogger("poller.service")


def poll_single_company(company: Dict[str, Any], session: Optional[requests.Session] = None) -> List[Dict[str, Any]]:
    platform = company.get("ats_platform", "").lower()
    slug = company.get("board_slug", "")
    board_url = company.get("board_url", "")

    if platform == "greenhouse":
        return poll_greenhouse(slug, session=session)
    elif platform == "lever":
        return poll_lever(slug, session=session)
    elif platform == "ashby":
        return poll_ashby(slug, session=session)
    elif platform == "workday":
        return poll_workday(board_url or company.get("api_endpoint", ""), session=session)
    elif platform == "smartrecruiters":
        from services.poller.smartrecruiters import poll_smartrecruiters
        return poll_smartrecruiters(slug, session=session)
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
            logger.debug("Could not bootstrap SearchSpec: %s", exc)

    if search_spec:
        query_pairs = search_spec.get_expanded_queries(max_queries=12)
    else:
        query_pairs = [{"title": t, "location": "Remote", "is_remote": True} for t in (target_titles or ["Network Engineer"])]

    total_discovered = 0
    for q in query_pairs[:8]:
        title = q["title"]
        loc = q["location"]
        try:
            logger.info("Executing 4-hour background query discovery for '%s' in '%s'...", title, loc)
            jobs = search_all_fresh_jobs(keywords=title, location=loc, past_24_hours=True, limit=25)
            for j in jobs:
                resolved = resolve_and_seed_job(j.company_name, j.url)
                storage.upsert_company(
                    company_name=j.company_name,
                    ats_platform=resolved["ats_type"],
                    board_slug=resolved["company_token"],
                    board_url=resolved["canonical_url"],
                )
                batch = {
                    "ats_platform": resolved["ats_type"],
                    "company_slug": resolved["company_token"],
                    "polled_at": datetime.now(timezone.utc).isoformat(),
                    "jobs": [{
                        "id": j.job_id or j.url,
                        "title": j.title,
                        "location": j.location,
                        "remote": j.is_remote,
                        "apply_url": resolved["canonical_url"],
                        "description": j.description,
                        "posted_date": j.posted_date,
                    }],
                }
                process_poll_batch(batch, storage)
                total_discovered += 1
        except Exception as exc:
            logger.warning("Query discovery failed for '%s': %s", title, exc)

    logger.info("4-hour background query discovery complete: processed %d jobs", total_discovered)
    return total_discovered


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Initializing Poller daemon...")
    storage = JobStorage()

    # Bootstrap seeds if directory is empty
    active_companies = storage.get_companies_to_poll()
    if not active_companies:
        logger.info("Company directory is empty. Seeding initial directory...")
        try:
            from services.directory_builder.bootstrap_seeds import seed_directory
            count = seed_directory(storage)
            logger.info("Successfully seeded %d initial companies", count)
        except Exception as exc:
            logger.warning("Could not auto-seed directory: %s", exc)

    # Optional Redis connection
    redis_client = None
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            import redis
            redis_client = redis.from_url(redis_url, decode_responses=True)
            redis_client.ping()
            logger.info("Connected to Redis at %s", redis_url)
        except Exception as exc:
            logger.warning("Redis connection failed (%s); fallback to DB storage", exc)
            redis_client = None

    running = True

    def _handle_signal(sig, frame):
        nonlocal running
        logger.info("Signal %s received; stopping Poller daemon gracefully...", sig)
        running = False

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    poll_interval = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))
    query_discovery_interval = int(os.getenv("QUERY_DISCOVERY_INTERVAL_SECONDS", str(4 * 3600)))  # Every 4 hours
    last_query_time = 0.0

    logger.info("Poller daemon started with interval %ds, query discovery interval %ds (4h)", poll_interval, query_discovery_interval)

    while running:
        current_time = time.time()
        # Run 4-hour fresh query discovery cycle
        if current_time - last_query_time >= query_discovery_interval:
            logger.info("Triggering scheduled 4-hour query discovery cycle...")
            try:
                run_query_discovery_cycle(storage)
            except Exception as exc:
                logger.error("Error during query discovery cycle: %s", exc)
            last_query_time = current_time

        logger.info("Starting standard poll cycle execution...")
        try:
            results = run_poll_cycle(storage)
            total_new = sum(r.get("new_count", 0) for r in results)
            logger.info("Poll cycle completed: discovered %d new jobs across %d companies", total_new, len(results))

            if redis_client:
                for r in results:
                    for jid in r.get("new_job_ids", []):
                        redis_client.rpush("jobs:matching", str(jid))
        except Exception as exc:
            logger.error("Error during poll cycle: %s", exc, exc_info=True)

        elapsed = 0
        while running and elapsed < poll_interval:
            time.sleep(1)
            elapsed += 1

    logger.info("Poller daemon shutdown complete.")


if __name__ == "__main__":
    main()
