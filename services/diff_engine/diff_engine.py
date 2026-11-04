"""Diff Engine for job polling batches.
Calculates newly posted jobs, removed postings, and updates freshness timestamps.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from services.diff_engine.storage import JobStorage

logger = logging.getLogger("diff_engine")


def process_poll_batch(batch: Dict[str, Any], storage: JobStorage) -> Dict[str, Any]:
    """Process a batch of scraped/polled jobs from an ATS platform.

    Batch format:
    {
        "ats_platform": "greenhouse",
        "company_slug": "stripe",
        "jobs": [
            {"external_id": "123", "title": "Network Engineer", ...}
        ]
    }
    """
    ats_platform = batch.get("ats_platform", "")
    company_slug = batch.get("company_slug", "")
    incoming_jobs = batch.get("jobs", [])

    incoming_by_id = {str(j["external_id"]): j for j in incoming_jobs if j.get("external_id")}
    incoming_ids = set(incoming_by_id.keys())

    stored_ids = storage.get_active_job_ids(ats_platform, company_slug)

    # 1. New jobs
    new_ids = incoming_ids - stored_ids
    new_job_ids: List[int] = []
    for ext_id in new_ids:
        job_data = incoming_by_id[ext_id]
        job_data["ats_platform"] = ats_platform
        job_data["company_slug"] = company_slug
        job_data["status"] = "active"
        inserted_id = storage.insert_job(job_data)
        new_job_ids.append(inserted_id)
        logger.info("New job discovered: %s (%s) [%s]", job_data.get("title"), company_slug, ext_id)

    # 2. Removed jobs
    removed_ids = stored_ids - incoming_ids
    for ext_id in removed_ids:
        storage.mark_removed(ext_id, ats_platform)
        logger.info("Job removed: %s [%s]", company_slug, ext_id)

    # 3. Still active jobs
    still_active = incoming_ids & stored_ids
    storage.bulk_update_last_seen(still_active, ats_platform)

    return {
        "ats_platform": ats_platform,
        "company_slug": company_slug,
        "incoming_total": len(incoming_ids),
        "new_count": len(new_ids),
        "removed_count": len(removed_ids),
        "still_active_count": len(still_active),
        "new_job_ids": new_job_ids,
    }

