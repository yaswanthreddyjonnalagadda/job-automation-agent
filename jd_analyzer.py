"""
Job description ingestion and analysis.

Jobs are provided by the user (a URL they found while browsing LinkedIn/
Indeed/Dice themselves, or raw JD text pasted in) -- this module does not
scrape or crawl those sites. Deep analysis (extracted requirements, keywords,
seniority) is delegated to Claude via claude_integration.analyze_job.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from config import UserProfile

logger = logging.getLogger(__name__)

# Phrases that typically mean "we will not sponsor / you must already have
# work authorization" -- checked locally, for free, before any API call.
_CITIZENSHIP_CLEARANCE_PATTERNS = [
    r"U\.?S\.?\s+citizen(?:ship)?\s+(?:is\s+)?required",
    r"must\s+be\s+a\s+U\.?S\.?\s+citizen",
    r"green\s+card\s+holder",
    r"export\s+control",
    r"export\s+authorization",
    r"export\s+licens",
    r"\bITAR\b",
    r"\bEAR\b",
    r"security\s+clearance",
    r"unable\s+to\s+sponsor",
    r"no\s+sponsorship",
    r"not\s+able\s+to\s+sponsor",
    r"does\s+not\s+sponsor",
    r"will\s+not\s+sponsor",
    r"without\s+sponsorship",
]

# Lightweight local fallback keyword list so basic matching works without an
# API call. Claude does the real heavy lifting in claude_integration.py.
_COMMON_NETWORKING_SKILLS = [
    "BGP", "OSPF", "EIGRP", "MPLS", "SD-WAN", "VPN", "IPsec", "Cisco", "Juniper",
    "Arista", "Palo Alto", "Fortinet", "Python", "Ansible", "Terraform", "AWS",
    "Azure", "GCP", "VXLAN", "QoS", "Firewall", "Load Balancer", "DNS", "DHCP",
    "Network Automation", "CCNP", "CCIE", "Wireshark", "Kubernetes", "Docker",
]


@dataclass
class JobDescription:
    title: str
    company: str
    location: str
    source_site: str  # "linkedin" | "indeed" | "dice" | "other"
    url: str
    raw_text: str
    analysis: dict[str, Any] = field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        """Stable identity for a job, used to prevent duplicate applications."""
        return dedup_key_for_url(self.url) if self.url.strip() else hashlib.sha256(
            f"{self.company}|{self.title}".lower().encode("utf-8")
        ).hexdigest()


def dedup_key_for_url(url: str) -> str:
    """Same hashing scheme as JobDescription.dedup_key, usable before a full
    JobDescription exists -- lets us check for duplicates by URL alone,
    before asking the human to paste in JD text."""
    return hashlib.sha256(url.strip().lower().encode("utf-8")).hexdigest()


def guess_source_site(url: str) -> str:
    url_l = url.lower()
    if "linkedin.com" in url_l:
        return "linkedin"
    if "indeed.com" in url_l:
        return "indeed"
    if "dice.com" in url_l:
        return "dice"
    return "other"


def local_keyword_scan(text: str) -> list[str]:
    """Cheap, offline keyword scan -- used as a fallback / sanity check
    alongside Claude's analysis, not a replacement for it."""
    found = []
    for skill in _COMMON_NETWORKING_SKILLS:
        if re.search(rf"\b{re.escape(skill)}\b", text, flags=re.IGNORECASE):
            found.append(skill)
    return found


def local_eligibility_flags(raw_text: str) -> list[str]:
    """Free, offline scan for citizenship/clearance/no-sponsorship language.
    Run this before spending any Claude API call."""
    flags = []
    for pattern in _CITIZENSHIP_CLEARANCE_PATTERNS:
        match = re.search(pattern, raw_text, flags=re.IGNORECASE)
        if match:
            flags.append(f"Text suggests a citizenship/clearance/sponsorship restriction: \"{match.group(0)}\"")
    return flags


def eligibility_summary(job: "JobDescription", profile: "UserProfile") -> list[str]:
    """Combines local regex flags with Claude's structured analysis (if
    analyze_job() has already populated job.analysis) against the user's
    profile. Returns human-readable warnings, empty list if nothing stands out."""
    warnings = list(local_eligibility_flags(job.raw_text))

    if profile.requires_visa_sponsorship:
        if job.analysis.get("citizenship_or_clearance_required") is True:
            warnings.append("Claude's analysis: job requires citizenship/clearance status.")
        if job.analysis.get("visa_sponsorship_mentioned") is False:
            warnings.append("Claude's analysis: no visa sponsorship mentioned in the posting.")

    min_years = job.analysis.get("min_years_experience_required")
    if isinstance(min_years, (int, float)) and min_years > profile.years_experience:
        warnings.append(
            f"Job asks for {min_years}+ years of experience; your profile has {profile.years_experience}."
        )

    # De-duplicate while preserving order.
    seen = set()
    unique = []
    for w in warnings:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    return unique


def clean_jd_text(text: str) -> str:
    """If the user pastes raw HTML (e.g. copied via 'view source'), strip
    markup down to plain text. Safe no-op on already-plain text."""
    if "<" in text and ">" in text and re.search(r"</?[a-zA-Z]+[^>]*>", text):
        text = BeautifulSoup(text, "html.parser").get_text(separator="\n")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def build_job_description(
    *,
    title: str,
    company: str,
    location: str,
    url: str,
    raw_text: str,
) -> JobDescription:
    if not raw_text.strip():
        raise ValueError("Job description text is empty")

    jd = JobDescription(
        title=title.strip(),
        company=company.strip(),
        location=location.strip(),
        source_site=guess_source_site(url),
        url=url.strip(),
        raw_text=clean_jd_text(raw_text),
    )
    jd.analysis["local_keywords"] = local_keyword_scan(jd.raw_text)
    logger.info(
        "Built job description: %s @ %s (%s), %d local keywords matched",
        jd.title, jd.company, jd.source_site, len(jd.analysis["local_keywords"]),
    )
    return jd
