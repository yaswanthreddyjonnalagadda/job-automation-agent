"""Preview required application questions before opening a browser (Task 5).

Ported from freehire (internal/ingest/applyform/{greenhouse,lever,ashby}.go), MIT License.
Supports Greenhouse, Lever, and Ashby. Returns None when no preview API exists or calls fail.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import urlparse

import requests

import acknowledgements
import answer_bank
import concept_matcher
import diagnostics
import legal_questions

logger = logging.getLogger(__name__)

_UPLOAD = re.compile(r"\b(resume|c\.?v\.?|curriculum vitae|cover letter)\b", re.IGNORECASE)
_GH_JOB_ID = re.compile(r"/jobs/(\d+)")
_LEVER_QUESTION_BLOCK = re.compile(r'<div class="application-question">([\s\S]*?)</div>', re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def _clean_text(s: str) -> str:
    return " ".join((s or "").split()).strip()


def parse_greenhouse_response(data: dict) -> list[dict]:
    """Parse Greenhouse API response (?questions=true) into standard preview questions."""
    blocks: list[dict] = list(data.get("questions") or [])
    blocks.extend(data.get("location_questions") or [])
    for group in data.get("compliance") or []:
        blocks.extend((group or {}).get("questions") or [])
    blocks.extend(data.get("demographic_questions") or [])

    out = []
    for raw in blocks:
        if not isinstance(raw, dict):
            continue
        fields = raw.get("fields") or []
        options: list[str] = []
        q_type = "input"
        for field in fields:
            if isinstance(field, dict):
                q_type = field.get("type") or q_type
                for value in field.get("values") or []:
                    label = (value or {}).get("label") if isinstance(value, dict) else value
                    if label:
                        options.append(str(label))
        out.append({
            "label": str(raw.get("label") or "").strip(),
            "required": bool(raw.get("required")),
            "type": q_type,
            "options": options,
        })
    return out


def fetch_greenhouse_preview(url: str, session: Optional[requests.Session] = None) -> Optional[list[dict]]:
    """Fetch Greenhouse questions via public API endpoint."""
    parsed = urlparse(url)
    match = _GH_JOB_ID.search(parsed.path)
    if not match:
        return None
    job_id = match.group(1)
    # Extract board token: boards.greenhouse.io/{token}/jobs/{id}
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return None
    token = parts[0] if parts[0] != "jobs" else (parsed.query_params.get("for") if hasattr(parsed, "query_params") else None)
    if not token and "embed" in parsed.path:
        qs = dict(q.split("=") for q in parsed.query.split("&") if "=" in q)
        token = qs.get("for")
    if not token:
        token = parts[0]
    api_url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}?questions=true"
    s = session or requests.Session()
    try:
        resp = s.get(api_url, headers={"User-Agent": "job-agent/1", "Accept": "application/json"}, timeout=8.0)
        if resp.status_code == 200:
            return parse_greenhouse_response(resp.json())
    except Exception as exc:
        logger.debug("Greenhouse preview failed for %s: %s", url, diagnostics.short_exc(exc, 100))
    return None


def parse_ashby_response(data: dict) -> list[dict]:
    """Parse Ashby GraphQL/API response into standard preview questions."""
    job_posting = (data.get("data") or {}).get("jobPosting") or data.get("jobPosting") or {}
    form_def = job_posting.get("applicationFormDefinition") or {}
    sections = form_def.get("sections") or []
    out = []
    for section in sections:
        for field in section.get("fields") or []:
            if not isinstance(field, dict):
                continue
            options = []
            for val in field.get("selectableValues") or []:
                if isinstance(val, dict) and "label" in val:
                    options.append(str(val["label"]))
                elif isinstance(val, str):
                    options.append(val)
            out.append({
                "label": _clean_text(field.get("title") or field.get("label") or ""),
                "required": bool(field.get("isRequired")),
                "type": field.get("type") or "text",
                "options": options,
            })
    return out


def fetch_ashby_preview(url: str, session: Optional[requests.Session] = None) -> Optional[list[dict]]:
    """Fetch Ashby questions via public non-user GraphQL endpoint."""
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    company, job_id = parts[0], parts[1]
    api_url = "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting"
    payload = {
        "operationName": "ApiJobPosting",
        "variables": {
            "organizationHostedJobsPageName": company,
            "jobPostingId": job_id,
        },
        "query": (
            "query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) {\n"
            "  jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) {\n"
            "    applicationFormDefinition\n"
            "  }\n"
            "}"
        ),
    }
    s = session or requests.Session()
    try:
        resp = s.post(
            api_url,
            json=payload,
            headers={
                "User-Agent": "job-agent/1",
                "Content-Type": "application/json",
                "Apollographql-Client-Name": "frontend_non_user",
            },
            timeout=8.0,
        )
        if resp.status_code == 200:
            return parse_ashby_response(resp.json())
    except Exception as exc:
        logger.debug("Ashby preview failed for %s: %s", url, diagnostics.short_exc(exc, 100))
    return None


def parse_lever_html(html: str) -> list[dict]:
    """Parse Lever apply page HTML into standard preview questions."""
    out = []
    for match in _LEVER_QUESTION_BLOCK.finditer(html):
        block = match.group(1)
        label_match = re.search(r"<label[^>]*>([\s\S]*?)</label>", block, re.IGNORECASE)
        if not label_match:
            continue
        raw_label = label_match.group(1)
        required = bool("✱" in raw_label or "*" in raw_label or "required" in block.lower())
        clean_label = _clean_text(_TAG.sub("", raw_label).replace("✱", "").replace("*", ""))
        q_type = "file" if 'type="file"' in block else ("select" if "<select" in block else "input")
        options = []
        for opt in re.finditer(r"<option[^>]*>([\s\S]*?)</option>", block, re.IGNORECASE):
            text = _clean_text(_TAG.sub("", opt.group(1)))
            if text and not text.lower().startswith("select"):
                options.append(text)
        out.append({
            "label": clean_label,
            "required": required,
            "type": q_type,
            "options": options,
        })
    return out


def fetch_lever_preview(url: str, session: Optional[requests.Session] = None) -> Optional[list[dict]]:
    """Fetch Lever apply page HTML and parse questions."""
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    apply_url = f"https://jobs.lever.co/{parts[0]}/{parts[1]}/apply"
    s = session or requests.Session()
    try:
        resp = s.get(apply_url, headers={"User-Agent": "job-agent/1"}, timeout=8.0)
        if resp.status_code == 200:
            parsed_questions = parse_lever_html(resp.text)
            if parsed_questions:
                return parsed_questions
    except Exception as exc:
        logger.debug("Lever preview failed for %s: %s", url, diagnostics.short_exc(exc, 100))
    return None


def preview_questions(job: Any, session: Optional[requests.Session] = None) -> Optional[list[dict]]:
    """Get structured application questions from ATS API before browser launch.

    Returns list of {label, required, type, options} or None when no preview API exists or call fails.
    """
    url = getattr(job, "url", "") if hasattr(job, "url") else (job.get("url") if isinstance(job, dict) else "")
    if not url:
        return None
    import sites
    adapter = sites.adapter_for(url)
    if hasattr(adapter, "preview_questions") and adapter.name in ("greenhouse", "lever", "ashby"):
        return adapter.preview_questions(job, session=session)
    return None


def _is_file_upload(q: dict) -> bool:
    if q.get("type") in ("file", "attachment"):
        return True
    return bool(_UPLOAD.search(q.get("label") or ""))


def unanswerable_required_questions(questions: list[dict], profile: Any, job: Any) -> list[str]:
    """Runs required questions through Task 1/2 answer path in dry mode.

    Returns labels of required questions that would wait for owner intervention.
    """
    company = getattr(job, "company", "") if hasattr(job, "company") else (job.get("company", "") if isinstance(job, dict) else "")
    unanswerable = []
    for q in questions or []:
        label = (q.get("label") or "").strip()
        if not label or not q.get("required"):
            continue
        options = q.get("options") or []
        if _is_file_upload(q):
            continue
        if acknowledgements.is_acknowledgement(label):
            continue
        # Task 1 legal questions:
        cat = legal_questions.classify_legal_question(label)
        if cat:
            facts = legal_questions.get_category_facts(cat, profile)
            if facts:
                continue
            decline = legal_questions.find_decline_option(options)
            if decline:
                continue
            unanswerable.append(label)
            continue
        # Deterministic profile / concept matcher
        concept = concept_matcher.match_concept(label)
        val, _src = concept_matcher.resolve_profile_value(concept or "", profile, options, label)
        if val:
            continue
        # Approved answers bank
        if answer_bank.lookup(label, company) is not None:
            continue
        # Without facts or rules, grounded AI contract (Task 2) cannot answer required field
        unanswerable.append(label)
    return unanswerable


def check_early_skip(job: Any, profile: Any, tracker: Any, key: str) -> bool:
    """Checks ATS questions before opening browser.

    If any required question is unanswerable, records 'needs_answers' and returns True (skip browser).
    Otherwise returns False (proceed with browser).
    """
    questions = preview_questions(job)
    if questions is None:
        return False
    unanswerable = unanswerable_required_questions(questions, profile, job)
    if unanswerable:
        note = f"Needs answers for {len(unanswerable)} required question(s): {'; '.join(unanswerable)}"
        logger.info("EARLY_SKIP: %s", note)
        if hasattr(tracker, "update_status"):
            tracker.update_status(key, "needs_answers", notes=note)
        return True
    return False
