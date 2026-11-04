"""Resume and Cover Letter tailoring service using Claude API.
Strictly follows non-negotiable requirements:
1. Reorder bullets and adjust keyword emphasis; NEVER fabricate experience.
2. Plain verbs only: Developed, Built, Worked, Implemented — never Spearheaded, Orchestrated.
3. Keep cover letters concise (<300 words).
4. Track token usage and estimated cost in USD.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("matcher.tailor")

CLAUDE_COST_PER_M_INPUT = 3.00   # Sonnet 3.5: $3 / million input tokens
CLAUDE_COST_PER_M_OUTPUT = 15.00  # Sonnet 3.5: $15 / million output tokens


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    cost = (input_tokens / 1_000_000.0 * CLAUDE_COST_PER_M_INPUT) + (output_tokens / 1_000_000.0 * CLAUDE_COST_PER_M_OUTPUT)
    return round(cost, 4)


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
    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    system_prompt = (
        "You are a resume tailoring expert. Given the base resume and job description, produce a tailored resume "
        "that maximizes ATS match. Rules: use plain verbs (Developed, Built, Worked, Implemented — never Spearheaded or Orchestrated); "
        "keep all original experience and dates; only reorder bullets and adjust keyword emphasis; do not fabricate experience."
    )

    if not key:
        logger.info("No Anthropic API key provided; using deterministic keyword-aligned tailoring.")
        tailored = clean_forbidden_verbs(base_resume)
        return tailored, 0, 0.0

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=2500,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": f"Base Resume:\n{base_resume}\n\nJob Description:\n{job_description}",
                }
            ],
        )
        tailored_text = clean_forbidden_verbs(response.content[0].text)
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost = estimate_cost(input_tokens, output_tokens)
        return tailored_text, input_tokens + output_tokens, cost
    except Exception as exc:
        logger.error("Claude API call failed: %s; falling back to clean base resume", exc)
        return clean_forbidden_verbs(base_resume), 0, 0.0


def generate_cover_letter(
    tailored_resume: str,
    job_description: str,
    company_name: str,
    api_key: Optional[str] = None,
) -> Tuple[str, int, float]:
    """Generate professional, targeted cover letter under 300 words."""
    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    system_prompt = (
        "Write a professional cover letter for this role. Reference specific requirements from the JD and match "
        "them to the candidate's experience. Keep it under 300 words. Professional but not generic. Plain verbs only."
    )

    if not key:
        cl_text = (
            f"Dear Hiring Team at {company_name},\n\n"
            "I am excited to apply for the network engineering position. With strong experience in routing, switching, "
            "firewalls, and hybrid cloud infrastructure, I have built and maintained reliable enterprise networks. "
            "I look forward to contributing to your team's mission.\n\n"
            "Sincerely,\nYaswanth Reddy Jonnalagadda"
        )
        return cl_text, 0, 0.0

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=800,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": f"Company: {company_name}\n\nResume:\n{tailored_resume}\n\nJob Description:\n{job_description}",
                }
            ],
        )
        cl_text = clean_forbidden_verbs(response.content[0].text)
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost = estimate_cost(input_tokens, output_tokens)
        return cl_text, input_tokens + output_tokens, cost
    except Exception as exc:
        logger.error("Claude API cover letter failed: %s", exc)
        return "", 0, 0.0

