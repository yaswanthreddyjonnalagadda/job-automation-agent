"""Diff Engine for job polling batches and ATS ReaderResult objects.
Calculates newly posted jobs, removed postings, and updates freshness timestamps.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from services.diff_engine.storage import JobStorage

if TYPE_CHECKING:
    from services.poller.reader_result import ReaderResult

logger = logging.getLogger("diff_engine")


def _resolve_read_args(
    arg1: Any,
    arg2: Any,
    storage: Optional[JobStorage],
    company: Optional[Any],
    ats_platform: Optional[str],
    company_slug: Optional[str],
) -> tuple[Optional[JobStorage], Any, str, str, str]:
    from services.poller.reader_result import ReaderResult

    target_storage = storage
    target_result = None
    target_comp = company

    for a in (arg1, arg2):
        if a is None:
            continue
        if isinstance(a, JobStorage) or hasattr(a, "get_active_job_ids"):
            target_storage = a
        elif isinstance(a, ReaderResult) or (hasattr(a, "postings") and hasattr(a, "complete")):
            target_result = a
        elif isinstance(a, dict) and "jobs" not in a and not hasattr(a, "postings"):
            target_comp = a
        elif isinstance(a, str) and not company_slug:
            company_slug = a

    if isinstance(target_comp, dict):
        ats_platform = ats_platform or target_comp.get("ats_platform") or target_comp.get("platform") or ""
        company_slug = (
            company_slug
            or target_comp.get("board_slug")
            or target_comp.get("company_slug")
            or target_comp.get("slug")
            or ""
        )
        company_name = target_comp.get("company_name") or target_comp.get("name") or company_slug
    elif isinstance(target_comp, str):
        company_slug = company_slug or target_comp
        company_name = company_slug
    else:
        company_name = company_slug or ""

    if target_result is None:
        if isinstance(arg1, dict) and "jobs" in arg1:
            jobs = arg1.get("jobs", [])
            complete = arg1.get("complete", True)
            err = arg1.get("error")
            ats_platform = ats_platform or arg1.get("ats_platform", "")
            company_slug = company_slug or arg1.get("company_slug", "")
            target_result = ReaderResult(postings=jobs, complete=complete, error=err)
        elif isinstance(arg1, list):
            target_result = ReaderResult(postings=arg1, complete=True, error=None)
        else:
            target_result = ReaderResult(postings=[], complete=False, error="no_result")

    return target_storage, target_result, ats_platform or "", company_slug or "", company_name or ""


def apply_read_result(
    arg1: Any,
    arg2: Any = None,
    storage: Optional[JobStorage] = None,
    *,
    company: Optional[Any] = None,
    ats_platform: Optional[str] = None,
    company_slug: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply an ATS ReaderResult (or batch) to storage.

    Rules:
      1. New jobs in result.postings are inserted as active.
      2. Still-active jobs have last_seen updated.
      3. Removed jobs are closed ONLY if the read was complete and without error.
         A failed or incomplete read closes nothing.
    """
    target_storage, target_result, ats_platform, company_slug, company_name = _resolve_read_args(
        arg1, arg2, storage, company, ats_platform, company_slug
    )

    if target_storage is None:
        raise ValueError("JobStorage instance is required for apply_read_result")

    postings = target_result.postings or []
    is_complete = bool(getattr(target_result, "complete", False))
    error = getattr(target_result, "error", None)

    incoming_by_id = {str(j["external_id"]): j for j in postings if j.get("external_id")}
    incoming_ids = set(incoming_by_id.keys())
    stored_ids: Set[str] = (
        target_storage.get_active_job_ids(ats_platform, company_slug)
        if (ats_platform and company_slug) else set()
    )

    # 1. New jobs
    new_ids = incoming_ids - stored_ids
    new_job_ids: List[int] = []
    for ext_id in new_ids:
        job_data = dict(incoming_by_id[ext_id])
        job_data["ats_platform"] = ats_platform
        job_data["company_slug"] = company_slug
        if "company_name" not in job_data and company_name:
            job_data["company_name"] = company_name
        job_data["status"] = "active"
        inserted_id = target_storage.insert_job(job_data)
        new_job_ids.append(inserted_id)
        logger.info("New job discovered: %s (%s) [%s]", job_data.get("title"), company_slug, ext_id)

    # 2. Removed jobs: ONLY when read was successful and complete
    removed_ids: Set[str] = set()
    if is_complete and not error:
        removed_ids = stored_ids - incoming_ids
        for ext_id in removed_ids:
            target_storage.mark_removed(ext_id, ats_platform)
            logger.info("Job removed: %s [%s]", company_slug, ext_id)
    else:
        logger.info(
            "Failed or incomplete read for %s on %s (complete=%s, error=%s): closing nothing (preserved %d active jobs)",
            company_slug, ats_platform, is_complete, error, len(stored_ids)
        )

    # 3. Still active jobs
    still_active = incoming_ids & stored_ids
    if still_active:
        target_storage.bulk_update_last_seen(still_active, ats_platform)

    return {
        "ats_platform": ats_platform,
        "company_slug": company_slug,
        "incoming_total": len(incoming_ids),
        "new_count": len(new_ids),
        "new": len(new_ids),
        "removed_count": len(removed_ids),
        "removed": len(removed_ids),
        "still_active_count": len(still_active),
        "active": len(still_active),
        "updated": len(still_active),
        "new_job_ids": new_job_ids,
        "complete": is_complete,
        "error": error,
    }


def process_poll_batch(
    batch: Any,
    storage: JobStorage,
    ats_platform: Optional[str] = None,
    company_slug: Optional[str] = None,
) -> Dict[str, Any]:
    """Process a batch of scraped/polled jobs from an ATS platform. Backward compatibility wrapper."""
    from services.poller.reader_result import ReaderResult

    if isinstance(batch, dict):
        jobs = batch.get("jobs", [])
        complete = batch.get("complete", True)
        error = batch.get("error", None)
        plat = batch.get("ats_platform", ats_platform or "")
        slug = batch.get("company_slug", company_slug or "")
        rr = ReaderResult(postings=jobs, complete=complete, error=error)
        return apply_read_result(rr, storage, ats_platform=plat, company_slug=slug)
    elif isinstance(batch, list):
        rr = ReaderResult(postings=batch, complete=True, error=None)
        return apply_read_result(rr, storage, ats_platform=ats_platform or "", company_slug=company_slug or "")
    else:
        return apply_read_result(batch, storage, ats_platform=ats_platform, company_slug=company_slug)
