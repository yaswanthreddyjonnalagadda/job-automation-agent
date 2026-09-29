"""OpenAI answers the form, or writes the resume and cover letter -- whichever the owner chose on Settings.

OpenAIClient is a ClaudeClient with OpenAI behind it: the same prompts, the same reading of the JSON that comes
back, the same checks on it. Only the transport differs, so a prompt fixed once is fixed for every provider.

The transport speaks OpenAI's Chat Completions API over plain HTTPS (no `openai` package: it was never installed
on the owner's computer, so the old OpenAI fallback could not run at all). Each call climbs the owner's ladder of
OpenAI models (model_ladder.py): a model at its per-minute limit hands over to the next, and an account out of
credit ("insufficient_quota") rests an hour. The key travels in a header and is never logged.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, Optional

import requests

import model_ladder
from claude_integration import ClaudeClient
from model_ladder import Failed, Missing, NoCredit, Paused

logger = logging.getLogger(__name__)

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o"
RATE_LIMIT_WAIT = 20.0          # when OpenAI does not say how long


class OpenAIError(Exception):
    pass


class OpenAIStatusError(OpenAIError):
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(f"OpenAI ({status_code}): {message}")
        self.status_code = status_code
        self.message = message


class OpenAIRateLimited(OpenAIStatusError):
    """Every model is at its per-minute limit: worth waiting for."""


class OpenAINoCredit(OpenAIStatusError):
    """The account has no credit left: not worth asking again today."""


class OpenAIConnectionError(OpenAIError):
    pass


class OpenAITimeout(OpenAIConnectionError):
    pass


def _message(reply) -> tuple[str, str]:
    """(OpenAI's error code, its message) from an error reply."""
    try:
        error = reply.json().get("error") or {}
        return str(error.get("code") or error.get("type") or ""), str(error.get("message") or "")[:300]
    except (ValueError, AttributeError):
        return "", (reply.text or f"status {reply.status_code}")[:300]


def _wait(reply) -> float:
    try:
        return min(float(reply.headers.get("retry-after") or RATE_LIMIT_WAIT), model_ladder.MAX_WAIT)
    except (TypeError, ValueError):
        return RATE_LIMIT_WAIT


def _content(content) -> Any:
    """Anthropic-shaped content (text, or text and images) in Chat Completions' shape."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if block.get("type") == "image":
            source = block["source"]
            parts.append({"type": "image_url",
                          "image_url": {"url": f"data:{source['media_type']};base64,{source['data']}"}})
        else:
            parts.append({"type": "text", "text": block.get("text", "")})
    return parts


class _OpenAIMessages:
    """`client.messages.create(...)` as ClaudeClient calls it, answered by the first OpenAI model able to."""

    def __init__(self, config: Any, json_reply: bool = True):
        self._config = config
        self._json_reply = json_reply
        self.tier = "page"

    def create(self, *, model: str, max_tokens: int, system: str, messages: list[dict],
               timeout: Optional[float] = None, **_ignored: Any) -> SimpleNamespace:
        key = str(getattr(self._config, "openai_api_key", "") or "")
        base = str(getattr(self._config, "openai_base_url", "") or DEFAULT_OPENAI_BASE_URL).rstrip("/")
        chat = [{"role": "system", "content": system}] + [
            {"role": m.get("role", "user"), "content": _content(m["content"])} for m in messages]

        def send(name: str) -> SimpleNamespace:
            # max_completion_tokens and no temperature: the reasoning models refuse max_tokens and a temperature.
            body: dict[str, Any] = {"model": name, "messages": chat, "max_completion_tokens": int(max_tokens)}
            if self._json_reply:
                body["response_format"] = {"type": "json_object"}
            try:
                reply = requests.post(f"{base}/chat/completions", json=body, timeout=timeout or 120.0,
                                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            except requests.exceptions.Timeout:
                raise Failed(OpenAITimeout("OpenAI did not answer in time")) from None
            except requests.exceptions.RequestException as exc:
                raise Failed(OpenAIConnectionError(f"could not reach OpenAI ({type(exc).__name__})")) from None
            if reply.status_code == 429:
                code, text = _message(reply)
                if code == "insufficient_quota":
                    raise NoCredit(OpenAINoCredit("the account has no credit left (platform.openai.com/billing)",
                                                  429))
                raise Paused(OpenAIRateLimited(text or "rate limit reached", 429), wait=_wait(reply))
            if reply.status_code >= 500:
                raise Failed(OpenAIConnectionError(f"OpenAI is busy or down ({reply.status_code})"))
            if reply.status_code >= 400:
                code, text = _message(reply)
                error = OpenAIStatusError(text.replace(key, "[key]") if key else text, reply.status_code)
                if reply.status_code == 404 or code == "model_not_found":
                    raise Missing(error)
                raise Failed(error)
            try:
                choice = (reply.json().get("choices") or [{}])[0]
            except ValueError:
                raise Failed(OpenAIConnectionError("OpenAI sent an answer that could not be read")) from None
            text = ((choice.get("message") or {}).get("content")) or ""
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)],
                                   stop_reason=choice.get("finish_reason") or "stop")

        ladder = model_ladder.ladder_for(self._config, "openai", self.tier) or [DEFAULT_OPENAI_MODEL]
        return model_ladder.climb(
            ladder, send, label="OPENAI", tier=self.tier,
            all_spent=lambda: OpenAINoCredit("no OpenAI model can answer: the account has no credit left", 429),
            all_paused=lambda: OpenAIRateLimited("every OpenAI model is at its per-minute limit", 429))


class OpenAIClient(ClaudeClient):
    """ClaudeClient's prompts and checks, answered by OpenAI."""

    PROVIDER = "OpenAI"
    RATE_LIMITED = OpenAIRateLimited
    STATUS_ERROR = OpenAIStatusError
    CONNECTION_ERROR = OpenAIConnectionError
    TIMEOUT_ERROR = OpenAITimeout

    def __init__(self, config: Any, json_reply: bool = True):
        self._config = config
        self._client = SimpleNamespace(messages=_OpenAIMessages(config, json_reply=json_reply))
        self._remembered: dict = {}

    @property
    def _model(self) -> str:
        return str(getattr(self._config, "openai_model", "") or DEFAULT_OPENAI_MODEL)


model_ladder.put_on_ladders(OpenAIClient, ClaudeClient)


class OpenAIDocumentClient(OpenAIClient):
    """OpenAI writing the resume and cover letter: prose, not JSON."""

    def __init__(self, config: Any):
        super().__init__(config, json_reply=False)
