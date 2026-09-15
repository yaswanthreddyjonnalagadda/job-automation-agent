"""
One-off migration: copies the existing SQLite tracker into Postgres and pulls
the generated resumes/cover letters into the documents table.

Safe to re-run -- applications upsert on dedup_key and documents upsert on
content hash, so nothing is duplicated.

    python migrate_to_postgres.py
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

from config import DB_PATH, OUTPUT_DIR
from db import get_tracker

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("migrate")


def main() -> int:
    if not Path(DB_PATH).exists():
        logger.info("No SQLite database at %s -- nothing to migrate.", DB_PATH)
        return 0

    tracker = get_tracker()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM applications ORDER BY created_at").fetchall()
    conn.close()

    migrated = docs = 0
    for row in rows:
        r = dict(row)
        tracker.create(
            dedup_key=r["dedup_key"],
            title=r["title"],
            company=r["company"],
            location=r.get("location") or "",
            source_site=r.get("source_site") or "",
            url=r.get("url") or "",
            resume_path=r.get("resume_path"),
            cover_letter_path=r.get("cover_letter_path"),
            notes=r.get("notes"),
        )
        # create() only ever sets 'prepared'; carry the real status over.
        if r.get("status"):
            tracker.update_status(r["dedup_key"], r["status"], r.get("notes"))
        migrated += 1

        for kind, key in (("resume", "resume_path"), ("cover_letter", "cover_letter_path")):
            path = r.get(key)
            if path and Path(path).is_file():
                if tracker.store_document(r["dedup_key"], kind, path):
                    docs += 1

        # The paths recorded in SQLite predate the PDF rename, so also sweep
        # this application's output folder for anything not already captured.
        folder = Path(OUTPUT_DIR) / f"{r['company'].replace(' ', '_')}_{r['title'].replace(' ', '_')}"
        if folder.is_dir():
            for pdf in sorted(folder.glob("*.pdf")):
                text_twin = folder / "tailored_resume.txt"
                text = text_twin.read_text(encoding="utf-8", errors="replace") if text_twin.is_file() else None
                if tracker.store_document(r["dedup_key"], "resume", pdf, content_text=text):
                    docs += 1
            letter = folder / "cover_letter.txt"
            if letter.is_file():
                if tracker.store_document(
                    r["dedup_key"], "cover_letter", letter,
                    content_text=letter.read_text(encoding="utf-8", errors="replace"),
                ):
                    docs += 1

    logger.info("Migrated %d application(s) and stored %d document(s).", migrated, docs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
