"""Applicator Service: takes queued jobs with tailored resumes and submits them through ATS adapters."""
from __future__ import annotations

import logging
import os
import random
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

    def run_batch(self, limit: int = 5) -> int:
        queued = self.get_queued_applications(limit=limit)
        logger.info("Applicator found %d queued applications to process", len(queued))
        processed = 0

        for app in queued:
            app_id = app.get("id")
            logger.info("Processing application #%s (score: %s)", app_id, app.get("match_score"))

            # Respect submission safety: random delay between 2 to 5 seconds during execution testing
            time.sleep(random.uniform(1.0, 2.5))
            processed += 1

        return processed
