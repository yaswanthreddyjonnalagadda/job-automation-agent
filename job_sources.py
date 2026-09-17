"""
Reading a job posting from its link.

One function per platform, each using that platform's own data where it is
available (Workday, Greenhouse, Lever, Ashby, Eightfold, schema.org markup) and
falling back to plain page text. This is the "where does a job come from" half
of the assistant; the browser half lives in browser_automation.py + sites/.

Job boards and third-party auto-apply aggregators are refused on purpose: the
user's rule is to apply on the employer's own page.
"""

from __future__ import annotations

import html
import json
import logging
import re
from urllib.parse import parse_qs, urlparse

import requests

logger = logging.getLogger("job_sources")


BLOCKED_SOURCES = ("linkedin.com", "indeed.com", "dice.com")

# Aggregator "apply with AI" sites and staffing/recruiting agencies are both
# excluded by the user's own sourcing rule -- apply on the EMPLOYER's own
# page, not through a middleman. A staffing agency listing (randstadusa.com
# and similar) often doesn't even name the actual employer in the posting.
AGGREGATORS = ("remotehunter.com", "jobright.ai", "simplify.jobs", "randstadusa.com")


def fetch_workday_job(url: str) -> dict | None:
    """Pulls a Workday posting through its CXS JSON API.

    Workday renders postings client-side, so scraping the HTML yields an empty
    shell. The CXS endpoint behind the page returns the real title, location
    and description, which is both more reliable and lighter than a browser.
    """
    parsed = urlparse(url)
    if "myworkdayjobs.com" not in parsed.netloc:
        return None

    tenant = parsed.netloc.split(".")[0]
    parts = [p for p in parsed.path.split("/") if p]
    try:
        job_index = parts.index("job")
    except ValueError:
        return None

    # /<locale?>/<site>/job/<location>/<jobId>
    site = parts[job_index - 1]
    tail = parts[job_index + 1:]
    if not tail:
        return None

    api = f"https://{parsed.netloc}/wday/cxs/{tenant}/{site}/job/{'/'.join(tail)}"
    try:
        resp = requests.get(api, headers={"Accept": "application/json"}, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        info = payload.get("jobPostingInfo", {})
    except Exception as exc:
        logger.warning("Workday API fetch failed (%s); falling back.", exc)
        return None

    if not info.get("title"):
        return None

    # The real employer name, not the URL slug -- this ends up in the cover
    # letter, and the tenant slug gives you 'Bbinsurance'. Workday prefixes
    # some tenants with an internal number ('001 Brown & Brown, Inc').
    company = (payload.get("hiringOrganization") or {}).get("name", "").strip()
    company = re.sub(r"^\d+\s+", "", company)
    if not company:
        company = tenant.replace("-", " ").title()

    return {
        "title": info["title"],
        "company": company,
        "location": info.get("location", ""),
        "url": url,
        "raw_text": info.get("jobDescription", ""),
    }


def fetch_greenhouse_job(url: str) -> dict | None:
    """Pulls a Greenhouse-backed posting through the public boards API.

    Employers embed Greenhouse in their own careers page and pass the job in
    a `gh_jid` query parameter, so the visible HTML is the company's site
    chrome with the job rendered client-side -- scraping it returns menus and
    a title of 'job'. The board token is usually the company's own domain
    label, which is how it's guessed here before falling back.
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    job_id = (params.get("gh_jid") or [""])[0]
    if not job_id:
        if "greenhouse.io" not in parsed.netloc:
            return None
        m = re.search(r"/jobs/(\d+)", parsed.path)
        if not m:
            return None
        job_id = m.group(1)

    host_label = parsed.netloc.split(".")[-2] if parsed.netloc.count(".") >= 1 else parsed.netloc
    candidates = [host_label, host_label.replace("-", "")]
    if "greenhouse.io" in parsed.netloc:
        parts = [p for p in parsed.path.split("/") if p]
        if parts:
            candidates.insert(0, parts[0])

    for token in dict.fromkeys(candidates):
        api = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}?content=true"
        try:
            resp = requests.get(api, timeout=30)
            if resp.status_code != 200:
                continue
            data = resp.json()
        except Exception:
            continue
        if not data.get("title"):
            continue

        # Greenhouse returns the description as HTML-escaped HTML.
        content = html.unescape(data.get("content", ""))
        text = re.sub(r"<[^>]+>", " ", content)
        text = re.sub(r"\s+", " ", text).strip()

        return {
            "title": data["title"].strip(),
            "company": (data.get("company_name") or token).strip(),
            "location": (data.get("location") or {}).get("name", ""),
            "url": data.get("absolute_url") or url,
            "raw_text": text,
        }
    return None


def fetch_lever_job(url: str) -> dict | None:
    """Pulls a Lever-hosted posting through Lever's public postings API.

    jobs.lever.co pages need JavaScript to render, so scraping the raw HTML
    yields mostly nav chrome and titles the posting's ATS ('Jobs' from the
    'jobs.lever.co' hostname). The public API at api.lever.co returns the
    same content the page renders, structured.
    """
    parsed = urlparse(url)
    if "lever.co" not in parsed.netloc:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    tenant, posting_id = parts[0], parts[1]

    api = f"https://api.lever.co/v0/postings/{tenant}/{posting_id}?mode=json"
    try:
        resp = requests.get(api, timeout=30)
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception as exc:
        logger.warning("Lever API fetch failed (%s); falling back.", exc)
        return None

    title = (data.get("text") or "").strip()
    if not title:
        return None

    categories = data.get("categories") or {}
    location = categories.get("location", "")

    # Lever's API has no explicit company-name field; the tenant slug in the
    # URL is the company's own identifier there.
    company = tenant.replace("-", " ").title()

    # 'opening' is the intro/about-us; 'additional' has responsibilities,
    # qualifications, and benefits. Both are needed for a complete JD.
    body = "\n\n".join(
        part for part in (data.get("descriptionPlain"), data.get("additionalPlain")) if part
    )

    return {
        "title": title,
        "company": company,
        "location": location,
        "url": data.get("hostedUrl") or url,
        "raw_text": body,
    }


def fetch_ashby_job(url: str) -> dict | None:
    """Pulls an Ashby-hosted posting through Ashby's public job-board API.

    jobs.ashbyhq.com pages are rendered entirely client-side, so the raw HTML
    has almost no text and the generic fetch gives up on them. The public
    posting API returns the tenant's whole board; the posting is picked out
    by its id from the URL.
    """
    parsed = urlparse(url)
    if "ashbyhq.com" not in parsed.netloc:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    tenant, posting_id = parts[0], parts[1]

    api = f"https://api.ashbyhq.com/posting-api/job-board/{tenant}?includeCompensation=true"
    try:
        resp = requests.get(api, timeout=30)
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception as exc:
        logger.warning("Ashby API fetch failed (%s); falling back.", exc)
        return None

    posting = next((j for j in data.get("jobs", []) if j.get("id") == posting_id), None)
    if not posting or not (posting.get("title") or "").strip():
        return None

    location = posting.get("location") or ""
    if posting.get("isRemote") and "remote" not in location.lower():
        location = f"{location} (Remote)" if location else "Remote"

    # Like Lever, Ashby's API has no company-name field; the tenant slug in
    # the URL is the company's own identifier.
    company = tenant.replace("-", " ").title()

    body = posting.get("descriptionPlain") or ""
    pay = ((posting.get("compensation") or {}).get("scrapeableCompensationSalarySummary") or "").strip()
    if pay:
        body += f"\n\nCompensation: {pay}"

    return {
        "title": posting["title"].strip(),
        "company": company,
        "location": location,
        "url": posting.get("jobUrl") or url,
        "raw_text": body,
    }


def fetch_eightfold_job(url: str) -> dict | None:
    """Pulls a posting from an Eightfold-powered careers site (jobs.cbts.com
    and many others, often on the employer's own domain).

    Their pages are a JavaScript app, so the generic fetch read the theme JSON
    instead of the job -- it recorded 'Careers at CBTS' at 'Jobs' and tailored
    a resume against page config. The site's own position API has the real
    title and description. Recognised by URL shape: /careers/job/<id> or
    ?pid=<id>."""
    parsed = urlparse(url)
    m = re.search(r"/careers/job/(\d+)", parsed.path)
    position_id = m.group(1) if m else (parse_qs(parsed.query).get("pid") or [""])[0]
    if not position_id.isdigit():
        return None
    host = parsed.netloc.lower()
    domain = (parse_qs(parsed.query).get("domain") or [""])[0] or re.sub(r"^(jobs|careers|apply)\.", "", host)

    api = f"https://{host}/api/pcsx/position_details"
    try:
        resp = requests.get(
            api, params={"position_id": position_id, "domain": domain, "hl": "en"}, timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (compatible; job-application-assistant)"},
        )
        if resp.status_code != 200:
            return None
        data = (resp.json() or {}).get("data") or {}
    except Exception as exc:
        logger.warning("Eightfold API fetch failed (%s); falling back.", exc)
        return None

    title = (data.get("name") or "").strip()
    description_html = data.get("jobDescription") or ""
    if not title or not description_html:
        return None

    text = re.sub(r"<br\s*/?>|</(p|div|li|h\d)>", "\n", description_html, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()

    # No company-name field; the site's domain names the employer. Short
    # names are usually acronyms (cbts.com -> CBTS).
    stem = domain.split(".")[0]
    company = stem.upper() if len(stem) <= 4 else stem.replace("-", " ").title()

    return {
        "title": title,
        "company": company,
        "location": ", ".join(data.get("locations") or []),
        "url": url,
        "raw_text": text,
    }


def fetch_schema_org_job(url: str) -> dict | None:
    """Reads a posting marked up as a schema.org JobPosting -- JSON-LD or
    itemprop microdata. SAP SuccessFactors career sites (jobs.igt.com) and
    many employer pages publish it. Without this the generic fetch took the
    browser-tab title ('Engineer NOC I Job Details | IGT, a Nevada
    Corporation') as the job title and the 'jobs.' subdomain as the company."""
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (compatible; job-application-assistant)"})
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        logger.warning("Structured job fetch failed (%s); falling back.", exc)
        return None

    def clean(text: str) -> str:
        return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text or "")).strip()

    # JSON-LD first: a single structured object when present.
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "JobPosting" and item.get("title"):
                org = item.get("hiringOrganization") or {}
                place = ((item.get("jobLocation") or [{}]) if isinstance(item.get("jobLocation"), list)
                         else [item.get("jobLocation") or {}])[0].get("address", {}) or {}
                description = BeautifulSoup(item.get("description") or "", "html.parser").get_text("\n")
                return {
                    "title": item["title"].strip(),
                    "company": (org.get("name") if isinstance(org, dict) else str(org)).split(",")[0].strip(),
                    "location": ", ".join(p for p in (place.get("addressLocality"), place.get("addressRegion")) if p),
                    "url": url,
                    "raw_text": clean(description),
                }

    # Microdata (itemprop attributes).
    def prop(name: str) -> str:
        el = soup.find(attrs={"itemprop": name})
        if el is None:
            return ""
        if el.get("content"):
            return el["content"].strip()
        # Line breaks only where the page has blocks; inline spans stay joined
        # (splitting on every tag broke sentences into one-word lines).
        for br in el.find_all("br"):
            br.replace_with("\n")
        for block in el.find_all(["p", "div", "li", "h1", "h2", "h3", "h4", "tr"]):
            block.insert_after("\n")
        return el.get_text("").strip()

    title, description = prop("title"), prop("description")
    if not title or len(description) < 200:
        return None
    return {
        "title": " ".join(title.split()),
        # "IGT, a Nevada Corporation" -> "IGT"
        "company": prop("hiringOrganization").split(",")[0].strip() or urlparse(url).netloc.split(".")[-2].upper(),
        "location": ", ".join(p for p in (prop("addressLocality"), prop("addressRegion")) if p),
        "url": url,
        "raw_text": clean(description)[:20000],
    }


def fetch_amazon_job(url: str) -> dict | None:
    """amazon.jobs.

    Its pages carry no JSON-LD, and the <title> is the posting's title with
    " - Job ID: N | Amazon.jobs" appended -- which the generic reader recorded
    as the job title, under the company "Www" (the host's first label). The
    title comes from og:title instead, and the employer is simply Amazon.
    """
    if "amazon.jobs" not in urlparse(url).netloc.lower():
        return None
    try:
        resp = requests.get(
            url, timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (compatible; job-application-assistant)"},
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Could not fetch %s: %s", url, exc)
        return None

    def meta(prop: str) -> str:
        match = re.search(rf'<meta property="{prop}" content="([^"]*)"', resp.text)
        return html.unescape(match.group(1)).strip() if match else ""

    title = meta("og:title")
    locations = re.findall(r"USA, [A-Z]{2}, [A-Za-z .-]{3,30}", resp.text)
    # Several postings share one page; the first is the one linked to.
    location = locations[0].strip(" -–") if locations else ""

    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", resp.text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    if not title or len(text) < 400:
        return None

    logger.info("Read the posting from amazon.jobs")
    return {"title": title, "company": "Amazon", "location": location,
            "url": url, "raw_text": text[:20000]}


def fetch_rendered_job(url: str) -> dict | None:
    """Reads a posting that only exists after its JavaScript runs.

    Dayforce serves 136 characters of text and builds the rest in the browser,
    so every reader that works on the served HTML found nothing and the run
    stopped at "Could not read a job description". This one opens the page the
    way a person would and reads what they would see. It is the last resort:
    it costs a browser launch, so it runs only when the plain readers fail.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    page.wait_for_timeout(4_000)
                data = page.evaluate("""() => {
                    const clean = s => (s || '').replace(/[ \\t]+/g, ' ').trim();
                    const meta = n => {
                        const m = document.querySelector(`meta[property="${n}"], meta[name="${n}"]`);
                        return m ? m.getAttribute('content') : '';
                    };
                    // A posting rendered in script often carries JSON-LD too,
                    // added at the same time.
                    let posting = null;
                    for (const tag of document.querySelectorAll('script[type="application/ld+json"]')) {
                        try {
                            const parsed = JSON.parse(tag.textContent);
                            for (const entry of [].concat(parsed, parsed['@graph'] || [])) {
                                if (entry && entry['@type'] === 'JobPosting') posting = entry;
                            }
                        } catch (e) { /* not this one */ }
                    }
                    // Not a heading inside a pop-up or a cookie banner: a Casey's
                    // posting was recorded as "Set your cookie preferences",
                    // the banner's heading, because it came first on the page.
                    const inBanner = h => !!h.closest(
                        '[role=dialog], [aria-modal=true], [id*=onetrust i], [class*=onetrust i],'
                        + ' [id*=cookie i], [class*=cookie i], [class*=consent i]');
                    const heading = [...document.querySelectorAll('h1, h2')]
                        .filter(h => !inBanner(h) && h.getClientRects().length)
                        .map(h => clean(h.innerText))
                        .find(t => t.length > 3 && !/cookie|privacy|consent/i.test(t)) || '';
                    const main = document.querySelector('main, [role=main], article, #content, .job-details');
                    return {
                        title: clean((posting && posting.title) || heading || meta('og:title') || document.title),
                        company: clean((posting && posting.hiringOrganization && posting.hiringOrganization.name)
                                       || meta('og:site_name')),
                        location: clean((posting && posting.jobLocation && posting.jobLocation.address
                                         && posting.jobLocation.address.addressLocality) || ''),
                        text: clean((main || document.body).innerText),
                    };
                }""")
            finally:
                browser.close()
    except Exception as exc:
        logger.warning("Could not render %s: %s", url, str(exc).splitlines()[0][:120])
        return None

    body = re.sub(r"\n{3,}", "\n\n", (data.get("text") or "")).strip()
    title = _clean_page_title(data.get("title") or "")
    if not title or len(body) < 400:
        return None

    company = data.get("company") or _employer_named_in(body) or _company_from_url(url)
    logger.info("Read the posting by rendering the page (%s)", urlparse(url).netloc)
    return {"title": title, "company": company, "location": data.get("location") or "",
            "url": url, "raw_text": body[:20000]}


def _employer_named_in(text: str) -> str:
    """The employer as its own equal-opportunity statement names it.

    Casey's ADP page is titled "Career Site", its logo is labelled "Corporate
    Positions" and its address says "caseysstoresupportcenter" -- but its
    footer says "Casey's Is an Equal Opportunity Employer". Nearly every
    employer publishes a sentence like it.
    """
    match = re.search(
        r"([\w'’&.\- ]{2,60}?)\s+(?:is|are)\s+an?\s+equal\s+(?:employment\s+)?opportunity",
        text or "", re.IGNORECASE)
    if not match:
        return ""
    # Only the name itself: the capitalised words right before "is".
    # "Legal links Casey's Is an Equal Opportunity" -> "Casey's".
    words = match.group(1).split()
    name_words = []
    for word in reversed(words):
        if word[:1].isupper() or word[:1].isdigit() or word in ("&", "of"):
            name_words.insert(0, word)
        else:
            break
    while name_words and name_words[0] in ("&", "of"):
        name_words.pop(0)
    name = " ".join(name_words)
    if not name or name.lower() in ("we", "our company", "the company", "company"):
        return ""
    return name[:40]


def _clean_page_title(title: str) -> str:
    """A job title, not a page title: no site name, no job id."""
    title = re.split(r"\s+[|\u2013\u2014]\s+", title)[0]
    return re.sub(r"\s*[-\u2013]\s*Job ID:?\s*\d+\s*$", "", title).strip()


def _company_from_url(url: str) -> str:
    """The employer, from the address a tenant-hosted portal uses.

    Dayforce gives every client a path of its own
    (jobs.dayforcehcm.com/en-US/<client>/CANDIDATEPORTAL/...), so the host is
    the software vendor and the path is the employer.
    """
    parsed = urlparse(url)
    host = re.sub(r"^(www|jobs|careers|apply|recruiting)\.", "", parsed.netloc)
    vendors = ("dayforcehcm", "myworkdayjobs", "icims", "greenhouse", "lever",
               "ashbyhq", "smartrecruiters", "successfactors", "taleo", "paylocity",
               "adp.com", "bamboohr")
    if any(v in host for v in vendors):
        for part in parsed.path.strip("/").split("/"):
            token = part.strip().lower()
            if token in ("en-us", "en", "candidateportal", "jobs", "job", "careers") or token.isdigit():
                continue
            return part.replace("-", " ").replace("_", " ").title()
    return host.split(".")[0].replace("-", " ").title()


def fetch_generic_job(url: str) -> dict | None:
    """Best-effort fetch for non-Workday pages."""
    try:
        resp = requests.get(
            url, timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (compatible; job-application-assistant)"},
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Could not fetch %s: %s", url, exc)
        return None

    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", resp.text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 400:
        return None

    title = ""
    m = re.search(r"<title>(.*?)</title>", resp.text, re.S | re.I)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()

    # "Support Engineer ... - Job ID: 10539098 | Amazon.jobs" is a page title,
    # not a job title; and the company is the site, not its "www" label.
    title = re.split(r"\s+[|–—]\s+", title)[0]
    title = re.sub(r"\s*[-–]\s*Job ID:?\s*\d+\s*$", "", title).strip()
    host = re.sub(r"^(www|jobs|careers|apply)\.", "", urlparse(url).netloc)
    return {
        "title": title or "Unknown Role",
        "company": host.split(".")[0].replace("-", " ").title(),
        "location": "",
        "url": url,
        "raw_text": text[:20000],
    }


def resolve_job(url: str) -> dict | None:
    host = urlparse(url).netloc.lower()

    if any(b in host for b in BLOCKED_SOURCES):
        logger.error(
            "%s is a job board, not an employer site. Open the posting there, "
            "follow its link to the company's own careers page, and use that URL.", host,
        )
        return None
    if any(a in host for a in AGGREGATORS):
        logger.error(
            "%s is a third-party auto-apply aggregator. Use the employer's own "
            "careers page instead.", host,
        )
        return None

    return (
        fetch_workday_job(url)
        or fetch_greenhouse_job(url)
        or fetch_lever_job(url)
        or fetch_ashby_job(url)
        or fetch_eightfold_job(url)
        or fetch_amazon_job(url)
        or fetch_schema_org_job(url)
        or fetch_generic_job(url)
        or fetch_rendered_job(url)  # a posting that only exists once its script runs
    )


