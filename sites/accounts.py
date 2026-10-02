"""
Whose account a sign-in page is for: one employer's, or a whole portal's.

Most platforms give every employer its own accounts: a Waystar account on Workday does not sign in at Crescent
Energy, and an account is made once per employer. A few platforms keep one account for the candidate across every
employer that uses them, behind one shared login page: an account made for one Dayforce employer is the one the next
Dayforce employer asks for. There, creating an account first only meets "this email already has an account" and
spends one of the day's creations (Segra on Dayforce, 16 September: refused, with the account made months earlier).

The data, by the host of the shared login page (substring of the URL's host), with the portal's name. A host not
listed is one employer's.
"""

from __future__ import annotations

from urllib.parse import urlparse

SHARED_ACCOUNT_HOSTS: dict[str, str] = {
    "dayforcehcm.com": "Dayforce",        # dfid.dayforcehcm.com/globalidentity: one Dayforce ID for every employer
    "login.icims.com": "iCIMS",           # iCIMS's own candidate login, after the employer's email page
    "ultipro.com": "UKG",                 # signin-us.ultipro.com: one UKG candidate login
}


def shared_portal(url: str) -> str:
    """The portal whose one account this page signs in to ("Dayforce"), or "" for one employer's own account."""
    host = urlparse(url or "").netloc.lower()
    return next((name for part, name in SHARED_ACCOUNT_HOSTS.items() if host and part in host), "")


def record_key(employer: str, url: str) -> str:
    """The name an account is recorded under: the employer's, or for a shared login the portal's, so that every
    employer on that portal finds the one account."""
    portal = shared_portal(url)
    return f"{portal} (all employers)" if portal else (employer or "").strip()
