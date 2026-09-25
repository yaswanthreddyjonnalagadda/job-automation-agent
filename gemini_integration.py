"""Gemini answers the form; Claude writes the resume and the cover letter.

The owner's split (25 September 2026), after the Anthropic account reached its usage limit. Everything that
answers a question on an application page -- planning a page, reading a screenshot, matching a dropdown choice,
screening answers -- goes to Google's Gemini. The documents (tailoring the resume, the cover letter, and the two
calls that prepare them) stay with Claude, and GeminiClient will not write one even if asked.

GeminiClient is a ClaudeClient with a different model behind it: the same prompts, the same reading of the JSON
that comes back, the same checks on it (a dropdown answer that is not one of the offered choices is dropped).
Only the transport differs, so a prompt fixed once is fixed for both.

What leaves the machine: with FORM_ANSWER_MODE=gemini every page the agent reads, with the facts it answers from
(the profile and the resume text), goes to Google; the resume and the cover letter are written by Claude from the
same facts. BEHAVIOUR.md says so. The key travels in a header, never in an address, and is never logged.
"""
from __future__ import annotations

import base64
import logging
import re
import time
from types import SimpleNamespace
from typing import Any, Optional

import requests

from claude_integration import ClaudeClient, ClaudeIntegrationError
from config import DEFAULT_GEMINI_BASE_URL, DEFAULT_GEMINI_MODEL

logger = logging.getLogger(__name__)

# Every public method of ClaudeClient belongs to exactly one side (tests/test_gemini_answers.py checks it), so a
# model call added later cannot end up at the wrong company by default.
ANSWERS = frozenset({"plan_page", "read_page", "choose_option", "answer_screening_questions"})
DOCUMENTS = frozenset({"tailor_resume", "generate_cover_letter", "structure_resume", "analyze_job"})

MIN_OUTPUT_TOKENS = 4_096       # Gemini counts its thinking in the limit: a 400-token answer would be cut off
MAX_RATE_LIMIT_WAIT = 65.0      # the longest wait for a per-minute limit that is worth making


class GeminiError(Exception):
    pass


class GeminiStatusError(GeminiError):
    """Google refused, and asking again will not change it (a bad key, a bad request, a blocked page)."""

    def __init__(self, message: str, status_code: int):
        super().__init__(f"Gemini ({status_code}): {message}")
        self.message = message
        self.status_code = status_code


class GeminiRateLimited(GeminiStatusError):
    """A per-minute or daily limit: worth waiting for."""


class GeminiConnectionError(GeminiError):
    """No answer, or a busy server (Gemini answers 503 often): worth asking again."""


class GeminiTimeout(GeminiConnectionError):
    pass


def key_problem(key: str) -> Optional[str]:
    """What is wrong with a key, in words, or None. Never contains the key. A key once pasted 4 characters
    short (missing its AIza) made every call fail with a message that did not say why."""
    if not key:
        return "GEMINI_API_KEY is not set"
    if key != key.strip() or key[0] in "\"'" or key[-1] in "\"'":
        return "GEMINI_API_KEY has spaces or quotes around it: put the bare key after the = sign"
    if len(key) != 39 or not key.startswith("AIza"):
        return (f"GEMINI_API_KEY does not look like a Google API key (they are 39 characters and start with "
                f"AIza; this one is {len(key)}): copy the whole key again from aistudio.google.com")
    return None


def _scrub(text: str, key: str) -> str:
    return text.replace(key, "[key]") if key else text


def _retry_delay(reply) -> float:
    """How long Google asked to wait ("7s"), else a short default; never more than MAX_RATE_LIMIT_WAIT."""
    try:
        for detail in (reply.json().get("error") or {}).get("details") or []:
            found = re.fullmatch(r"\s*([\d.]+)s\s*", str(detail.get("retryDelay") or ""))
            if found:
                return min(float(found.group(1)), MAX_RATE_LIMIT_WAIT)
    except (ValueError, AttributeError):
        pass
    return 2.0


def _google_message(reply) -> str:
    try:
        message = (reply.json().get("error") or {}).get("message")
    except (ValueError, AttributeError):
        message = None
    return str(message or reply.text or f"status {reply.status_code}").strip().splitlines()[0][:300]


def _parts(content) -> list[dict]:
    """A message's content in the shape Anthropic's client takes, as Gemini's parts."""
    if isinstance(content, str):
        return [{"text": content}]
    parts = []
    for block in content:
        if block.get("type") == "image":
            source = block["source"]
            parts.append({"inlineData": {"mimeType": source["media_type"], "data": source["data"]}})
        else:
            parts.append({"text": block.get("text", "")})
    return parts


def _as_reply(data: dict) -> SimpleNamespace:
    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason") or "no answer"
        raise GeminiStatusError(f"Gemini would not answer this page (blocked: {reason})", 400)
    candidate = candidates[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
    finish = candidate.get("finishReason") or ""
    if not text and finish not in ("", "STOP", "MAX_TOKENS"):
        raise GeminiStatusError(f"Gemini would not answer this page (blocked: {finish})", 400)
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=finish)


class _Messages:
    """`client.messages.create(...)` as ClaudeClient calls it, answered by Gemini."""

    def __init__(self, config: Any):
        self._config = config

    def create(self, *, model: str, max_tokens: int, system: str, messages: list[dict],
               timeout: Optional[float] = None, **_ignored: Any) -> SimpleNamespace:
        key = str(getattr(self._config, "gemini_api_key", "") or "")
        base = str(getattr(self._config, "gemini_base_url", "") or DEFAULT_GEMINI_BASE_URL).rstrip("/")
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "model" if m.get("role") == "assistant" else "user",
                          "parts": _parts(m["content"])} for m in messages],
            "generationConfig": {"temperature": 0, "maxOutputTokens": max(int(max_tokens), MIN_OUTPUT_TOKENS),
                                 "responseMimeType": "application/json"},
        }
        try:
            reply = requests.post(f"{base}/models/{model}:generateContent", json=payload,
                                  headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                                  timeout=timeout)
        except requests.exceptions.Timeout:
            raise GeminiTimeout("Gemini did not answer in time") from None
        except requests.exceptions.RequestException as exc:
            raise GeminiConnectionError(f"could not reach Gemini ({type(exc).__name__})") from None

        if reply.status_code == 429:
            time.sleep(_retry_delay(reply))       # the wait Google asked for, before the caller's own backoff
            raise GeminiRateLimited("quota or per-minute limit reached (wait, or check the key's quota "
                                    "at aistudio.google.com)", 429)
        if reply.status_code >= 500:
            raise GeminiConnectionError(f"Gemini is busy or down ({reply.status_code})")
        if reply.status_code >= 400:
            raise GeminiStatusError(_scrub(_google_message(reply), key), reply.status_code)
        try:
            data = reply.json()
        except ValueError:
            raise GeminiConnectionError("Gemini sent an answer that could not be read") from None
        return _as_reply(data)


class GeminiClient(ClaudeClient):
    """ClaudeClient's prompts and checks, with Gemini answering."""

    PROVIDER = "Gemini"
    RATE_LIMITED = GeminiRateLimited
    STATUS_ERROR = GeminiStatusError
    CONNECTION_ERROR = GeminiConnectionError
    TIMEOUT_ERROR = GeminiTimeout

    def __init__(self, config: Any):
        self._config = config
        self._client = SimpleNamespace(messages=_Messages(config))

    @property
    def _model(self) -> str:
        return str(getattr(self._config, "gemini_model", "") or DEFAULT_GEMINI_MODEL)


def _refuse(name: str):
    def refuse(self, *args: Any, **kwargs: Any):
        raise ClaudeIntegrationError(f"{name} is written by Claude, not Gemini: nothing was sent")
    refuse.__name__ = name
    return refuse


for _name in DOCUMENTS:                 # the resume and the cover letter never go to Google from here
    setattr(GeminiClient, _name, _refuse(_name))


class GeminiBrain:
    """What the agent is given when Gemini answers: the questions go to Gemini, the documents to Claude.

    Like session_planner.SessionPlanner it stands in for the client the agent already uses; anything it does not
    name goes on to the documents client.
    """

    def __init__(self, config: Any, documents: Any = None, answers: Any = None):
        if answers is None:
            if not getattr(config, "gemini_api_key", ""):
                raise RuntimeError("FORM_ANSWER_MODE=gemini needs GEMINI_API_KEY in .env "
                                   "(make a key at aistudio.google.com)")
            problem = key_problem(config.gemini_api_key)
            if problem:
                logger.warning("GEMINI: %s", problem)
            answers = GeminiClient(config)
        self._answers = answers
        self._documents = documents if documents is not None else ClaudeClient(config)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return getattr(self._answers if name in ANSWERS else self._documents, name)
