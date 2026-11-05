"""Robust Unified Job Fetching Script.

Supports:
1. Public ATS APIs (Greenhouse, Lever, Ashby, SmartRecruiters, Workday).
2. Universal HTML & Schema.org JSON-LD web scraping via BeautifulSoup.
3. Query-Driven Real-Time Discovery across live boards (LinkedIn Guest, Google Jobs / JSearch, Jobicy, Arbeitnow).
4. Location and country normalization via geo_reference.py.
5. Direct terminal output or persistence to JobStorage / DiffEngine.

Examples:
    python scripts/fetch_jobs.py --query "Network Engineer" --today --limit 10
    python scripts/fetch_jobs.py --ats greenhouse --slug stripe --limit 5
    python scripts/fetch_jobs.py --url https://jobs.lever.co/netflix --limit 5
    python scripts/fetch_jobs.py --query "Cloud Network Engineer" --save-db
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

# Ensure project root is in python path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests

from services.poller.greenhouse import poll_greenhouse
from services.poller.lever import poll_lever
from services.poller.ashby import poll_ashby
from services.poller.workday import poll_workday
from services.poller.smartrecruiters import poll_smartrecruiters
from services.poller.generic_scraper import scrape_career_page, build_resilient_session
from services.poller.query_discovery import search_all_fresh_jobs, DiscoveredQueryJob
from services.poller.ats_resolver import resolve_and_seed_job, detect_ats_from_url
import geo_reference

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fetch_jobs")


def fetch_jobs_from_source(
    ats: str,
    slug_or_url: str,
    session: Optional[requests.Session] = None,
    use_ai: bool = False,
    company_name: str = "",
) -> List[Dict[str, Any]]:
    """Fetches and standardizes jobs using the appropriate ATS adapter or universal scraper."""
    s = session or build_resilient_session()
    ats = ats.lower()
    jobs: List[Dict[str, Any]] = []

    logger.info("Fetching jobs using ATS '%s' for target '%s'", ats, slug_or_url)

    if ats == "greenhouse":
        jobs = poll_greenhouse(slug_or_url, session=s)
    elif ats == "lever":
        jobs = poll_lever(slug_or_url, session=s)
    elif ats == "ashby":
        jobs = poll_ashby(slug_or_url, session=s)
    elif ats == "workday":
        jobs = poll_workday(slug_or_url, session=s)
    elif ats == "smartrecruiters":
        jobs = poll_smartrecruiters(slug_or_url, session=s)
    elif ats in ("generic", "html", "custom"):
        jobs = scrape_career_page(slug_or_url, session=s)
        if not jobs and use_ai:
            logger.info("Universal scraper found 0 jobs; invoking Tier 3 AI extractor...")
            from services.poller.ai_job_extractor import extract_jobs_with_ai
            try:
                resp = s.get(slug_or_url, timeout=20)
                if resp.status_code == 200:
                    jobs = extract_jobs_with_ai(resp.text, slug_or_url)
            except Exception as exc:
                logger.error("AI extraction error: %s", exc)
    else:
        raise ValueError(f"Unsupported ATS platform: {ats}")

    company = company_name or slug_or_url
    for job in jobs:
        job["company"] = company
        loc = job.get("location", "")
        if loc and loc != "Not Specified":
            countries = geo_reference.location_countries(loc)
            job["country_code"] = next(iter(countries)) if countries else ""
        else:
            job["country_code"] = ""

    return jobs


def fetch_jobs_via_query(
    query: str,
    location: str = "United States",
    past_24_hours: bool = True,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Discovers real-time jobs matching keywords and resolves destination ATS URLs."""
    raw_jobs = search_all_fresh_jobs(
        keywords=query,
        location=location,
        past_24_hours=past_24_hours,
        limit=limit,
    )

    standardized_jobs: List[Dict[str, Any]] = []
    for rj in raw_jobs:
        # Resolve destination ATS and unroll any tracking redirects
        resolved = resolve_and_seed_job(rj.company_name, rj.url)

        # Country normalization
        countries = geo_reference.location_countries(rj.location)
        country_code = next(iter(countries)) if countries else ""

        standardized_jobs.append({
            "id": rj.job_id or rj.url,
            "title": rj.title,
            "company": rj.company_name,
            "company_token": resolved["company_token"],
            "ats_platform": resolved["ats_type"],
            "location": rj.location,
            "country_code": country_code,
            "remote": rj.is_remote,
            "apply_url": resolved["canonical_url"],
            "description": rj.description,
            "posted_date": rj.posted_date,
            "source": rj.source,
        })

    return standardized_jobs


def save_jobs_to_database(jobs: List[Dict[str, Any]], ats_platform: str, company: str) -> int:
    """Persists fetched jobs into PostgreSQL / SQLite JobStorage via DiffEngine."""
    from services.diff_engine.storage import JobStorage
    from services.diff_engine.diff_engine import process_poll_batch

    storage = JobStorage()
    storage.upsert_company(
        company_name=company,
        ats_platform=ats_platform,
        board_slug=company.lower().replace(" ", "-"),
        board_url=jobs[0]["apply_url"] if jobs else "",
    )
    res = process_poll_batch(jobs, storage, ats_platform, company)
    logger.info("Saved to database: %d new, %d updated, %d active", res["new"], res["updated"], res["active"])
    return res["new"] + res["updated"]


def format_table(jobs: List[Dict[str, Any]], limit: int = 20) -> str:
    """Format job list into an easy-to-read ASCII table."""
    shown = jobs[:limit]
    lines = [
        f"{'#':<3} | {'COMPANY':<22} | {'TITLE':<32} | {'LOCATION':<18} | {'REMOTE':<6} | {'APPLY URL'}",
        "-" * 125,
    ]
    for i, j in enumerate(shown, 1):
        comp = j.get("company", "")[:20]
        t = j.get("title", "")[:30]
        loc = j.get("location", "")[:16]
        rem = "Yes" if j.get("remote") else "No"
        url = j.get("apply_url", "")
        lines.append(f"{i:<3} | {comp:<22} | {t:<32} | {loc:<18} | {rem:<6} | {url}")
    if len(jobs) > limit:
        lines.append(f"... and {len(jobs) - limit} more jobs (use --limit to show more)")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Robust Universal Job Fetcher")
    parser.add_argument("--query", help="Job title or keywords to search (e.g. 'Network Engineer')")
    parser.add_argument("--today", action="store_true", default=True, help="Filter strictly to jobs posted today / past 24h")
    parser.add_argument("--location", default="United States", help="Geographic search location")
    parser.add_argument("--ats", choices=["greenhouse", "lever", "ashby", "workday", "smartrecruiters", "generic", "auto"],
                        default="auto", help="ATS platform to query")
    parser.add_argument("--slug", help="Company board slug (e.g. 'stripe')")
    parser.add_argument("--url", help="Careers portal or board URL")
    parser.add_argument("--company", help="Company name (optional, defaults to slug)")
    parser.add_argument("--use-ai", action="store_true", help="Enable Tier 3 AI extractor for unstructured HTML")
    parser.add_argument("--limit", type=int, default=25, help="Max jobs to display")
    parser.add_argument("--save-db", action="store_true", help="Save jobs to JobStorage / DiffEngine")
    parser.add_argument("--json", action="store_true", help="Output raw JSON to stdout")
    parser.add_argument("--country", help="Filter by ISO country code (e.g. 'US')")

    args = parser.parse_args()

    # Route 1: Query-driven discovery
    if args.query:
        logger.info("Executing query discovery for '%s' in '%s' (today=%s)", args.query, args.location, args.today)
        jobs = fetch_jobs_via_query(
            query=args.query,
            location=args.location,
            past_24_hours=args.today,
            limit=args.limit,
        )
        company = f"Search: {args.query}"
        ats = "query_discovery"

    # Route 2: Target company / URL direct scraping
    elif args.slug or args.url:
        target = args.slug or args.url
        ats = args.ats
        if ats == "auto":
            if args.url:
                detected, token = detect_ats_from_url(args.url)
                ats = detected or "generic"
                target = token or args.url
            else:
                ats = "greenhouse"

        company = args.company or (args.slug if args.slug else urlparse(target).netloc)

        try:
            jobs = fetch_jobs_from_source(
                ats=ats,
                slug_or_url=target,
                use_ai=args.use_ai,
                company_name=company,
            )
        except Exception as exc:
            logger.error("Job fetching failed: %s", exc)
            sys.exit(1)
    else:
        parser.error("Must provide either --query, --slug, or --url")

    if args.country:
        code = args.country.upper()
        jobs = [j for j in jobs if j.get("country_code") == code or code in j.get("location", "").upper()]

    if args.save_db:
        save_jobs_to_database(jobs, ats, company)

    if args.json:
        print(json.dumps(jobs[:args.limit], indent=2, default=str))
    else:
        print(f"\nDiscovered {len(jobs)} jobs for '{company}':\n")
        print(format_table(jobs, limit=args.limit))


if __name__ == "__main__":
    main()
