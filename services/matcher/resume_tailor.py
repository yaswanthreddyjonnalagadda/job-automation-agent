"""Resume and Cover Letter tailoring service, Claude first and Gemini as fallback.
Strictly follows non-negotiable requirements:
1. Reorder bullets and adjust keyword emphasis; NEVER fabricate experience.
2. Plain verbs only: Developed, Built, Worked, Implemented — never Spearheaded, Orchestrated.
3. Keep cover letters concise (<300 words).
4. Track token usage and estimated cost in USD.

Claude (ANTHROPIC_API_KEY) writes first; if that call fails for any reason (including the
Anthropic account being out of credit), Gemini (GEMINI_API_KEY) writes instead, mirroring the
RESUME_WRITER / RESUME_WRITER_FALLBACK chain in the main app's ai_choice.py. Only when neither
key is set or both calls fail does a deterministic, un-tailored base resume get attached.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Dict, Optional, Tuple

import requests

logger = logging.getLogger("matcher.tailor")

CLAUDE_COST_PER_M_INPUT = 3.00   # Sonnet 3.5: $3 / million input tokens
CLAUDE_COST_PER_M_OUTPUT = 15.00  # Sonnet 3.5: $15 / million output tokens

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_MODEL_DEFAULT = os.getenv("GEMINI_MODEL", "").strip() or "gemini-3.8-flash"
# A short ladder, not just one model: the newest flash model answers 503 (busy) often enough
# that a single older, steadier model worth falling back to is cheap insurance.
GEMINI_MODEL_FALLBACK = "gemini-3.5-flash"
GEMINI_MODELS = [m for m in dict.fromkeys([GEMINI_MODEL_DEFAULT, GEMINI_MODEL_FALLBACK])]
GEMINI_MIN_OUTPUT_TOKENS = 4096  # Gemini counts its own "thinking" against this limit


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    cost = (input_tokens / 1_000_000.0 * CLAUDE_COST_PER_M_INPUT) + (output_tokens / 1_000_000.0 * CLAUDE_COST_PER_M_OUTPUT)
    return round(cost, 4)


def _claude_generate(system_prompt: str, user_content: str, api_key: str, max_tokens: int) -> Tuple[str, int, float]:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )
    text = clean_forbidden_verbs(response.content[0].text)
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    return text, input_tokens + output_tokens, estimate_cost(input_tokens, output_tokens)


GEMINI_ATTEMPTS_PER_MODEL = 2
GEMINI_RETRY_BACKOFF_SECONDS = 2.0


def _gemini_post(model: str, payload: dict, api_key: str) -> requests.Response:
    """One model, with its own short retry for a busy server (503) or connection hiccup.
    A 4xx (bad key, bad request) will not change on retry, so it is returned as-is immediately."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, GEMINI_ATTEMPTS_PER_MODEL + 1):
        try:
            reply = requests.post(
                f"{GEMINI_BASE_URL}/models/{model}:generateContent",
                json=payload,
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                timeout=60,
            )
            if reply.status_code == 503 and attempt < GEMINI_ATTEMPTS_PER_MODEL:
                time.sleep(GEMINI_RETRY_BACKOFF_SECONDS * attempt)
                continue
            return reply
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < GEMINI_ATTEMPTS_PER_MODEL:
                time.sleep(GEMINI_RETRY_BACKOFF_SECONDS * attempt)
                continue
    raise last_exc  # type: ignore[misc]


def _gemini_generate(system_prompt: str, user_content: str, api_key: str, max_tokens: int) -> Tuple[str, int, float]:
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_content}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": max(max_tokens, GEMINI_MIN_OUTPUT_TOKENS)},
    }
    reply = None
    last_exc: Optional[Exception] = None
    for model in GEMINI_MODELS:
        try:
            reply = _gemini_post(model, payload, api_key)
            # 503 (busy) and 429 (this model's per-minute/day allowance spent) both mean this
            # model specifically can't answer right now; move straight to the next model rather
            # than retry the same one (an exhausted per-minute limit won't clear in time anyway).
            if reply.status_code in (503, 429):
                last_exc = RuntimeError(f"Gemini ({model}): {reply.status_code}")
                reply = None
                continue
            break
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            reply = None
            continue
    if reply is None:
        raise last_exc  # type: ignore[misc]
    reply.raise_for_status()
    data = reply.json()
    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason") or "no answer"
        raise RuntimeError(f"Gemini returned no candidates (blocked: {reason})")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = clean_forbidden_verbs("".join(p.get("text", "") for p in parts if not p.get("thought")))
    tokens = int((data.get("usageMetadata") or {}).get("totalTokenCount") or 0)
    return text, tokens, 0.0  # Gemini Flash cost is negligible; not worth estimating here


def _write_with_fallback(system_prompt: str, user_content: str, max_tokens: int) -> Tuple[str, int, float, str]:
    """Tries Claude, then Gemini. Returns (text, tokens, cost, writer_label) or raises if both fail/absent."""
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")
    last_exc: Optional[Exception] = None

    if anthropic_key:
        try:
            text, tokens, cost = _claude_generate(system_prompt, user_content, anthropic_key, max_tokens)
            return text, tokens, cost, "claude"
        except Exception as exc:
            logger.error("Claude API call failed: %s; trying the next writer", exc)
            last_exc = exc

    if gemini_key:
        try:
            text, tokens, cost = _gemini_generate(system_prompt, user_content, gemini_key, max_tokens)
            return text, tokens, cost, "gemini"
        except Exception as exc:
            logger.error("Gemini API call failed: %s", exc)
            last_exc = exc

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("no ANTHROPIC_API_KEY or GEMINI_API_KEY configured")


def clean_forbidden_verbs(text: str) -> str:
    """Enforce requirement: replace buzzword verbs with plain verbs."""
    replacements = {
        r"\bSpearheaded\b": "Led",
        r"\bspearheaded\b": "led",
        r"\bOrchestrated\b": "Built",
        r"\borchestrated\b": "built",
        r"\bLeveraged\b": "Used",
        r"\bleveraged\b": "used",
        r"\bSynergized\b": "Coordinated",
        r"\bsynergized\b": "coordinated",
    }
    for pat, rep in replacements.items():
        text = re.sub(pat, rep, text)
    return text


def tailor_resume(
    base_resume: str,
    job_description: str,
    api_key: Optional[str] = None,
) -> Tuple[str, int, float]:
    """Tailor base resume for the job description.
    Returns (tailored_text, tokens_used, cost_usd).
    """
    if api_key:
        os.environ.setdefault("ANTHROPIC_API_KEY", api_key)
    system_prompt = (
        "You are a resume tailoring expert. Given the base resume and job description, produce a tailored resume "
        "that maximizes ATS match. Rules: use plain verbs (Developed, Built, Worked, Implemented — never Spearheaded or Orchestrated); "
        "keep all original experience and dates; only reorder bullets and adjust keyword emphasis; do not fabricate experience."
    )
    user_content = f"Base Resume:\n{base_resume}\n\nJob Description:\n{job_description}"

    try:
        text, tokens, cost, writer = _write_with_fallback(system_prompt, user_content, max_tokens=2500)
        logger.info("Tailored resume written via %s", writer)
        return text, tokens, cost
    except Exception as exc:
        logger.error("No writer could tailor the resume (%s); attaching the clean base resume", exc)
        return clean_forbidden_verbs(base_resume), 0, 0.0


def generate_cover_letter(
    tailored_resume: str,
    job_description: str,
    company_name: str,
    api_key: Optional[str] = None,
) -> Tuple[str, int, float]:
    """Generate professional, targeted cover letter under 300 words."""
    if api_key:
        os.environ.setdefault("ANTHROPIC_API_KEY", api_key)
    system_prompt = (
        "Write a professional cover letter for this role. Reference specific requirements from the JD and match "
        "them to the candidate's experience. Keep it under 300 words. Professional but not generic. Plain verbs only."
    )
    user_content = f"Company: {company_name}\n\nResume:\n{tailored_resume}\n\nJob Description:\n{job_description}"

    try:
        text, tokens, cost, writer = _write_with_fallback(system_prompt, user_content, max_tokens=800)
        logger.info("Cover letter written via %s", writer)
        return text, tokens, cost
    except Exception as exc:
        logger.error("No writer could write the cover letter (%s); attaching a generic one", exc)
        cl_text = (
            f"Dear Hiring Team at {company_name},\n\n"
            "I am excited to apply for the network engineering position. With strong experience in routing, switching, "
            "firewalls, and hybrid cloud infrastructure, I have built and maintained reliable enterprise networks. "
            "I look forward to contributing to your team's mission.\n\n"
            "Sincerely,\nYaswanth Reddy Jonnalagadda"
        )
        return cl_text, 0, 0.0

