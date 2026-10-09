"""The models each provider offers this owner's key, for the choices on Settings.

Asked of the providers themselves (Anthropic, Google and OpenAI each list the models a key can use), never
written into the code: models are retired (gemini-2.5-flash, for new keys, 25 September 2026) and new ones
appear. Only models that write text are kept -- an embedding, voice, image or video model cannot answer a form
or write a resume. The lists are kept for a few hours in data/_ai_models.json, and Settings can ask again.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

CACHE_FILE = Path("data/_ai_models.json")
CACHE_SECONDS = 6 * 3600

# Kinds of model that do not write text, by the words their names use.
NOT_TEXT = re.compile(r"(embed|tts|audio|realtime|transcri|whisper|image|imagen|veo|lyria|dall-e|moderation|"
                      r"banana|robotics|computer-use|deep-research|antigravity|search|omni|live|native)", re.I)
_DATED = re.compile(r"-\d{4}-\d{2}-\d{2}$|-\d{8}$")          # a pinned snapshot of a model already listed


def _google(config: Any) -> list[str]:
    key = getattr(config, "gemini_api_key", "")
    base = str(getattr(config, "gemini_base_url", "") or "https://generativelanguage.googleapis.com/v1beta")
    names, token = [], ""
    for _ in range(10):
        reply = requests.get(f"{base.rstrip('/')}/models", headers={"x-goog-api-key": key}, timeout=20,
                             params={"pageSize": 200, **({"pageToken": token} if token else {})})
        reply.raise_for_status()
        data = reply.json()
        for model in data.get("models", []):
            name = model.get("name", "").split("/", 1)[-1]
            if ("generateContent" in model.get("supportedGenerationMethods", [])
                    and name.startswith(("gemini", "gemma")) and not NOT_TEXT.search(name)
                    and not name.endswith(("-latest", "-customtools"))):
                names.append(name)
        token = data.get("nextPageToken", "")
        if not token:
            break
    return names


def _anthropic(config: Any) -> list[str]:
    reply = requests.get("https://api.anthropic.com/v1/models", params={"limit": 1000}, timeout=20,
                         headers={"x-api-key": getattr(config, "anthropic_api_key", ""),
                                  "anthropic-version": "2023-06-01"})
    reply.raise_for_status()
    return [m["id"] for m in reply.json().get("data", []) if str(m.get("id", "")).startswith("claude")]


def _openai(config: Any) -> list[str]:
    base = str(getattr(config, "openai_base_url", "") or "https://api.openai.com/v1").rstrip("/")
    reply = requests.get(f"{base}/models", timeout=20,
                         headers={"Authorization": f"Bearer {getattr(config, 'openai_api_key', '')}"})
    reply.raise_for_status()
    ids = [m.get("id", "") for m in reply.json().get("data", [])]
    return [i for i in ids if re.match(r"(gpt-|o\d|chatgpt-)", i) and not NOT_TEXT.search(i)
            and "instruct" not in i and not _DATED.search(i)]


FETCH = {"gemini": _google, "claude": _anthropic, "openai": _openai}
KEYS = {"gemini": "gemini_api_key", "claude": "anthropic_api_key", "openai": "openai_api_key"}


def _order(provider: str, names: list[str]) -> list[str]:
    """Newest versions first, so the likeliest choice is at the top."""
    def version(name: str) -> tuple:
        numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", name)[:2]] or [0.0]
        return tuple(-n for n in numbers)
    return sorted(dict.fromkeys(names), key=lambda n: (version(n), n))


def catalogue(config: Any, refresh: bool = False) -> dict[str, dict]:
    """{provider: {"models": [...], "error": "", "has_key": bool}} for every provider."""
    try:
        cached = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = {}
    result, now, changed = {}, time.time(), False
    for provider, fetch in FETCH.items():
        has_key = bool(getattr(config, KEYS[provider], ""))
        entry = cached.get(provider) or {}
        fresh = entry.get("at", 0) > now - CACHE_SECONDS and entry.get("had_key") == has_key
        if has_key and (refresh or not fresh):
            try:
                entry = {"models": _order(provider, fetch(config)), "error": "", "at": now, "had_key": True}
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", "")
                entry = {"models": entry.get("models", []), "at": now, "had_key": True,
                         "error": f"could not list the models ({status or type(exc).__name__}) -- is the key right?"}
            changed = True
        elif not has_key:
            entry = {"models": [], "error": "", "at": now, "had_key": False}
        cached[provider] = entry
        result[provider] = {"models": entry.get("models", []), "error": entry.get("error", ""), "has_key": has_key}
    if changed:
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_text(json.dumps(cached, indent=1), encoding="utf-8")
        except OSError:
            pass
    return result
