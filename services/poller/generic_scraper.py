"""Universal HTML & Schema.org Career Page Scraper using requests & BeautifulSoup."""
from __future__ import annotations

import html
import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import requests
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

logger = logging.getLogger("poller.generic_scraper")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}


def build_resilient_session(retries: int = 3, backoff_factor: float = 0.5) -> requests.Session:
    """Build a requests session equipped with standard headers and exponential backoff retry logic."""
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    retry_strategy = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def clean_text(raw_html: str) -> str:
    """Strip HTML tags and normalize whitespace."""
    if not raw_html:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_schema_job_posting(item: Dict[str, Any], base_url: str) -> Optional[Dict[str, Any]]:
    """Convert a schema.org/JobPosting JSON-LD object to the canonical job shape."""
    if not isinstance(item, dict):
        return None
    raw_type = item.get("@type", "")
    types = [raw_type] if isinstance(raw_type, str) else list(raw_type)
    if not any("JobPosting" in str(t) for t in types):
        return None

    title = str(item.get("title") or item.get("name") or "").strip()
    if not title:
        return None

    external_id = str(item.get("identifier", {}).get("value") or item.get("identifier") or "")
    if not external_id:
        external_id = str(item.get("@id") or item.get("url") or title)

    # Location resolution from jobLocation object
    location_parts = []
    job_loc = item.get("jobLocation")
    is_remote = False
    if isinstance(job_loc, dict):
        addr = job_loc.get("address", {})
        if isinstance(addr, dict):
            for part in [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")]:
                if part and str(part).strip():
                    location_parts.append(str(part).strip())
        elif isinstance(addr, str) and addr.strip():
            location_parts.append(addr.strip())
    elif isinstance(job_loc, list):
        for loc in job_loc:
            if isinstance(loc, dict) and "address" in loc:
                addr = loc["address"]
                if isinstance(addr, dict) and addr.get("addressLocality"):
                    location_parts.append(str(addr["addressLocality"]).strip())
    elif isinstance(job_loc, str) and job_loc.strip():
        location_parts.append(job_loc.strip())

    # Check for telecommute / remote indicators in schema
    job_location_type = str(item.get("jobLocationType") or "").lower()
    if "telecommute" in job_location_type or "remote" in job_location_type:
        is_remote = True

    location = ", ".join(location_parts) if location_parts else "Not Specified"
    if not is_remote and re.search(r"\b(remote|anywhere|virtual|work from home)\b", f"{title} {location}", re.I):
        is_remote = True

    # Salary parsing
    salary_min = None
    salary_max = None
    salary_currency = "USD"
    base_sal = item.get("baseSalary")
    if isinstance(base_sal, dict):
        val = base_sal.get("value", {})
        salary_currency = str(base_sal.get("currency") or "USD").upper()
        if isinstance(val, dict):
            salary_min = val.get("minValue") or val.get("value")
            salary_max = val.get("maxValue") or val.get("value")
        elif isinstance(val, (int, float)):
            salary_min = int(val)

    # Apply URL
    apply_url = str(item.get("url") or base_url)
    if apply_url and not apply_url.startswith("http"):
        apply_url = urljoin(base_url, apply_url)

    desc_html = str(item.get("description") or "")
    plain_desc = clean_text(desc_html)

    dept = ""
    occ = item.get("occupationalCategory")
    if isinstance(occ, str):
        dept = occ
    elif isinstance(occ, list):
        dept = ", ".join(str(o) for o in occ if o)

    return {
        "external_id": external_id,
        "title": title,
        "location": location,
        "department": dept,
        "description": plain_desc,
        "description_html": desc_html,
        "apply_url": apply_url,
        "salary_min": int(salary_min) if salary_min is not None and str(salary_min).isdigit() else None,
        "salary_max": int(salary_max) if salary_max is not None and str(salary_max).isdigit() else None,
        "salary_currency": salary_currency,
        "employment_type": str(item.get("employmentType") or "full-time").lower(),
        "remote": is_remote,
        "posted_at": item.get("datePosted"),
    }


def extract_schema_org_jobs(soup: BeautifulSoup, base_url: str) -> List[Dict[str, Any]]:
    """Scan all script[type="application/ld+json"] tags for schema.org/JobPosting entries."""
    jobs: List[Dict[str, Any]] = []
    scripts = soup.find_all("script", type=re.compile(r"application/ld\+json", re.I))

    for script in scripts:
        raw_text = script.string or script.text or ""
        if not raw_text.strip():
            continue
        try:
            data = json.loads(raw_text)
        except Exception:
            continue

        items_to_check = []
        if isinstance(data, dict):
            if "@graph" in data and isinstance(data["@graph"], list):
                items_to_check.extend(data["@graph"])
            elif data.get("@type") == "ItemList" and "itemListElement" in data:
                for el in data["itemListElement"]:
                    if isinstance(el, dict):
                        items_to_check.append(el.get("item", el))
            else:
                items_to_check.append(data)
        elif isinstance(data, list):
            items_to_check.extend(data)

        for item in items_to_check:
            parsed = parse_schema_job_posting(item, base_url)
            if parsed:
                jobs.append(parsed)

    return jobs


def extract_dom_heuristic_jobs(soup: BeautifulSoup, base_url: str) -> List[Dict[str, Any]]:
    """Heuristic fallback: search for career links and job cards in the HTML DOM."""
    jobs: List[Dict[str, Any]] = []
    seen_urls = set()

    # Match links pointing to specific job/career details
    job_link_pattern = re.compile(
        r"/(?:job|jobs|career|careers|position|positions|posting|postings|role|roles|apply|opening|openings)/[a-zA-Z0-9_\-\.\/]+",
        re.I,
    )

    # 1. Search semantic cards or list items
    card_selectors = [
        "[data-job-id]",
        "[class*='job-item']",
        "[class*='job-card']",
        "[class*='job-listing']",
        "[class*='career-item']",
        "[class*='posting-card']",
        "li[class*='job']",
        "article",
    ]

    for sel in card_selectors:
        cards = soup.select(sel)
        for card in cards:
            link = card.find("a", href=job_link_pattern) or card.find("a", href=True)
            if not link:
                continue
            href = urljoin(base_url, link.get("href", ""))
            if href in seen_urls or not href.startswith("http"):
                continue

            # Extract title from heading or link text
            title_tag = card.find(["h2", "h3", "h4", "h5", "strong"]) or link
            title = clean_text(title_tag.text)
            if not title or len(title) < 3 or len(title) > 140:
                continue
            if re.search(r"^(read more|view job|apply now|details|learn more|click here)$", title, re.I):
                continue

            # Look for location text in card
            loc_tag = card.find(attrs={"class": re.compile(r"location|city|place|dept", re.I)})
            loc_text = clean_text(loc_tag.text) if loc_tag else ""
            if not loc_text:
                full_card_text = clean_text(card.text)
                loc_match = re.search(r"\b([A-Z][a-zA-Z\s]+,\s*(?:[A-Z]{2}|[A-Za-z]+)|Remote)\b", full_card_text)
                loc_text = loc_match.group(1) if loc_match else "Not Specified"

            is_remote = bool(re.search(r"\b(remote|anywhere|virtual)\b", f"{title} {loc_text}", re.I))
            external_id = card.get("data-job-id") or re.sub(r"[^\w]", "_", urlparse(href).path)

            seen_urls.add(href)
            jobs.append({
                "external_id": str(external_id),
                "title": title,
                "location": loc_text,
                "department": "",
                "description": "",
                "description_html": "",
                "apply_url": href,
                "salary_min": None,
                "salary_max": None,
                "salary_currency": "USD",
                "employment_type": "full-time",
                "remote": is_remote,
                "posted_at": None,
            })

    # 2. If no cards were found, scan all anchor tags directly
    if not jobs:
        for a in soup.find_all("a", href=job_link_pattern):
            href = urljoin(base_url, a.get("href", ""))
            if href in seen_urls or not href.startswith("http"):
                continue
            text = clean_text(a.text)
            if 3 <= len(text) <= 120 and not re.search(r"^(apply|view|learn more|careers|jobs)$", text, re.I):
                seen_urls.add(href)
                jobs.append({
                    "external_id": re.sub(r"[^\w]", "_", urlparse(href).path),
                    "title": text,
                    "location": "Not Specified",
                    "department": "",
                    "description": "",
                    "description_html": "",
                    "apply_url": href,
                    "salary_min": None,
                    "salary_max": None,
                    "salary_currency": "USD",
                    "employment_type": "full-time",
                    "remote": bool(re.search(r"\bremote\b", text, re.I)),
                    "posted_at": None,
                })

    return jobs


def scrape_career_page(url: str, session: Optional[requests.Session] = None) -> List[Dict[str, Any]]:
    """Scrapes a public career page using schema.org extraction first, then DOM heuristics."""
    s = session or build_resilient_session()
    try:
        resp = s.get(url, timeout=20)
        resp.raise_for_status()
        html_content = resp.text
    except Exception as exc:
        logger.error("Failed to fetch careers page %s: %s", url, exc)
        return []

    soup = BeautifulSoup(html_content, "html.parser")

    # Priority 1: Schema.org structured data (JSON-LD)
    schema_jobs = extract_schema_org_jobs(soup, url)
    if schema_jobs:
        logger.info("Found %d jobs via schema.org JSON-LD at %s", len(schema_jobs), url)
        return schema_jobs

    # Priority 2: Heuristic DOM parsing
    dom_jobs = extract_dom_heuristic_jobs(soup, url)
    logger.info("Found %d jobs via DOM heuristics at %s", len(dom_jobs), url)
    return dom_jobs

