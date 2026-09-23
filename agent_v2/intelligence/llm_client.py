"""
intelligence/llm_client.py - Multi-provider async LLM client for Anthropic, OpenAI, and Gemini.
Includes exponential backoff, retry handling, and JSON extraction wrappers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Literal, Optional
import httpx

from agent_v2.config import config

logger = logging.getLogger("agent_v2.llm")

ProviderType = Literal["anthropic", "openai", "gemini"]


class LLMClient:
    """Async unified connector for Anthropic Claude, OpenAI, and Google Gemini."""

    def __init__(self, provider: Optional[ProviderType] = None):
        self.provider: ProviderType = provider or config.default_llm_provider
        self.anthropic_key = config.anthropic_api_key
        self.openai_key = config.openai_api_key
        self.gemini_key = config.gemini_api_key

    async def complete(
        self,
        prompt: str,
        system_prompt: str = "You are an expert autonomous web automation agent.",
        temperature: float = 0.1,
        max_tokens: int = 1500,
        max_retries: int = 3,
    ) -> str:
        """Execute completion with exponential backoff and retry mechanism."""
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                if self.provider == "anthropic":
                    return await self._call_anthropic(prompt, system_prompt, temperature, max_tokens)
                elif self.provider == "openai":
                    return await self._call_openai(prompt, system_prompt, temperature, max_tokens)
                elif self.provider == "gemini":
                    return await self._call_gemini(prompt, system_prompt, temperature, max_tokens)
                else:
                    raise ValueError(f"Unsupported LLM provider: {self.provider}")
            except Exception as exc:
                last_error = exc
                wait_time = (2 ** attempt) + 0.5
                logger.warning(
                    "LLM %s call failed (attempt %d/%d): %s. Retrying in %.1fs...",
                    self.provider, attempt, max_retries, exc, wait_time
                )
                await asyncio.sleep(wait_time)

        raise RuntimeError(f"LLM request failed after {max_retries} retries: {last_error}") from last_error

    async def complete_json(
        self,
        prompt: str,
        system_prompt: str = "You are a precise data extraction assistant. Return valid JSON only.",
    ) -> dict[str, Any]:
        """Execute completion and parse JSON block from output."""
        formatted_prompt = f"{prompt}\n\nIMPORTANT: Respond with pure JSON only, without markdown fences or commentary."
        raw_text = await self.complete(formatted_prompt, system_prompt=system_prompt)
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip(), flags=re.MULTILINE)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse JSON response: %s", cleaned[:200])
            raise ValueError(f"Invalid JSON returned by LLM: {exc}") from exc

    # -------------------------------------------------------------------------
    # Provider Implementations
    # -------------------------------------------------------------------------
    async def _call_anthropic(self, prompt: str, system: str, temp: float, max_tokens: int) -> str:
        if not self.anthropic_key:
            raise ValueError("Anthropic API key is not configured in environment (ANTHROPIC_API_KEY).")
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": self.anthropic_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temp,
        }
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["content"][0]["text"].strip()

    async def _call_openai(self, prompt: str, system: str, temp: float, max_tokens: int) -> str:
        if not self.openai_key:
            raise ValueError("OpenAI API key is not configured in environment (OPENAI_API_KEY).")
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.openai_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temp,
        }
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()

    async def _call_gemini(self, prompt: str, system: str, temp: float, max_tokens: int) -> str:
        if not self.gemini_key:
            raise ValueError("Google Gemini API key is not configured in environment (GEMINI_API_KEY).")
        model = "gemini-2.0-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.gemini_key}"
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temp,
                "maxOutputTokens": max_tokens,
            },
        }
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()

