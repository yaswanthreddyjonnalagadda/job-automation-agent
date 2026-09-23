"""
intelligence/inference.py - Autonomous decision engine for mapping and answering form inputs.
Guarantees valid choices on dropdowns/radios by strictly constraining choices to scraped DOM options.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from agent_v2.core.parser import FormElement
from agent_v2.intelligence.llm_client import LLMClient

logger = logging.getLogger("agent_v2.inference")

STANDARD_PROFILE_FIELDS = {
    r"first\s*name|given\s*name": "first_name",
    r"last\s*name|family\s*name|surname": "last_name",
    r"^name$|full\s*name": "full_name",
    r"e-?mail": "email",
    r"phone|mobile|cell": "phone",
    r"address\s*(?:line\s*1)?": "address",
    r"city": "city",
    r"state|province|region": "state",
    r"zip|postal\s*code": "postal_code",
    r"country": "country",
    r"linkedin": "linkedin_url",
    r"github": "github_url",
    r"portfolio|website": "portfolio_url",
}


class InferenceEngine:
    """Decision engine matching profile attributes and constraining dropdown selections."""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client or LLMClient()

    def direct_profile_match(self, element: FormElement, profile: dict[str, Any]) -> Optional[str]:
        """Attempt deterministic regex matching against known profile fields first."""
        target_label = f"{element.label} {element.placeholder} {element.name} {element.id}".lower()

        # Check standard fields
        for pattern, key in STANDARD_PROFILE_FIELDS.items():
            if re.search(pattern, target_label, re.IGNORECASE):
                val = profile.get(key)
                if val:
                    return str(val)

        # Handle Full Name composition if individual names are split
        if "first_name" in profile and "last_name" in profile and "full_name" not in profile:
            profile["full_name"] = f"{profile['first_name']} {profile['last_name']}".strip()

        return None

    async def decide_answer(
        self,
        element: FormElement,
        profile: dict[str, Any],
        historical_answer: Optional[str] = None,
    ) -> str:
        """Determines the appropriate answer for a form control.
        For dropdowns and select groups, forces selection exclusively from available DOM choices.
        """
        # 1. Historical verified answer takes precedence if valid
        if historical_answer:
            if element.options:
                valid_values = [opt["text"].lower() for opt in element.options] + [opt["value"].lower() for opt in element.options]
                if historical_answer.lower() in valid_values:
                    return historical_answer
            else:
                return historical_answer

        # 2. Try deterministic profile match for text inputs
        if element.tag in ("input", "textarea") and element.type not in ("checkbox", "radio"):
            matched = self.direct_profile_match(element, profile)
            if matched:
                return matched

        # 3. Checkbox / Boolean consent / Privacy handling
        if element.type == "checkbox" or element.role == "checkbox":
            label_lower = element.label.lower()
            if any(term in label_lower for term in ("agree", "consent", "privacy", "terms", "acknowledge", "certify")):
                return "true"
            # Default checkbox to unchecked unless profile explicitly says Yes
            return "false"

        # 4. Multi-choice elements (<select>, radio groups, comboboxes with options)
        if element.options:
            return await self._select_constrained_choice(element, profile)

        # 5. Open-ended question fallback to LLM
        return await self._ask_llm_freeform(element, profile)

    async def _select_constrained_choice(self, element: FormElement, profile: dict[str, Any]) -> str:
        """Forces the LLM to choose exclusively from available options array."""
        options_list = [opt["text"] for opt in element.options if opt["text"].strip()]
        if not options_list:
            options_list = [opt["value"] for opt in element.options if opt["value"].strip()]

        if not options_list:
            return ""

        prompt = f"""
Given the form question and candidate profile, choose the SINGLE BEST matching option from the Allowed Options list.

Form Question: "{element.label or element.name or element.id}"
Allowed Options:
{json.dumps(options_list, indent=2)}

Candidate Profile:
{json.dumps(profile, indent=2)}

CRITICAL: Return a JSON object with a single key "selected_option" containing the EXACT text from Allowed Options.
Do NOT invent an option not in the list.
"""
        try:
            res = await self.llm.complete_json(prompt)
            chosen = res.get("selected_option", "")
            # Verify chosen option is strictly in the allowed options
            for opt in options_list:
                if opt.strip().lower() == chosen.strip().lower():
                    return opt

            # If LLM returned a slight mismatch, pick the closest substring
            for opt in options_list:
                if chosen.strip().lower() in opt.lower() or opt.lower() in chosen.strip().lower():
                    return opt

            # Fallback to the first non-empty option
            logger.warning("LLM choice %r not found in options %r; selecting %r", chosen, options_list, options_list[0])
            return options_list[0]
        except Exception as exc:
            logger.warning("Constrained choice selection failed (%s); using first option %r", exc, options_list[0])
            return options_list[0]

    async def _ask_llm_freeform(self, element: FormElement, profile: dict[str, Any]) -> str:
        """Generates concise, factual answer for freeform screening questions."""
        prompt = f"""
Answer this job application screening question concisely using the candidate's profile.

Question: "{element.label or element.name}"
Candidate Profile:
{json.dumps(profile, indent=2)}

Rules:
- Be factual and concise.
- If asking about salary expectation, use the profile salary or state 'Negotiable'.
- If asking about work authorization, answer accurately according to the profile.
- Return ONLY the direct answer text.
"""
        answer = await self.llm.complete(prompt, max_tokens=150)
        return answer.strip("\"' ")

