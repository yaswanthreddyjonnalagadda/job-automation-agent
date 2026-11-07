# Logic adapted from freehire (MIT), https://github.com/strelov1/freehire
"""Workday ATS CXS Public API Poller with facet-splitting and detail ingestion."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import html
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import requests

from services.poller.http import (
    BadJson,
    BoardNotFound,
    HttpError,
    HttpTimeout,
    get_json,
    make_session,
    post_json,
)
from services.poller.reader_result import ReaderResult

logger = logging.getLogger("poller.workday")

HOST_MYWORKDAYJOBS_RE = re.compile(r"^[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com$", re.I)
HOST_MYWORKDAYSITE_RE = re.compile(r"^wd\d+\.myworkdaysite\.com$", re.I)
LOCALE_RE = re.compile(r"^[a-z]{2}(?:-[a-z0-9]{2,4})?$", re.I)


class CappedBoardError(Exception):
    """Raised when a board with total 2000 cannot be split further or depth limit is reached."""


class PartialListingError(Exception):
    """Internal error carrying gathered postings and an error code on mid-pagination failure."""
    def __init__(self, postings: List[Dict[str, Any]], error: str):
        super().__init__(f"Partial listing ({len(postings)} items): {error}")
        self.postings = postings
        self.error = error


class FacetValue:
    def __init__(self, id: str, count: int, descriptor: str = ""):
        self.id = id
        self.count = count
        self.descriptor = descriptor


class FacetDimension:
    def __init__(self, parameter: str, values: List[FacetValue]):
        self.parameter = parameter
        self.values = values


def parse_workday_board(board: str) -> Tuple[str, str, str, str]:
    """Extract host, tenant, site, and public_prefix from a Workday board URL.

    Two shapes:
      acme.wd1.myworkdayjobs.com/Careers          tenant = first host label, public = /Careers
      wd1.myworkdaysite.com/recruiting/acme/Site  tenant = path part, public = /recruiting/acme/Site
    Drop a locale segment such as /en-US/ before the site.
    """
    b = (board or "").strip()
    if b.startswith("http://"):
        b = b[7:]
    elif b.startswith("https://"):
        b = b[8:]

    parts = b.split("/", 1)
    host = parts[0].strip().lower()
    path = "/" + parts[1].strip() if len(parts) > 1 else ""
    path_segments = [s for s in path.split("/") if s]

    if HOST_MYWORKDAYJOBS_RE.match(host):
        tenant = host.split(".")[0]
        if not path_segments:
            raise BoardNotFound(f"Invalid Workday board path: {board}")
        if len(path_segments) >= 2 and LOCALE_RE.match(path_segments[0]):
            site = path_segments[1]
        else:
            site = path_segments[0]
        public_prefix = f"/{site}"
        return host, tenant, site, public_prefix

    if HOST_MYWORKDAYSITE_RE.match(host):
        if len(path_segments) < 3 or path_segments[0].lower() != "recruiting":
            raise BoardNotFound(f"Invalid Workday site path: {board}")
        tenant = path_segments[1]
        remainder = path_segments[2:]
        if len(remainder) >= 2 and LOCALE_RE.match(remainder[0]):
            site = remainder[1]
        else:
            site = remainder[0]
        public_prefix = f"/recruiting/{tenant}/{site}"
        return host, tenant, site, public_prefix

    raise BoardNotFound(f"Invalid Workday host: {host}")


def parse_workday_url(board_url: str) -> Optional[Dict[str, str]]:
    """Backward-compatible helper returning host, tenant, site dictionary."""
    try:
        host, tenant, site, _ = parse_workday_board(board_url)
        return {"host": host, "tenant": tenant, "site": site}
    except Exception:
        return None


def fetch_page(
    session: requests.Session,
    url: str,
    facets: Dict[str, Any],
    offset: int,
) -> Dict[str, Any]:
    """Fetch a single page of job postings via POST."""
    body = {
        "appliedFacets": facets or {},
        "limit": 20,
        "offset": offset,
        "searchText": "",
    }
    return post_json(session, url, body=body)


def page_all(
    session: requests.Session,
    url: str,
    facets: Dict[str, Any],
    first_page: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Paginate all jobs from a board or slice using offset latching."""
    total = -1
    offset = 0
    page = first_page
    postings: List[Dict[str, Any]] = []

    while page and page.get("jobPostings"):
        items = page["jobPostings"]
        postings.extend(items)
        offset += len(items)

        if total < 0 and page.get("total", 0) > 0:
            total = page["total"]  # LATCH: some boards send real total on page 1 and 0 after

        if total >= 0 and offset >= total:
            break

        try:
            page = fetch_page(session, url, facets, offset)
        except BoardNotFound:
            if postings:
                raise PartialListingError(postings, "board_not_found")
            raise
        except HttpError as exc:
            if postings:
                raise PartialListingError(postings, f"http_{exc.status}")
            raise
        except (requests.exceptions.Timeout, HttpTimeout):
            if postings:
                raise PartialListingError(postings, "timeout")
            raise
        except BadJson:
            if postings:
                raise PartialListingError(postings, "bad_json")
            raise

    return postings


def splittable_facets(facets: List[Dict[str, Any]]) -> List[FacetDimension]:
    """Extract splittable facet dimensions, unwrapping nested groups."""
    dims: List[FacetDimension] = []
    if not isinstance(facets, list):
        return dims

    for facet in facets:
        if not isinstance(facet, dict):
            continue
        param = facet.get("facetParameter")
        raw_values = facet.get("values", [])
        if not param or not isinstance(raw_values, list):
            continue

        valid_values: List[FacetValue] = []
        for v in raw_values:
            if not isinstance(v, dict):
                continue
            if v.get("facetParameter") and isinstance(v.get("values"), list):
                dims.extend(splittable_facets([v]))
            val_id = v.get("id")
            if val_id is not None and val_id != "":
                cnt = v.get("count", 0)
                try:
                    cnt = int(cnt)
                except (ValueError, TypeError):
                    cnt = 0
                desc = v.get("descriptor", "")
                valid_values.append(FacetValue(id=str(val_id), count=cnt, descriptor=str(desc)))

        if valid_values:
            dims.append(FacetDimension(parameter=str(param), values=valid_values))

    return dims


def pick_split_dimension(
    facets: List[Dict[str, Any]],
    used: Set[str],
) -> Optional[FacetDimension]:
    """Pick facet dimension whose sum >= 2000 and whose largest value is smallest."""
    dims = splittable_facets(facets)
    candidates: List[Tuple[int, FacetDimension]] = []

    for dim in dims:
        if dim.parameter in used:
            continue
        if not dim.values:
            continue
        total_count = sum(v.count for v in dim.values)
        if total_count < 2000:
            continue
        max_val = max(v.count for v in dim.values)
        candidates.append((max_val, dim))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def split_by_facet(
    session: requests.Session,
    url: str,
    facets: Dict[str, Any],
    capped_page: Dict[str, Any],
    used: Set[str],
    depth: int = 0,
) -> List[Dict[str, Any]]:
    """Recursively slice capped boards by facet to bypass the 2000-job cutoff."""
    dim = pick_split_dimension(capped_page.get("facets", []), used)
    if dim is None or depth >= 3:
        raise CappedBoardError("capped_unsplittable")

    by_path: Dict[str, Dict[str, Any]] = {}
    for value in dim.values:
        f = dict(facets)
        f[dim.parameter] = [value.id]
        page = fetch_page(session, url, f, 0)
        if page.get("total") == 2000:
            part = split_by_facet(session, url, f, page, used | {dim.parameter}, depth + 1)
        else:
            part = page_all(session, url, f, page)

        for p in part:
            ext_path = p.get("externalPath", "")
            if ext_path:
                by_path[ext_path] = p

    return list(by_path.values())


def list_postings(session: requests.Session, url: str) -> List[Dict[str, Any]]:
    """List all raw postings from board URL, splitting if capped at 2000."""
    first = fetch_page(session, url, {}, 0)
    if first.get("total") == 2000:
        return split_by_facet(session, url, {}, first, used=set(), depth=0)
    return page_all(session, url, {}, first)


def html_to_plain_text(html_text: str) -> str:
    """Convert HTML job description into clean plain text."""
    if not html_text:
        return ""
    text = re.sub(r"<(?:br|p|div|li)[^>]*>", "\n", html_text, flags=re.I)
    text = re.sub(r"</(?:p|div|li)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(l for l in lines if l)


def make_listing_posting(
    host: str,
    public_prefix: str,
    item: Dict[str, Any],
) -> Dict[str, Any]:
    """Create standardized posting dict from listing data only."""
    ext_path = item.get("externalPath", "")
    title = item.get("title", "")
    location = item.get("locationsText", "")
    posted_at = item.get("postedOn", "")
    apply_url = f"https://{host}{public_prefix}{ext_path}" if ext_path else ""
    is_remote = bool(re.search(r"\b(remote|anywhere|virtual)\b", f"{title} {location}", re.I))

    return {
        "external_id": ext_path,
        "title": title,
        "location": location,
        "department": "",
        "description": "",
        "description_html": "",
        "apply_url": apply_url,
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "USD",
        "employment_type": "full-time",
        "remote": bool(is_remote),
        "posted_at": posted_at,
    }


def fetch_detail(
    session: requests.Session,
    host: str,
    tenant: str,
    site: str,
    public_prefix: str,
    item: Dict[str, Any],
) -> Dict[str, Any]:
    """Fetch detail GET for a posting, falling back to listing attributes."""
    ext_path = item.get("externalPath", "")
    detail_url = f"https://{host}/wday/cxs/{tenant}/{site}{ext_path}"

    title = item.get("title", "")
    location = item.get("locationsText", "")
    posted_at = item.get("postedOn", "")
    apply_url = f"https://{host}{public_prefix}{ext_path}" if ext_path else ""
    is_remote = bool(re.search(r"\b(remote|anywhere|virtual)\b", f"{title} {location}", re.I))
    description = ""
    description_html = ""

    try:
        data = get_json(session, detail_url)
        info = data.get("jobPostingInfo", {}) if isinstance(data, dict) else {}
        if info:
            title = info.get("title") or title
            location = info.get("location") or location
            apply_url = info.get("externalUrl") or apply_url
            description_html = info.get("jobDescription") or ""
            description = html_to_plain_text(description_html)
            posted_at = info.get("startDate") or posted_at
            remote_type = info.get("remoteType", "")
            if remote_type and re.search(r"\bremote\b", str(remote_type), re.I):
                is_remote = True
            elif not remote_type:
                is_remote = bool(re.search(r"\b(remote|anywhere|virtual)\b", f"{title} {location}", re.I))
    except Exception as exc:
        logger.debug("Workday detail fetch failed for %s: %s", detail_url, exc)
        description = ""

    return {
        "external_id": ext_path,
        "title": title,
        "location": location,
        "department": "",
        "description": description,
        "description_html": description_html,
        "apply_url": apply_url,
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "USD",
        "employment_type": "full-time",
        "remote": bool(is_remote),
        "posted_at": posted_at,
    }


def parse_workday_response(
    data: Any,
    host: str = "",
    site: str = "",
    board_url: str = "",
) -> ReaderResult:
    """Parse raw CXS response page for backward-compatibility with existing tests."""
    if not isinstance(data, dict) or "jobPostings" not in data:
        return ReaderResult(postings=[], complete=False, error="bad_json")

    postings: List[Dict[str, Any]] = []
    items = data.get("jobPostings", [])
    if isinstance(items, list):
        for raw in items:
            if isinstance(raw, dict):
                ext_path = raw.get("externalPath", "")
                postings.append({
                    "external_id": ext_path,
                    "title": raw.get("title", ""),
                    "location": raw.get("locationsText", ""),
                    "department": "",
                    "description": raw.get("title", ""),
                    "description_html": "",
                    "apply_url": f"https://{host}/{site}{ext_path}" if host and site and ext_path else board_url,
                    "salary_min": None,
                    "salary_max": None,
                    "salary_currency": "USD",
                    "employment_type": "full-time",
                    "remote": bool(re.search(r"\b(remote|anywhere|virtual)\b", f"{raw.get('title','')} {raw.get('locationsText','')}", re.I)),
                    "posted_at": raw.get("postedOn", ""),
                })

    total = data.get("total", len(postings))
    complete = len(postings) >= total
    return ReaderResult(postings=postings, complete=complete, error=None)


def read_workday_board(
    board: str,
    session: Optional[requests.Session] = None,
    known_ids: Optional[Callable[[str], bool]] = None,
    **kwargs: Any,
) -> ReaderResult:
    """Read Workday CXS board with facet-splitting and detail ingestion."""
    try:
        host, tenant, site, public_prefix = parse_workday_board(board)
    except BoardNotFound:
        return ReaderResult(postings=[], complete=False, error="board_not_found")
    except Exception as exc:
        return ReaderResult(postings=[], complete=False, error=str(exc))

    list_url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    s = session or make_session("workday")

    raw_items: List[Dict[str, Any]] = []
    listing_error: Optional[str] = None

    try:
        raw_items = list_postings(s, list_url)
    except PartialListingError as ple:
        raw_items = ple.postings
        listing_error = ple.error
    except CappedBoardError:
        return ReaderResult(postings=[], complete=False, error="capped_unsplittable")
    except BoardNotFound:
        return ReaderResult(postings=[], complete=False, error="board_not_found")
    except HttpError as exc:
        return ReaderResult(postings=[], complete=False, error=f"http_{exc.status}")
    except (requests.exceptions.Timeout, HttpTimeout):
        return ReaderResult(postings=[], complete=False, error="timeout")
    except BadJson:
        return ReaderResult(postings=[], complete=False, error="bad_json")
    except Exception as exc:
        return ReaderResult(postings=[], complete=False, error=str(exc))

    # Detail fetching (4 at a time via ThreadPoolExecutor)
    items_to_fetch: List[Tuple[int, Dict[str, Any]]] = []
    results: List[Optional[Dict[str, Any]]] = [None] * len(raw_items)

    for idx, item in enumerate(raw_items):
        ext_path = item.get("externalPath", "")
        if known_ids and known_ids(ext_path):
            results[idx] = make_listing_posting(host, public_prefix, item)
        else:
            items_to_fetch.append((idx, item))

    if items_to_fetch:
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_idx = {
                executor.submit(fetch_detail, s, host, tenant, site, public_prefix, item): idx
                for idx, item in items_to_fetch
            }
            for future in future_to_idx:
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                except Exception as exc:
                    logger.debug("Detail future execution failed: %s", exc)
                    item = raw_items[idx]
                    results[idx] = make_listing_posting(host, public_prefix, item)

    postings = [r for r in results if r is not None]
    is_complete = (listing_error is None)
    return ReaderResult(postings=postings, complete=is_complete, error=listing_error)


def poll_workday(
    board: str,
    session: Optional[requests.Session] = None,
    known_ids: Optional[Callable[[str], bool]] = None,
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    """Backward-compatible helper returning list of job dicts."""
    return read_workday_board(board, session=session, known_ids=known_ids, **kwargs).postings
