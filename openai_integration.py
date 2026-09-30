"""OpenAI client for resume tailoring — used as a fallback when Claude and Gemini are unavailable.

Inherits ClaudeClient's prompts and checks verbatim; only the transport differs.
Requires: pip install openai  (already in most setups)
"""
from __future__ import annotations

import logging
import time
from types import SimpleNamespace
from typing import Any

from claude_integration import ClaudeClient, ClaudeIntegrationError

logger = logging.getLogger(__name__)


# ── sentinel errors ────────────────────────────────────────────────────────────

class OpenAIError(Exception):
    pass


class OpenAIRateLimited(OpenAIError):
    pass


class OpenAIStatusError(OpenAIError):
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class OpenAIConnectionError(OpenAIError):
    pass


class OpenAITimeout(OpenAIConnectionError):
    pass


# ── thin transport shim ─────────────────────────────────────────────────────────

class _OpenAIMessages:
    """`client.messages.create(...)` shape, answered by OpenAI."""

    def __init__(self, config: Any):
        self._config = config

    def create(self, *, model: str, max_tokens: int, system: str,
               messages: list[dict], timeout: float | None = None,
               **_ignored: Any) -> SimpleNamespace:
        try:
            import openai
        except ImportError:
            raise ClaudeIntegrationError(
                "openai package is not installed -- run: pip install openai"
            ) from None

        key = str(getattr(self._config, "openai_api_key", "") or "")
        if not key:
            raise ClaudeIntegrationError("OPENAI_API_KEY is not set")

        client = openai.OpenAI(api_key=key, timeout=timeout or 60.0)
        oai_messages = [{"role": "system", "content": system}]
        for m in messages:
            content = m["content"]
            if isinstance(content, list):
                # Strip image blocks — OpenAI vision is not used here
                text = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
            else:
                text = str(content)
            oai_messages.append({"role": m.get("role", "user"), "content": text})

        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=oai_messages,
            )
        except openai.RateLimitError as exc:
            raise OpenAIRateLimited(str(exc)) from exc
        except openai.APIStatusError as exc:
            raise OpenAIStatusError(str(exc), exc.status_code) from exc
        except openai.APIConnectionError as exc:
            raise OpenAIConnectionError(str(exc)) from exc
        except openai.APITimeoutError as exc:
            raise OpenAITimeout(str(exc)) from exc

        text = response.choices[0].message.content or ""
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            stop_reason=response.choices[0].finish_reason or "stop",
        )


class OpenAIDocumentClient(ClaudeClient):
    """ClaudeClient's prompts, answered by OpenAI — for resume tailoring only."""

    PROVIDER = "OpenAI"
    RATE_LIMITED = OpenAIRateLimited
    STATUS_ERROR = OpenAIStatusError
    CONNECTION_ERROR = OpenAIConnectionError
    TIMEOUT_ERROR = OpenAITimeout

    def __init__(self, config: Any):
        self._config = config
        self._client = SimpleNamespace(messages=_OpenAIMessages(config))

    @property
    def _model(self) -> str:
        return str(getattr(self._config, "openai_model", "") or "gpt-4o")
