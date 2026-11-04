"""Poller Service: polls active companies across all ATS platforms and sends batches to the Diff Engine."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
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
