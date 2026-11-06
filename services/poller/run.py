"""Scheduled job-source crawl runner: one provider (or all) per invocation.

Usage:
    python -m services.poller.run --provider greenhouse
    python -m services.poller.run --all

Takes a per-provider lock first (a Postgres advisory lock, or an exclusive
lock file under data/ for SQLite); a second run while the lock is held prints
"already running" and exits 0. Reads every live board for the provider through
poll_single_company, applies each ReaderResult with apply_read_result, stays
polite (at most 1 request per second to the same host, 0.5 s between boards),
records the run with finish_crawl_run, and always releases the lock.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import requests

from services.diff_engine.diff_engine import apply_read_result
from services.diff_engine.storage import JobStorage
from services.poller.poller_service import poll_single_company

logger = logging.getLogger("poller.run")

LOCK_STALE_SECONDS = 3 * 3600
HOST_MIN_INTERVAL = 1.0
BOARD_DELAY = 0.5


def lock_dir() -> Path:
    override = os.environ.get("JOB_SOURCE_DATA_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent.parent / "data"


def lock_path(provider: str) -> Path:
    return lock_dir() / f"job_source_{provider}.lock"


def advisory_key(provider: str) -> int:
    return zlib.crc32(f"job-source:{provider}".encode("utf-8")) & 0x7FFFFFFF


def _acquire_sqlite_lock(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            age = time.time() - path.stat().st_mtime
        except FileNotFoundError:
            return _acquire_sqlite_lock(path)
        if age <= LOCK_STALE_SECONDS:
            return False
        logger.warning("Taking over stale lock %s (age %.0fs)", path, age)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return _acquire_sqlite_lock(path)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"{os.getpid()} {time.time()}")
    return True


def _acquire_postgres_lock(storage: JobStorage, provider: str) -> bool:
    with storage._conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (advisory_key(provider),))
        row = cur.fetchone()
    storage._conn.commit()
    if isinstance(row, dict):
        return bool(row.get("pg_try_advisory_lock"))
    return bool(row[0])


def acquire_lock(provider: str, storage: JobStorage) -> bool:
    if storage.is_sqlite:
        return _acquire_sqlite_lock(lock_path(provider))
    return _acquire_postgres_lock(storage, provider)


def release_lock(provider: str, storage: JobStorage) -> None:
    if storage.is_sqlite:
        try:
            lock_path(provider).unlink()
        except FileNotFoundError:
            pass
        return
    with storage._conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (advisory_key(provider),))
    storage._conn.commit()


def _board_host(board: Dict[str, Any], provider: str) -> str:
    for key in ("board_url", "api_endpoint"):
        url = (board.get(key) or "").strip()
        if url:
            host = urlsplit(url).netloc.lower()
            if host:
                return host
    return provider


def run_provider(
    provider: str,
    storage: JobStorage,
    session: Optional[requests.Session] = None,
    now: Optional[datetime] = None,
) -> Dict[str, int]:
    provider = provider.lower()
    boards = [b for b in storage.boards(status="live")
              if (b.get("ats_platform") or "").lower() == provider]
    now = now or datetime.now(timezone.utc)
    run_id = storage.start_crawl_run(provider, now=now)
    session = session or requests.Session()
    total, complete, failed, new, reopened, closed = len(boards), 0, 0, 0, 0, 0
    last_seen: Dict[str, float] = {}
    try:
        for index, board in enumerate(boards):
            slug = board.get("board_slug", "")
            host = _board_host(board, provider)
            wait = HOST_MIN_INTERVAL - (time.monotonic() - last_seen.get(host, float("-inf")))
            if wait > 0:
                time.sleep(wait)
            last_seen[host] = time.monotonic()
            try:
                result = poll_single_company(board, session=session, storage=storage)
            except Exception as exc:
                logger.error("Failed to read %s board %s: %s", provider, slug, exc)
                failed += 1
                continue
            diff = apply_read_result(result, provider, slug, storage, run_id=run_id, now=now)
            if diff.get("complete"):
                complete += 1
            else:
                failed += 1
            new += int(diff.get("new_count", 0))
            reopened += int(diff.get("reopened_count", 0))
            closed += int(diff.get("closed_count", 0))
            if index < len(boards) - 1:
                time.sleep(BOARD_DELAY)
    finally:
        storage.finish_crawl_run(run_id, boards_total=total, boards_complete=complete,
                                 boards_failed=failed, now=datetime.now(timezone.utc))
    return {"boards_total": total, "boards_complete": complete, "boards_failed": failed,
            "new": new, "reopened": reopened, "closed": closed}


def _live_providers(storage: JobStorage) -> List[str]:
    return sorted({(b.get("ats_platform") or "").lower()
                   for b in storage.boards(status="live") if b.get("ats_platform")})


def main(argv: Optional[List[str]] = None, storage: Optional[JobStorage] = None) -> int:
    parser = argparse.ArgumentParser(description="Run a job-source crawl.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--provider")
    group.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)
    store = storage or JobStorage()
    providers = [args.provider.lower()] if args.provider else _live_providers(store)
    for provider in providers:
        if not acquire_lock(provider, store):
            print("already running")
            continue
        try:
            summary = run_provider(provider, store)
        finally:
            release_lock(provider, store)
        print(f"{provider}: boards total={summary['boards_total']} "
              f"complete={summary['boards_complete']} failed={summary['boards_failed']} "
              f"new={summary['new']} reopened={summary['reopened']} closed={summary['closed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())