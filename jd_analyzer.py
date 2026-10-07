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
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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


# Known non-identity URL variation: ad/click/campaign trackers that vary between two copies of
# the exact same posting link and never distinguish one posting or application from another.
# Consolidated in this one place -- jd_analyzer.submission_identity_url, job_tracker._strip_tracking,
# and db._strip_tracking used to each keep their own copy of a narrower list, which is exactly how a
# platform-specific tracker (LinkedIn's `trk`, Google's/Meta's/Microsoft's/Twitter's `*clid` family)
# went unstripped in all three places at once (the owner's finding of 7 October 2026).
_TRACKING_QUERY_PREFIXES = ("utm_", "gh_src")
_TRACKING_QUERY_SUFFIXES = ("clid",)   # gclid, fbclid, msclkid, twclid, dclid, yclid, ttclid, ...
_TRACKING_QUERY_NAMES = {
    "src", "source", "ref", "referrer", "trackingid",
    "trk", "li_fat_id", "mc_cid", "mc_eid", "rb_clickid", "shared_id",
    "msclkid",   # Microsoft's click id is spelled "clkid", not "clid" -- the suffix rule misses it
}


def is_tracking_query_param(key: str) -> bool:
    """Whether a URL query parameter is known ad/click/campaign tracking noise, never a job or
    application identifier, so it is safe to ignore for submission-identity and deduplication
    purposes.

    This is deliberately a narrow, explicit exclusion list, not a guess: an unrecognized parameter
    is always kept, because a parameter this does not recognize may be exactly how one posting or
    application is told apart from another (a requisition id, a job id, a step token). Only a
    parameter known by name, or matching the `*clid` ad-click-id family, is ignored."""
    key = (key or "").lower()
    return key.startswith(_TRACKING_QUERY_PREFIXES) or key.endswith(_TRACKING_QUERY_SUFFIXES) \
        or key in _TRACKING_QUERY_NAMES


def submission_identity_url(url: str) -> str:
    """Canonicalize only known non-identity URL variation for effect replay protection."""
    value = (url or "").strip().lower()
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("A valid application URL is required for submission identity") from exc
    if parsed.scheme not in {"http", "https"} or not hostname or parsed.username or parsed.password:
        raise ValueError("A valid application URL is required for submission identity")

    retained_query = sorted(
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not is_tracking_query_param(key)
    )
    path = parsed.path.rstrip("/")
    if not path and not retained_query:
        raise ValueError("Application URL does not identify a specific posting")

    netloc = hostname
    if ":" in hostname and not hostname.startswith("["):
        netloc = f"[{hostname}]"
    if port is not None and not (
        parsed.scheme == "https" and port == 443
        or parsed.scheme == "http" and port == 80
    ):
        netloc = f"{netloc}:{port}"
    scheme = "https" if parsed.scheme in {"http", "https"} else parsed.scheme
    return urlunsplit((scheme, netloc, path, urlencode(retained_query), ""))


def submission_effect_key_for_url(url: str) -> str:
    """Stable local replay identity, separate from the legacy application-row key."""
    identity = submission_identity_url(url)
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


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
