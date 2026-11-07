"""
Claude API Free-Text Parser for SearchSpec.
Added by ECC: Converts natural language candidate instructions into a structured, validated SearchSpec.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Optional

from services.matcher.search_spec import SearchSpec

logger = logging.getLogger("search_spec_parser")

SYSTEM_PROMPT = """You are an expert technical recruiter and job-search system architect.
Your job is to parse the candidate's free-text career instructions into a structured JSON configuration.
Return ONLY valid JSON matching this schema with no markdown formatting or extra commentary:
{
  "target_titles": ["string"],
  "title_synonyms": {"primary_title": ["synonym_1", "synonym_2"]},
  "adjacent_titles": ["string"],
  "seniority_levels": ["Senior", "Lead", "Staff", "Mid"],
  "must_have_keywords": ["string"],
  "nice_to_have_keywords": ["string"],
  "locations": ["string"],
  "workplace_types": ["remote", "hybrid", "onsite"],
  "salary_floor_annual": 125000,
  "salary_currency": "USD",
  "employment_types": ["full-time"],
  "requires_visa_sponsorship": true,
  "excluded_companies": ["string"],
  "excluded_keywords": ["string"],
  "posting_age_window_hours": 72,
  "hard_criteria": {
    "sponsorship": true,
    "location": true,
    "title": true,
    "salary": false,
    "seniority": false
  }
}
"""


def _call_claude_api(instructions: str, api_key: str) -> Dict[str, Any]:
    """Invokes Anthropic Claude API to extract structured SearchSpec JSON."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-3-5-sonnet-latest",
            max_tokens=2048,
            temperature=0.0,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": instructions}],
        )
        content = ""
        for block in response.content:
            if hasattr(block, "text"):
                content += block.text
        # Clean any accidental markdown code fences
        cleaned = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned.strip())
        return json.loads(cleaned)
    except Exception as exc:
        logger.warning("Claude API parsing failed, falling back to heuristic parsing: %s", exc)
        return _parse_heuristically(instructions)


def _parse_heuristically(instructions: str) -> Dict[str, Any]:
    """Deterministic fallback parser for offline testing or missing API keys."""
    lower = instructions.lower()
    titles = []
    # Identify network engineering titles
    known_roles = [
        "Network Engineer",
        "Senior Network Engineer",
        "Cloud Network Engineer",
        "Network Deployment Engineer",
        "Network Automation Engineer",
        "Network Security Engineer",
        "Data Center Network Engineer",
        "SD-WAN Engineer",
        "Network Architect",
    ]
    for r in known_roles:
        if r.lower() in lower:
            titles.append(r)
    if not titles:
        titles = ["Network Engineer", "Senior Network Engineer"]

    locations = []
    if "remote" in lower:
        locations.append("Remote")
    if "fairfax" in lower:
        locations.append("Fairfax, VA")
    if "arlington" in lower:
        locations.append("Arlington, VA")
    if not locations:
        locations = ["Remote", "Fairfax, VA", "Arlington, VA"]

    salary = 125000
    m_sal = re.search(r"(\d{2,3})k", lower)
    if m_sal:
        salary = int(m_sal.group(1)) * 1000

    sponsorship = True
    if "no sponsorship" in lower or "us citizen only" in lower:
        sponsorship = False

    return {
        "target_titles": titles,
        "title_synonyms": {titles[0]: [f"Senior {titles[0]}", f"Lead {titles[0]}"]},
        "adjacent_titles": ["Infrastructure Engineer", "Systems Engineer"],
        "seniority_levels": ["Senior", "Lead"],
        "must_have_keywords": ["cisco", "network"],
        "nice_to_have_keywords": ["bgp", "ospf", "python", "ansible"],
        "locations": locations,
        "workplace_types": ["remote", "hybrid", "onsite"],
        "salary_floor_annual": salary,
        "salary_currency": "USD",
        "employment_types": ["full-time"],
        "requires_visa_sponsorship": sponsorship,
        "excluded_companies": [],
        "excluded_keywords": ["security clearance", "polygraph", "sales"],
        "posting_age_window_hours": 72,
        "hard_criteria": {
            "sponsorship": sponsorship,
            "location": True,
            "title": True,
            "salary": False,
        },
    }


def parse_search_instructions(
    instructions: str,
    api_key: Optional[str] = None,
    version: int = 1,
) -> SearchSpec:
    """Parses free-text user instructions into a validated, versioned SearchSpec."""
    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if key and not key.startswith("test-"):
        parsed = _call_claude_api(instructions, key)
    else:
        parsed = _call_claude_api(instructions, key or "")

    parsed["raw_instructions"] = instructions
    parsed["version"] = version
    return SearchSpec.model_validate(parsed)


def bootstrap_search_spec_from_profile(profile_path: Optional[str] = None) -> SearchSpec:
    """Creates a baseline SearchSpec from config/user_profile.yaml."""
    from services.matcher.scoring import load_user_profile
    prof = load_user_profile(profile_path)
    titles = prof.get("target_roles", ["Network Engineer", "Senior Network Engineer", "Cloud Network Engineer"])
    locs = prof.get("preferred_locations", ["Remote", "Fairfax, VA", "Arlington, VA"])
    salary_cfg = prof.get("salary_range", {})
    salary_floor = salary_cfg.get("min", 125000) if isinstance(salary_cfg, dict) else 125000

    return SearchSpec(
        version=1,
        raw_instructions="Bootstrapped from user_profile.yaml baseline configuration.",
        target_titles=titles,
        title_synonyms={
            "Network Engineer": ["Network Infrastructure Engineer", "Data Center Network Engineer"],
            "Cloud Network Engineer": ["AWS Network Engineer", "Azure Network Engineer"],
        },
        adjacent_titles=["Infrastructure Engineer", "Network Architect"],
        seniority_levels=["Senior", "Lead", "Staff"],
        must_have_keywords=prof.get("must_have_keywords", ["cisco", "network"]),
        nice_to_have_keywords=prof.get("nice_to_have_keywords", ["bgp", "ospf", "sd-wan", "python"]),
        locations=locs,
        salary_floor_annual=salary_floor,
        requires_visa_sponsorship=bool(prof.get("visa_sponsorship_required", True)),
        excluded_keywords=prof.get("disqualifiers", ["security clearance", "public trust", "TS/SCI"]),
        is_active=True,
    )

