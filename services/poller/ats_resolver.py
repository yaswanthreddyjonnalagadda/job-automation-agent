"""
ATS URL Resolution and Dynamic Company Seeding.

Inspects job URLs, unrolls redirects, identifies the underlying ATS platform
(Greenhouse, Lever, Ashby, Workday, SmartRecruiters, iCIMS), extracts the
company slug/token, and seeds new companies into target_companies.json and
database registries.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import requests

logger = logging.getLogger("ats_resolver")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TARGET_COMPANIES_PATH = BASE_DIR / "data" / "target_companies.json"

ATS_PATTERNS = [
    # Greenhouse
    (r"boards\.greenhouse\.io/(?:embed/job_board\?for=)?([^/?#]+)", "greenhouse"),
    (r"greenhouse\.io/([^/?#]+)/jobs/\d+", "greenhouse"),
    (r"grnh\.se/([^/?#]+)", "greenhouse"),
    # Lever
    (r"jobs\.lever\.co/([^/?#]+)", "lever"),
    # Ashby
    (r"jobs\.ashbyhq\.com/([^/?#]+)", "ashby"),
    # SmartRecruiters
    (r"smartrecruiters\.com/([^/?#]+)", "smartrecruiters"),
    (r"smrtr\.io/([^/?#]+)", "smartrecruiters"),
    # Workday
    (r"([^/?#]+)\.myworkdayjobs\.com", "workday"),
    (r"myworkdayjobs\.com/[^/?#]+/([^/?#]+)", "workday"),
    # iCIMS
    (r"([^/?#]+)-careers\.icims\.com", "icims"),
    (r"careers-([^/?#]+)\.icims\.com", "icims"),
]


def detect_ats_from_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Detects the ATS platform and company token from a job URL.
    Returns: (ats_type, company_slug) or (None, None).
    """
    if not url:
        return None, None

    for pattern, ats_type in ATS_PATTERNS:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            slug = match.group(1).lower().strip()
            return ats_type, slug

    return None, None


def unroll_url(
    url: str,
    session: Optional[requests.Session] = None,
    timeout: float = 6.0,
    max_redirects: int = 4,
) -> str:
    """
    Follows HTTP redirects (e.g. from bit.ly, t.co, or job board redirect links)
    to reveal the destination canonical URL without downloading full body.
    """
    if not url or not url.startswith("http"):
        return url

    ats, _ = detect_ats_from_url(url)
    if ats:
        return url

    s = session or requests.Session()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        )
    }

    current = url
    try:
        for _ in range(max_redirects):
            resp = s.head(current, headers=headers, allow_redirects=True, timeout=timeout)
            if resp.url and resp.url != current:
                current = resp.url
                ats, _ = detect_ats_from_url(current)
                if ats:
                    break
            else:
                break
    except Exception as exc:
        logger.debug("Failed unrolling URL '%s': %s", url, exc)

    return current


def register_discovered_company(
    company_name: str,
    slug: str,
    ats_type: str,
    config_path: Optional[Path] = None,
) -> bool:
    """
    Registers a new company and ATS token in data/target_companies.json
    so that background pollers will monitor its full board going forward.
    Returns True if a new entry was added, False if already exists.
    """
    path = config_path or TARGET_COMPANIES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    entries = []
    if path.exists():
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Error reading %s: %s", path, exc)
            entries = []

    norm_token = slug.lower().strip()
    norm_ats = ats_type.lower().strip()
    norm_name = company_name.strip()

    for item in entries:
        if item.get("token", "").lower() == norm_token and item.get("ats", "").lower() == norm_ats:
            return False

    entries.append({
        "company": norm_name or norm_token.capitalize(),
        "token": norm_token,
        "ats": norm_ats,
    })

    try:
        path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        logger.info("Dynamically registered new target company: %s (%s, %s)", norm_name, norm_token, norm_ats)
        return True
    except Exception as exc:
        logger.warning("Failed writing to %s: %s", path, exc)
        return False


def resolve_and_seed_job(
    company_name: str,
    raw_url: str,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    """
    Unrolls redirect links, identifies the destination ATS, and seeds the company.
    Returns a dict with:
        'canonical_url': unrolled destination URL
        'ats_type': detected ATS or 'unknown'
        'company_token': extracted slug or slugified company name
        'is_newly_seeded': True if added to target_companies.json
    """
    final_url = unroll_url(raw_url, session=session)
    ats_type, slug = detect_ats_from_url(final_url)

    seeded = False
    if ats_type and slug:
        token = slug
        seeded = register_discovered_company(company_name, slug, ats_type)
    else:
        ats_type = "external"
        token = re.sub(r"[^a-zA-Z0-9]+", "-", company_name.lower()).strip("-")

    return {
        "canonical_url": final_url,
        "ats_type": ats_type,
        "company_token": token,
        "is_newly_seeded": seeded,
    }
