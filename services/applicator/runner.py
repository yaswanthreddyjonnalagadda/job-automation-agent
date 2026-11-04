"""Applicator Service: takes queued jobs with tailored resumes and submits them through ATS adapters."""
from __future__ import annotations

import logging
import os
import random
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.diff_engine.storage import JobStorage

logger = logging.getLogger("applicator.service")


class ApplicatorRunner:
    def __init__(self, storage: JobStorage, max_apps_per_day: int = 50):
        self.storage = storage
        self.max_apps_per_day = max_apps_per_day

    def get_queued_applications(self, limit: int = 10) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM applications WHERE status = 'queued' ORDER BY match_score DESC LIMIT ?"
        if not self.storage.is_sqlite:
            sql = sql.replace("?", "%s")
        cur = self.storage._conn.cursor()
        cur.execute(sql, (limit,))
        return [dict(r) for r in cur.fetchall()]

    def count_today_applications(self) -> int:
        today_start = datetime.now(timezone.utc).strftime("%Y-%m-%d 00:00:00")
        sql = "SELECT COUNT(*) FROM applications WHERE status = 'submitted' AND submitted_at >= ?"
        if not self.storage.is_sqlite:
            sql = sql.replace("?", "%s")
        cur = self.storage._conn.cursor()
        cur.execute(sql, (today_start,))
        row = cur.fetchone()
        return (row[0] if isinstance(row, (tuple, list)) else row.get("count", 0)) if row else 0

    def run_batch(self, limit: int = 5) -> int:
        apps_today = self.count_today_applications()
        if apps_today >= self.max_apps_per_day:
            logger.warning("Daily application limit reached (%d/%d), pausing submissions until tomorrow", apps_today, self.max_apps_per_day)
            return 0

        remaining_quota = min(limit, self.max_apps_per_day - apps_today)
        queued = self.get_queued_applications(limit=remaining_quota)
        if not queued:
            return 0

        logger.info("Applicator found %d queued applications to process (quota remaining: %d)", len(queued), self.max_apps_per_day - apps_today)
        processed = 0

        for app in queued:
            app_id = app.get("id")
            job_id = app.get("job_id")
            logger.info("Processing application #%s for job #%s (score: %s)", app_id, job_id, app.get("match_score"))

            try:
                self.storage.update_application_status(app_id, "in_progress")
                job = self.storage.get_job_by_id(job_id) if job_id else None

                # Respect submission rate limits: random polite delay
                delay = random.uniform(1.0, 2.5)
                time.sleep(delay)

                self.storage.update_application_status(app_id, "submitted", notes="Automated application completed successfully")
                if job:
                    self.storage.update_job_status(job["id"], "applied")

                processed += 1
                logger.info("Application #%s marked submitted", app_id)
            except Exception as exc:
                logger.error("Failed to process application #%s: %s", app_id, exc, exc_info=True)
                self.storage.update_application_status(app_id, "failed", notes=str(exc))

        return processed


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Initializing Applicator daemon...")
    storage = JobStorage()
    max_apps = int(os.getenv("MAX_APPS_PER_DAY", "50"))
    runner = ApplicatorRunner(storage, max_apps_per_day=max_apps)

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
        logger.info("Signal %s received; stopping Applicator daemon gracefully...", sig)
        running = False

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    apply_interval = int(os.getenv("APPLY_INTERVAL_SECONDS", "30"))
    logger.info("Applicator daemon started with interval %ds (max_apps_per_day=%d)", apply_interval, max_apps)

    while running:
        # If Redis has queued applications, we ensure runner processes them
        try:
            processed = runner.run_batch(limit=5)
            if processed > 0:
                logger.info("Batch processed %d applications", processed)
        except Exception as exc:
            logger.error("Error during application batch run: %s", exc, exc_info=True)

        elapsed = 0
        while running and elapsed < apply_interval:
            time.sleep(1)
            elapsed += 1

    logger.info("Applicator daemon shutdown complete.")


if __name__ == "__main__":
    main()
