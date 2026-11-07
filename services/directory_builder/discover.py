"""Board Discovery from Common Crawl.
Discovers new ATS boards, checks them with readers, counts matching titles,
and registers them in company_directory with status 'pending' (NEVER 'live').
"""
from __future__ import annotations

import argparse
import logging
import re
from typing import Any, Dict, List, Optional, Set
import requests

from discovery_sources import minimal_titles
from services.diff_engine.storage import JobStorage
from services.directory_builder.common_crawl import (
    ATS_PATTERNS,
    PROVIDER_HOSTS,
    extract_board,
    query_cdx,
)
from services.poller.ashby import read_ashby_board
from services.poller.bamboohr import read_bamboohr_board
from services.poller.greenhouse import read_greenhouse_board
from services.poller.lever import read_lever_board
from services.poller.reader_result import ReaderResult
from services.poller.smartrecruiters import read_smartrecruiters_board
from services.poller.workday import read_workday_board

logger = logging.getLogger("directory.discover")

DEFAULT_TARGET_ROLES = [
    "Network Engineer",
    "Senior Network Engineer",
    "Cloud Network Engineer",
    "Network Security Engineer",
    "Data Center Network Engineer",
    "Systems Engineer",
    "DevOps Engineer",
    "Software Engineer",
]


def _read_candidate_board(provider: str, board: str) -> ReaderResult:
    """Read a candidate board using provider reader without fetching full details."""
    prov = provider.lower()
    if prov == "workday":
        board_url = f"https://{board}" if not board.startswith("http") else board
        import services.poller.workday as wd_mod
        orig_pick = getattr(wd_mod, "pick_split_dimension", None)
        if orig_pick:
            def fast_pick(facets, used):
                d = orig_pick(facets, used)
                if d and len(d.values) > 10:
                    d.values = d.values[:10]
                return d
            try:
                wd_mod.pick_split_dimension = fast_pick
                return read_workday_board(board_url, known_ids=lambda _: True)
            finally:
                wd_mod.pick_split_dimension = orig_pick
        return read_workday_board(board_url, known_ids=lambda _: True)
    elif prov in ("greenhouse", "greenhouse2"):
        return read_greenhouse_board(board)
    elif prov == "lever":
        return read_lever_board(board)
    elif prov == "ashby":
        return read_ashby_board(board)
    elif prov == "smartrecruiters":
        return read_smartrecruiters_board(board)
    elif prov == "bamboohr":
        return read_bamboohr_board(board)
    else:
        return ReaderResult(postings=[], complete=False, error=f"unsupported_{prov}")


def run_discovery(
    provider: str,
    limit: int = 500,
    storage: Optional[JobStorage] = None,
    target_roles: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run board discovery for a given ATS provider."""
    store = storage or JobStorage()
    pattern = ATS_PATTERNS.get(provider)
    if not pattern:
        raise ValueError(f"Unknown ATS provider: {provider}")

    roles = target_roles or DEFAULT_TARGET_ROLES
    min_roles = minimal_titles(roles)

    logger.info("Discovering candidate boards for provider '%s' (limit=%d)...", provider, limit)
    raw_urls = query_cdx(pattern, limit=limit)

    candidates: Set[str] = set()
    for u in raw_urls:
        b = extract_board(provider, u)
        if b:
            candidates.add(b)

    real_boards = 0
    matching_boards = 0
    discovered_list: List[Dict[str, Any]] = []

    for board in sorted(candidates):
        # Skip if already registered
        existing = store.get_board(provider, board)
        if existing:
            continue

        try:
            res = _read_candidate_board(provider, board)
        except Exception as exc:
            logger.debug("Board read exception for %s (%s): %s", board, provider, exc)
            continue

        if res.error and (res.error == "board_not_found" or res.error.startswith("http_4")):
            continue

        real_boards += 1
        postings = res.postings or []
        postings_count = len(postings)

        # Count matching titles
        matching_count = 0
        for p in postings:
            title = p.get("title", "")
            words = set(re.findall(r"\w+", title.lower()))
            if any(set(re.findall(r"\w+", r.lower())).issubset(words) for r in min_roles):
                matching_count += 1

        if matching_count > 0:
            matching_boards += 1

        # Derive metadata and register as PENDING
        if provider == "workday":
            host_label = board.split("/")[0].split(".")[0]
            site_label = board.split("/")[1] if "/" in board else ""
            company_name = f"{host_label.replace('-', ' ').title()} ({site_label})" if site_label else host_label.title()
            board_url = f"https://{board}"
            api_endpoint = f"https://{board.split('/')[0]}/wday/cxs/{host_label}/{site_label}/jobs" if site_label else board_url
        else:
            company_name = board.replace("-", " ").title()
            expected_host = PROVIDER_HOSTS.get(provider, "")
            board_url = f"https://{expected_host}/{board}" if expected_host else board
            api_endpoint = board_url

        board_id = store.register_company({
            "company_name": company_name,
            "ats_platform": provider,
            "board_slug": board,
            "board_url": board_url,
            "api_endpoint": api_endpoint,
            "total_jobs_last": postings_count,
            "discovered_via": "common_crawl",
        })

        # ALWAYS set status to pending, NEVER live
        store.set_board_status(board_id, "pending")
        if hasattr(store, "_conn"):
            sql = "UPDATE company_directory SET total_jobs_last = ? WHERE id = ?"
            if not getattr(store, "is_sqlite", True):
                sql = sql.replace("?", "%s")
            with getattr(store, "_conn"):
                store._conn.execute(sql, (postings_count, board_id))

        discovered_list.append({
            "id": board_id,
            "board": board,
            "total_jobs": postings_count,
            "matching_jobs": matching_count,
        })

        logger.info(
            "Registered pending board %s: %d total jobs, %d matching roles",
            board, postings_count, matching_count
        )

    print(f"Candidates found: {len(candidates)}", flush=True)
    print(f"Real boards: {real_boards}", flush=True)
    print(f"Boards with >= 1 matching title: {matching_boards}", flush=True)

    return {
        "candidates": len(candidates),
        "real_boards": real_boards,
        "matching_boards": matching_boards,
        "discovered": discovered_list,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover new ATS boards via Common Crawl.")
    parser.add_argument("--provider", required=True, choices=list(ATS_PATTERNS.keys()), help="ATS provider")
    parser.add_argument("--limit", type=int, default=500, help="Max candidate URLs to mine")
    parser.add_argument("--db", default=None, help="SQLite/Postgres DSN (default from env)")
    args = parser.parse_args()

    storage = JobStorage(dsn=args.db) if args.db else None
    run_discovery(provider=args.provider, limit=args.limit, storage=storage)


if __name__ == "__main__":
    main()
