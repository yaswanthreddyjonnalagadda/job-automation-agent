"""Tier 3: AI-powered Career Page Extractor for unstructured pages."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

logger = logging.getLogger("poller.ai_extractor")


def clean_html_for_llm(html_content: str, base_url: str, max_chars: int = 15_000) -> str:
    """Preprocess HTML to a compact text representation preserving links and structural cues."""
    soup = BeautifulSoup(html_content, "html.parser")

    # Remove non-content tags
    for tag in soup(["script", "style", "nav", "footer", "header", "svg", "noscript", "meta", "link"]):
        tag.decompose()

    # Convert anchors to markdown style: [Link Text](absolute_url)
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = urljoin(base_url, a["href"])
        if text and href.startswith("http"):
            a.replace_with(f" [{text}]({href}) ")

    text = soup.get_text(separator="\n")
    # Normalize excessive newlines and whitespace
    clean_lines = [line.strip() for line in text.splitlines() if line.strip()]
    condensed = "\n".join(clean_lines)

    if len(condensed) > max_chars:
        return condensed[:max_chars] + "\n...[truncated]"
    return condensed


def extract_jobs_with_ai(html_content: str, base_url: str) -> List[Dict[str, Any]]:
    """Uses a lightweight LLM (Gemini or Claude) to extract job openings from unstructured page text."""
    clean_text = clean_html_for_llm(html_content, base_url)
    if not clean_text or len(clean_text) < 100:
        return []

    system_prompt = (
        "You are an expert career page parser. Given the text and links from an employer's career page, "
        "extract all distinct job openings into a JSON object with a 'jobs' array. "
        "Each job item must have: title (str), location (str or 'Not Specified'), department (str or ''), "
        "url (str, absolute URL to apply/details), and remote (bool). "
        "If no specific job openings exist on this page, return {'jobs': []}. "
        "Respond ONLY with valid JSON."
    )

    user_prompt = f"Base URL: {base_url}\n\nCareer Page Content:\n{clean_text}"

    # Try Gemini API first (cost-effective / free tier available)
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key:
        try:
            import requests
            model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_key}"
            payload = {
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"parts": [{"text": user_prompt}]}],
                "generationConfig": {"response_mime_type": "application/json", "temperature": 0.1},
            }
            resp = requests.post(url, json=payload, timeout=25)
            if resp.status_code == 200:
                body = resp.json()
                raw_reply = body["candidates"][0]["content"]["parts"][0]["text"]
                return _parse_llm_jobs(raw_reply, base_url)
        except Exception as exc:
            logger.warning("Gemini AI job extraction failed: %s", exc)

    # Fallback to Anthropic Claude
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_key)
            model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
            msg = client.messages.create(
                model=model,
                max_tokens=2000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw_reply = msg.content[0].text
            return _parse_llm_jobs(raw_reply, base_url)
        except Exception as exc:
            logger.warning("Claude AI job extraction failed: %s", exc)

    logger.info("No AI API keys available or all calls failed for AI extraction.")
    return []


def _parse_llm_jobs(raw_json: str, base_url: str) -> List[Dict[str, Any]]:
    """Parse and normalize LLM JSON response into canonical job dictionaries."""
    try:
        # Strip potential markdown code fences
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw_json.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned.strip())
        data = json.loads(cleaned)
    except Exception as exc:
        logger.error("Failed to parse JSON from AI extractor: %s", exc)
        return []

    jobs_list = data.get("jobs", []) if isinstance(data, dict) else data if isinstance(data, list) else []
    results: List[Dict[str, Any]] = []

    for item in jobs_list:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        location = str(item.get("location") or "Not Specified").strip()
        dept = str(item.get("department") or "").strip()
        url = str(item.get("url") or base_url).strip()
        if not url.startswith("http"):
            url = urljoin(base_url, url)
        is_remote = bool(item.get("remote")) or bool(
            re.search(r"\b(remote|anywhere|virtual)\b", f"{title} {location}", re.I)
        )

        external_id = str(item.get("req_id") or re.sub(r"[^\w]", "_", url))

        results.append({
            "external_id": external_id,
            "title": title,
            "location": location,
            "department": dept,
            "description": "",
            "description_html": "",
            "apply_url": url,
            "salary_min": None,
            "salary_max": None,
            "salary_currency": "USD",
            "employment_type": "full-time",
            "remote": is_remote,
            "posted_at": None,
        })

    return results

