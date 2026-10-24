"""Avature ATS and Sunday referral wrapper adapter (sites/avature.py).

Implements dedicated portal behaviors for Avature (f161, f163, f165) and
referral wrappers (Sundayy):
1. Detects optional candidate login when direct 'From Device' / 'Without Resume'
   resume entry choices are present (f161).
2. Formats 10-digit plain phone numbers when XXXXXXXXXX placeholders are
   enforced (f165).
3. Detects explicit closed-posting notices to prevent header job-search autofill (f163).
4. Unwraps outbound ATS destinations from referral aggregator pages (Sundayy).
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from .base import SiteAdapter


class AvatureAdapter(SiteAdapter):
    """Adapter for Avature ATS portals (*.avature.net) and Sunday referral wrappers."""

    name = "avature"
    hosts = ("avature.net", "avature.cn", "avature.com")
    confirmation_phrases = (
        "thank you for applying",
        "your application has been received",
        "application submitted",
        "your application has been successfully submitted",
    )
    portal_list_patterns = (
        "/careers/ApplicationMethods",
        "/careers/JobDetail",
        "/careers/ProfileDetail",
    )

    _RESUME_ENTRY_CHOICES = frozenset({"from device", "copy & paste", "without resume"})
    _CLOSED_POSTING_PATTERNS = (
        re.compile(r"\bthe job position is no longer available\b", re.IGNORECASE),
        re.compile(r"\bthis job is no longer available\b", re.IGNORECASE),
        re.compile(r"\bthis position is no longer open\b", re.IGNORECASE),
    )
    _REFERRAL_HOSTS = ("sundayy.com", "sunday.com")

    @classmethod
    def matches(cls, url: str) -> bool:
        if not url:
            return False
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower()
            if any(host == h or host.endswith(f".{h}") for h in cls.hosts):
                return True
            if any(host == rh or host.endswith(f".{rh}") for rh in cls._REFERRAL_HOSTS):
                return True
            # Employer-hosted custom domain with Avature path conventions
            path = parsed.path.lower()
            if "/careers/applicationmethods" in path or "/careers/jobdetail" in path:
                return True
        except Exception:
            pass
        return False

    @classmethod
    def is_referral_wrapper(cls, url: str) -> bool:
        """True if the URL belongs to a referral aggregator like Sundayy."""
        if not url:
            return False
        try:
            host = (urlsplit(url).hostname or "").lower()
            return any(host == rh or host.endswith(f".{rh}") for rh in cls._REFERRAL_HOSTS)
        except Exception:
            return False

    @classmethod
    def unwrap_referral_destination(cls, page_or_html: str) -> Optional[str]:
        """Finds outbound ATS destination links on an aggregator referral page.
        
        Scans href targets for recognized ATS domains (avature, greenhouse, workday, lever, etc.).
        """
        if not page_or_html:
            return None
        # Match explicit apply links pointing to known ATS endpoints
        ats_pattern = re.compile(
            r'href=["\'](https?://[^"\']*(?:avature\.net|greenhouse\.io|myworkdayjobs\.com|lever\.co|smartrecruiters\.com|icims\.com)[^"\']*)["\']',
            re.IGNORECASE,
        )
        match = ats_pattern.search(page_or_html)
        if match:
            return match.group(1)
        return None

    @classmethod
    def has_resume_entry_alternatives(cls, snapshot: str) -> bool:
        """Determines whether login is optional due to explicit resume entry choices (f161).
        
        Avature pages offering 'Choose any option to apply' with 'From Device'
        allow bypassing existing user login.
        """
        if not snapshot:
            return False
        clean = snapshot.lower()
        if "choose any option to apply" not in clean:
            return False
        entry_controls = re.findall(r'- (?:button|link) "((?:[^"\\]|\\.)*)"', snapshot)
        offered = {" ".join(name.split()).lower() for name in entry_controls}
        return len(cls._RESUME_ENTRY_CHOICES & offered) >= 2

    @classmethod
    def is_closed_posting(cls, text: str) -> bool:
        """Identifies explicit closed-posting notifications before form interaction (f163).
        
        Distinguishes genuine closed requisition banners from FAQ or general search text.
        """
        if not text:
            return False
        for pattern in cls._CLOSED_POSTING_PATTERNS:
            if pattern.search(text):
                # Ensure it's not merely part of a FAQ or conditional explanation
                if not re.search(r"\b(?:faq|faqs|contact us if|questions?)\b", text, re.IGNORECASE):
                    return True
        return False

    @classmethod
    def format_phone_number(cls, raw_phone: str, placeholder: str, country: str) -> str:
        """Formats 10-digit plain phone numbers when XXXXXXXXXX is requested (f165).
        
        Preserves original formatting for international or non-conforming inputs.
        """
        if not raw_phone:
            return ""
        if not placeholder or "XXXXXXXXXX" not in placeholder:
            return raw_phone
        import geo_reference
        is_us = not country or geo_reference.country_code(country) == "US"
        if not is_us:
            return raw_phone
        digits = re.sub(r"\D", "", raw_phone)
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) == 10:
            return digits
        return raw_phone

