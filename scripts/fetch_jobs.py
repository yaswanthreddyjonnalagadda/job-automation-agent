"""Robust Unified Job Fetching Script.

Supports:
1. Public ATS APIs (Greenhouse, Lever, Ashby, SmartRecruiters, Workday).
2. Universal HTML & Schema.org JSON-LD web scraping via BeautifulSoup.
3. Optional external AI API fallback (Gemini / Claude) for unstructured pages.
4. Location and country normalization via geo_reference.py.
5. Direct terminal output or persistence to JobStorage / DiffEngine.

Examples:
    python scripts/fetch_jobs.py --ats greenhouse --slug stripe --limit 5
    python scripts/fetch_jobs.py --url https://jobs.lever.co/netflix --limit 5
    python scripts/fetch_jobs.py --url https://careers.example.com/jobs --use-ai
    python scripts/fetch_jobs.py --ats smartrecruiters --slug visa --save-db
"""
from __future__ import annotations

import argparse
import json
import logging
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
import geo_reference

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fetch_jobs")


def detect_ats_from_url(url: str) -> tuple[str, str]:
    """Inspects a career URL to detect the ATS platform and extract the company slug."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.strip("/")
    parts = path.split("/")

    if "greenhouse.io" in host:
        slug = parts[0] if parts else ""
        return "greenhouse", slug
    elif "lever.co" in host:
        slug = parts[0] if parts else ""
        return "lever", slug
    elif "ashbyhq.com" in host:
        slug = parts[0] if parts else ""
        return "ashby", slug
    elif "myworkdayjobs.com" in host or "workday" in host:
        return "workday", url
    elif "smartrecruiters.com" in host:
        slug = parts[0] if parts else ""
        return "smartrecruiters", slug

    return "generic", url


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

    # Tag company name if provided
    company = company_name or slug_or_url
    for job in jobs:
        job["company"] = company
        # Location & country normalization
        loc = job.get("location", "")
        if loc and loc != "Not Specified":
            countries = geo_reference.location_countries(loc)
            job["country_code"] = next(iter(countries)) if countries else ""
        else:
            job["country_code"] = ""

    return jobs


def save_jobs_to_database(jobs: List[Dict[str, Any]], ats_platform: str, company: str) -> int:
    """Persists fetched jobs into PostgreSQL / SQLite JobStorage via DiffEngine."""
    from services.diff_engine.storage import JobStorage
    from services.diff_engine.diff_engine import process_poll_batch

    storage = JobStorage()
    # Ensure company directory entry exists
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
        f"{'#':<3} | {'TITLE':<38} | {'LOCATION':<22} | {'REMOTE':<6} | {'APPLY URL'}",
        "-" * 110,
    ]
    for i, j in enumerate(shown, 1):
        t = j.get("title", "")[:36]
        loc = j.get("location", "")[:20]
        rem = "Yes" if j.get("remote") else "No"
        url = j.get("apply_url", "")
        lines.append(f"{i:<3} | {t:<38} | {loc:<22} | {rem:<6} | {url}")
    if len(jobs) > limit:
        lines.append(f"... and {len(jobs) - limit} more jobs (use --limit to show more)")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Robust Universal Job Fetcher")
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

    target = args.slug or args.url
    if not target:
        parser.error("Must provide either --slug or --url")

    ats = args.ats
    if ats == "auto":
        if args.url:
            ats, target = detect_ats_from_url(args.url)
        else:
            ats = "greenhouse"  # Default slug to greenhouse if only slug is given

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

    if args.country:
        code = args.country.upper()
        jobs = [j for j in jobs if j.get("country_code") == code or code in j.get("location", "").upper()]

    if args.save_db:
        save_jobs_to_database(jobs, ats, company)

    if args.json:
        print(json.dumps(jobs[:args.limit], indent=2, default=str))
    else:
        print(f"\nDiscovered {len(jobs)} jobs for '{company}' via {ats.upper()}:\n")
        print(format_table(jobs, limit=args.limit))


if __name__ == "__main__":
    main()
