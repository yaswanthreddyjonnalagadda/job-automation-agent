"""Claude answering the forms, climbing the owner's ladder of Claude models (model_ladder.py).

ClaudeClient keeps its one model (ANTHROPIC_MODEL) for the documents; when the owner has Claude answer the
forms, each call instead goes to the first model of the ladder chosen on Settings that can answer: a model at
its rate limit hands over to the next, a model the key cannot use is skipped for a week, and an account out of
credit rests an hour instead of being asked on every question.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional

import anthropic

import model_ladder
from claude_integration import ClaudeClient, ClaudeIntegrationError
from model_ladder import Failed, Missing, NoCredit, Paused

RATE_LIMIT_WAIT = 20.0


class ClaudeNoCredit(ClaudeIntegrationError):
    pass


class ClaudeAllPaused(ClaudeIntegrationError):
    pass


def _wait(exc: Exception) -> float:
    try:
        return min(float(exc.response.headers.get("retry-after")), model_ladder.MAX_WAIT)
    except Exception:
        return RATE_LIMIT_WAIT


class _ClaudeLadder:
    """`messages.create(...)`, sent to the first Claude model of the current tier's ladder that can answer."""

    def __init__(self, config: Any, messages: Any):
        self._config = config
        self._messages = messages
        self.tier = "page"

    def create(self, *, model: str, timeout: Optional[float] = None, **kwargs: Any) -> Any:
        def send(name: str) -> Any:
            try:
                return self._messages.create(model=name, timeout=timeout, **kwargs)
            except anthropic.RateLimitError as exc:
                raise Paused(exc, wait=_wait(exc)) from None
            except anthropic.NotFoundError as exc:
                raise Missing(exc) from None
            except anthropic.APIStatusError as exc:
                if "credit balance" in str(exc).lower() or "billing" in str(exc).lower():
                    raise NoCredit(ClaudeNoCredit("the Claude account is out of credit")) from None
                raise Failed(exc) from None
            except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
                raise Failed(exc) from None

        ladder = model_ladder.ladder_for(self._config, "claude", self.tier) or [model]
        return model_ladder.climb(
            ladder, send, label="CLAUDE", tier=self.tier,
            all_spent=lambda: ClaudeNoCredit("no Claude model can answer: the account is out of credit "
                                             "(console.anthropic.com, Plans & Billing)"),
            all_paused=lambda: ClaudeAllPaused("every Claude model is at its rate limit"))


class ClaudeAnswerClient(ClaudeClient):
    """ClaudeClient's prompts and checks, answered by the owner's ladder of Claude models."""

    RATE_LIMITED = (anthropic.RateLimitError, ClaudeAllPaused)

    def __init__(self, config: Any, client: Any = None):
        self._config = config
        real = client if client is not None else anthropic.Anthropic(api_key=config.anthropic_api_key)
        self._client = SimpleNamespace(messages=_ClaudeLadder(config, real.messages))
        self._remembered: dict = {}


model_ladder.put_on_ladders(ClaudeAnswerClient, ClaudeClient)
