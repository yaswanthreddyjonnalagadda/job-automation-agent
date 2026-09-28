"""
Job description ingestion and analysis.

Jobs are provided by the user (a URL they found while browsing LinkedIn/
Indeed/Dice themselves, or raw JD text pasted in) -- this module does not
scrape or crawl those sites. Analysis here is local and free (keyword matching).
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
