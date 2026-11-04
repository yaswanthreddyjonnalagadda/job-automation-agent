"""Bootstrap seed list of high-priority hiring companies across ATS platforms."""
from __future__ import annotations

from typing import Any, Dict, List
from services.diff_engine.storage import JobStorage

SEED_COMPANIES: List[Dict[str, Any]] = [
    # Greenhouse
    {
        "company_name": "Stripe",
        "domain": "stripe.com",
        "ats_platform": "greenhouse",
        "board_slug": "stripe",
        "board_url": "https://boards.greenhouse.io/stripe",
        "api_endpoint": "https://boards-api.greenhouse.io/v1/boards/stripe/jobs?content=true",
        "discovered_via": "bootstrap",
    },
    {
        "company_name": "Cloudflare",
        "domain": "cloudflare.com",
        "ats_platform": "greenhouse",
        "board_slug": "cloudflare",
        "board_url": "https://boards.greenhouse.io/cloudflare",
        "api_endpoint": "https://boards-api.greenhouse.io/v1/boards/cloudflare/jobs?content=true",
        "discovered_via": "bootstrap",
    },
    {
        "company_name": "Reddit",
        "domain": "reddit.com",
        "ats_platform": "greenhouse",
        "board_slug": "reddit",
        "board_url": "https://boards.greenhouse.io/reddit",
        "api_endpoint": "https://boards-api.greenhouse.io/v1/boards/reddit/jobs?content=true",
        "discovered_via": "bootstrap",
    },
    # Lever
    {
        "company_name": "Palantir",
        "domain": "palantir.com",
        "ats_platform": "lever",
        "board_slug": "palantir",
        "board_url": "https://jobs.lever.co/palantir",
        "api_endpoint": "https://api.lever.co/v0/postings/palantir",
        "discovered_via": "bootstrap",
    },
    {
        "company_name": "Figma",
        "domain": "figma.com",
        "ats_platform": "lever",
        "board_slug": "figma",
        "board_url": "https://jobs.lever.co/figma",
        "api_endpoint": "https://api.lever.co/v0/postings/figma",
        "discovered_via": "bootstrap",
    },
    # Ashby
    {
        "company_name": "OpenAI",
        "domain": "openai.com",
        "ats_platform": "ashby",
        "board_slug": "openai",
        "board_url": "https://jobs.ashbyhq.com/openai",
        "api_endpoint": "https://api.ashbyhq.com/posting-api/job-board/openai?includeCompensation=true",
        "discovered_via": "bootstrap",
    },
    {
        "company_name": "Linear",
        "domain": "linear.app",
        "ats_platform": "ashby",
        "board_slug": "linear",
        "board_url": "https://jobs.ashbyhq.com/linear",
        "api_endpoint": "https://api.ashbyhq.com/posting-api/job-board/linear?includeCompensation=true",
        "discovered_via": "bootstrap",
    },
    # Workday
    {
        "company_name": "Analog Devices Inc.",
        "domain": "analog.com",
        "ats_platform": "workday",
        "board_slug": "analogdevices",
        "board_url": "https://analogdevices.wd1.myworkdayjobs.com/Analog_Devices_External",
        "api_endpoint": "https://analogdevices.wd1.myworkdayjobs.com/wday/cxs/analogdevices/Analog_Devices_External/jobs",
        "discovered_via": "bootstrap",
    },
]


def seed_directory(storage: JobStorage) -> int:
    count = 0
    for comp in SEED_COMPANIES:
        storage.register_company(comp)
        count += 1
    return count
