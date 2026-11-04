"""Grow the owner's scan list from verified public boards (f317).

Board slugs are guessable but collisions are real (one Greenhouse slug turned
out to be an Italian retailer sharing the name), so candidates from
reference/candidate_tokens.json are NEVER scanned directly. This script probes
each token live: the board's API must answer with jobs AND the board's own
page must name the company. Verified boards are merged into
data/target_companies.json (the owner's list, reviewed as a normal diff).

Run once (or again for new candidates) on a machine with network:
    python scripts/verify_boards.py [--dry-run] [--limit N] [--delay SECONDS]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import diagnostics  # noqa: E402
import discovery_sources as ds  # noqa: E402

CANDIDATES = ROOT / "reference" / "candidate_tokens.json"
TARGETS = ROOT / "data" / "target_companies.json"

API_URL = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
    "lever": "https://api.lever.co/v0/postings/{token}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{token}",
}
PAGE_URL = {
    "greenhouse": "https://boards.greenhouse.io/{token}",
    "lever": "https://jobs.lever.co/{token}",
    "ashby": "https://jobs.ashbyhq.com/{token}",
}
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def page_names_company(html: str, company: str) -> bool:
    """True when the board page's title carries the company: every long word
    of the company name (or the token's words) appears in it."""
    match = _TITLE.search(html or "")
    if not match:
        return False
    title = _words(re.sub(r"<[^>]+>", " ", match.group(1)))
    wanted = _words(company)
    return bool(wanted) and wanted <= title


@diagnostics.probe
def board_has_jobs(ats: str, payload, token: str) -> bool:
    try:
        if ats == "greenhouse":
            return len(payload.get("jobs", [])) > 0
        if ats == "ashby":
            return len(payload.get("jobs", [])) > 0
        if ats == "lever":
            jobs = payload if isinstance(payload, list) else []
            return len(jobs) > 0
    except (AttributeError, TypeError):
        return False
    return False


def verify_candidate(candidate: dict, fetch) -> dict | None:
    """The verified entry ({company, ats, token}) for the first token whose
    API answers with jobs and whose page names the company, else None. `fetch`
    is requests.get (injected for tests)."""
    company, ats = candidate.get("company", ""), candidate.get("ats", "")
    if ats not in API_URL or not company:
        return None
    for token in candidate.get("tokens", []) or []:
        try:
            api = fetch(API_URL[ats].format(token=token), headers=ds.HEADERS, timeout=15)
            if api.status_code != 200 or not board_has_jobs(ats, api.json(), token):
                continue
            page = fetch(PAGE_URL[ats].format(token=token), headers=ds.HEADERS, timeout=15)
            if page.status_code == 200 and page_names_company(page.text, company):
                return {"company": company, "ats": ats, "token": token}
        except Exception as exc:
            print(f"  {company}: {token} failed ({type(exc).__name__})")
    return None


@diagnostics.probe
def merge_verified(path: Path, verified: list[dict]) -> list[dict]:
    """Adds verified boards to the owner's list, each employer once, sorted
    by company; returns the merged list."""
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
        current = current if isinstance(current, list) else []
    except (OSError, ValueError):
        current = []
    seen = {ds._identity(e) for e in current}
    for entry in verified:
        if ds._identity(entry) not in seen:
            seen.add(ds._identity(entry))
            current.append(entry)
    current.sort(key=lambda e: (e.get("company") or "").lower())
    return current


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verify candidate boards and grow the scan list.")
    parser.add_argument("--dry-run", action="store_true", help="report without writing data/target_companies.json")
    parser.add_argument("--limit", type=int, default=0, help="verify at most N candidates (0: all)")
    parser.add_argument("--delay", type=float, default=0.5, help="seconds between candidates (be polite)")
    args = parser.parse_args(argv)
    import requests
    candidates = json.loads(CANDIDATES.read_text(encoding="utf-8"))["candidates"]
    if args.limit:
        candidates = candidates[:args.limit]
    verified, rejected = [], []
    for candidate in candidates:
        print(f"{candidate['company']} ({candidate['ats']}): ", end="", flush=True)
        entry = verify_candidate(candidate, requests.get)
        print(entry["token"] if entry else "rejected")
        if entry:
            verified.append(entry)
        else:
            rejected.append(candidate["company"])
        time.sleep(args.delay)
    print(f"\nverified {len(verified)}, rejected {len(rejected)}")
    if args.dry_run:
        for entry in verified:
            print(f"  would add {entry['company']} ({entry['ats']}/{entry['token']})")
        return 0
    merged = merge_verified(TARGETS, verified)
    text = json.dumps(merged, indent=2) + "\n"
    crlf = TARGETS.is_file() and b"\r\n" in TARGETS.read_bytes()[:4096]
    TARGETS.write_bytes(text.replace("\n", "\r\n" if crlf else "\n").encode("utf-8"))
    print(f"wrote {len(merged)} employers to {TARGETS.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
