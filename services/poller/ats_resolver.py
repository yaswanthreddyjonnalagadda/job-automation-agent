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

AGGREGATOR_DOMAINS = (
    "linkedin.com",
    "indeed.com",
    "dice.com",
    "glassdoor.com",
    "ziprecruiter.com",
    "simplyhired.com",
)


def is_aggregator_url(url: str) -> bool:
    """Returns True if the URL points to a known aggregator or social job board."""
    if not url:
        return False
    host = (urlparse(url).netloc or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in AGGREGATOR_DOMAINS)


def extract_aggregator_posting_details(
    url: str,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    """
    Extracts requisition ID, organization, and embedded direct apply/career links
    from an aggregator job posting using Schema.org JSON-LD or DOM heuristics.
    """
    details: Dict[str, Any] = {
        "job_id": "",
        "organization": "",
        "direct_url": "",
        "description": "",
    }
    if not url:
        return details

    s = session or requests.Session()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        )
    }
    try:
        resp = s.get(url, headers=headers, timeout=8.0)
        if resp.status_code != 200 or not resp.text:
            return details

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if isinstance(item, dict) and item.get("@type") == "JobPosting":
                        ident = item.get("identifier")
                        if isinstance(ident, dict):
                            details["job_id"] = str(ident.get("value") or "").strip()
                        elif isinstance(ident, (str, int)):
                            details["job_id"] = str(ident).strip()

                        org = item.get("hiringOrganization")
                        if isinstance(org, dict):
                            details["organization"] = str(org.get("name") or "").strip()
                        elif isinstance(org, str):
                            details["organization"] = org.strip()

                        desc = str(item.get("description") or "")
                        details["description"] = desc
                        url_match = re.search(
                            r'https?://[^\s"\'<>]+(?:careers|jobs|apply|pageup|massachusetts)[^\s"\'<>]*',
                            desc,
                            re.I
                        )
                        if url_match:
                            details["direct_url"] = url_match.group(0).rstrip(".,;)")
            except Exception:
                continue
    except Exception as exc:
        logger.debug("Failed extracting aggregator details from %s: %s", url, exc)

    return details


def resolve_direct_career_url(
    company: str,
    title: str = "",
    raw_url: str = "",
    session: Optional[requests.Session] = None,
) -> Optional[str]:
    """
    Attempts to resolve an aggregator posting to a direct employer careers/ATS URL.
    """
    if not raw_url:
        return None

    details = extract_aggregator_posting_details(raw_url, session=session)
    if details.get("direct_url"):
        return details["direct_url"]

    job_id = details.get("job_id")
    org = details.get("organization") or company

    # Higher Education / PageUp ATS resolution for UMass / Massachusetts
    if job_id and ("umass" in org.lower() or "massachusetts" in org.lower()):
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.lower()).strip("-")
        candidate_url = (
            f"https://careers.massachusetts.edu/mob/en-us/job/{job_id}/{slug}"
            if slug else f"https://careers.massachusetts.edu/mob/en-us/job/{job_id}"
        )
        return candidate_url

    return None



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
    job_title: str = "",
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    """
    Unrolls redirect links, resolves aggregators to direct careers, identifies the destination ATS, and seeds the company.
    Returns a dict with:
        'canonical_url': unrolled/direct destination URL
        'ats_type': detected ATS or 'external'
        'company_token': extracted slug or slugified company name
        'is_newly_seeded': True if added to target_companies.json
        'is_direct_careers': True if pointing to an employer direct portal
    """
    final_url = unroll_url(raw_url, session=session)
    ats_type, slug = detect_ats_from_url(final_url)

    is_direct = bool(ats_type and slug)

    if not is_direct and is_aggregator_url(final_url):
        direct_url = resolve_direct_career_url(company_name, job_title, final_url, session=session)
        if direct_url:
            final_url = direct_url
            ats_type, slug = detect_ats_from_url(final_url)
            is_direct = True

    seeded = False
    if ats_type and slug:
        token = slug
        seeded = register_discovered_company(company_name, slug, ats_type)
    else:
        ats_type = "external" if not is_direct else "generic"
        token = re.sub(r"[^a-zA-Z0-9]+", "-", company_name.lower()).strip("-")

    return {
        "canonical_url": final_url,
        "ats_type": ats_type,
        "company_token": token,
        "is_newly_seeded": seeded,
        "is_direct_careers": is_direct,
    }
