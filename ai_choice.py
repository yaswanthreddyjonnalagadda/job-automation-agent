"""Which AI answers the forms and which writes the documents: the owner's two choices on Settings, in one place.

Answering (FORM_ANSWER_MODE): "gemini", "claude" or "openai" answer the pages, each climbing the owner's ladders
of that provider's models (model_ladder.py); "profile" answers from the saved profile alone. Writing
(RESUME_WRITER, RESUME_WRITER_FALLBACK): the provider and model that write the resume and cover letter, and the
one that writes them if the first cannot. The two are separate on purpose: answering spends many small requests
on every application, writing a few large ones once per job, and neither may use up the other's allowance.
"""
from __future__ import annotations

import dataclasses
import logging
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)

ANSWERS = frozenset({"plan_page", "read_page", "choose_option", "answer_screening_questions", "answer_single_question"})
DOCUMENTS = frozenset({"tailor_resume", "generate_cover_letter"})

PROVIDERS = {"claude": "Anthropic Claude", "gemini": "Google Gemini", "openai": "OpenAI"}
KEYS = {"claude": "anthropic_api_key", "gemini": "gemini_api_key", "openai": "openai_api_key"}
DEFAULT_ORDER = ("claude", "gemini", "openai")


def has_key(config: Any, provider: str) -> bool:
    return bool(getattr(config, KEYS.get(provider, ""), ""))


def parse_writer(value: str) -> tuple[str, str]:
    """'gemini:gemini-3.5-flash' -> ('gemini', 'gemini-3.5-flash'); a bare provider has no model."""
    provider, _, model = (value or "").strip().partition(":")
    provider = provider.strip().lower()
    return (provider, model.strip()) if provider in PROVIDERS else ("", "")


def _with(config: Any, **values: Any) -> Any:
    if dataclasses.is_dataclass(config):
        return dataclasses.replace(config, **values)
    return SimpleNamespace(**{**vars(config), **values})


def writer_client(provider: str, model: str, config: Any):
    """A client that writes documents with this provider, and with this one model when one is named."""
    if provider == "claude":
        from claude_integration import ClaudeClient
        return ClaudeClient(_with(config, anthropic_model=model) if model else config)
    if provider == "gemini":
        from gemini_integration import GeminiDocumentClient
        return GeminiDocumentClient(_with(config, gemini_model=model, gemini_page_models=(model,)) if model else config)
    if provider == "openai":
        from openai_integration import OpenAIDocumentClient
        return OpenAIDocumentClient(_with(config, openai_model=model, openai_page_models=(model,))
                                    if model else config)
    raise ValueError(f"Unknown writer: {provider!r}")


def writer_label(provider: str, model: str) -> str:
    return f"{PROVIDERS[provider].split()[-1]} ({model})" if model else PROVIDERS[provider].split()[-1]


def writers(config: Any) -> list[tuple[str, Any]]:
    """The owner's writers in order, each as (label, client); only providers with a key."""
    chosen = [parse_writer(v) for v in (getattr(config, "resume_writer", ""), getattr(config, "resume_writer_fallback", ""))]
    chosen = [c for c in chosen if c[0]]
    if not chosen:
        chosen = [(p, "") for p in DEFAULT_ORDER]
    found = []
    for provider, model in dict.fromkeys(chosen):
        if not has_key(config, provider):
            if getattr(config, "resume_writer", ""):
                logger.warning("WRITER: %s was chosen but has no key in Settings -- skipped",
                               writer_label(provider, model))
            continue
        try:
            found.append((writer_label(provider, model), writer_client(provider, model, config)))
        except Exception as exc:
            logger.warning("WRITER: could not set up %s (%s) -- skipped", writer_label(provider, model), exc)
    return found


class Writers:
    """The documents, written by the first of the owner's writers that can."""

    def __init__(self, found: list[tuple[str, Any]]):
        self._found = found

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        if not self._found:
            from claude_integration import ClaudeIntegrationError
            raise ClaudeIntegrationError("no writer has a key in Settings -- your own resume is attached as it is")
        if name not in DOCUMENTS:
            return getattr(self._found[0][1], name)

        def first_that_works(*args: Any, **kwargs: Any):
            error: Exception | None = None
            for label, client in self._found:
                try:
                    return getattr(client, name)(*args, **kwargs)
                except Exception as exc:
                    logger.warning("WRITER: %s could not write it (%s) -- trying the next", label, exc)
                    error = exc
            raise error  # type: ignore[misc]
        return first_that_works


def answer_client(config: Any, kind: str):
    """The client that answers the pages for FORM_ANSWER_MODE `kind`."""
    if kind == "gemini":
        from gemini_integration import GeminiClient
        return GeminiClient(config)
    if kind == "openai":
        from openai_integration import OpenAIClient
        return OpenAIClient(config)
    from claude_answers import ClaudeAnswerClient
    return ClaudeAnswerClient(config)


class Brain:
    """What the agent is given: the questions go to the answering client, the documents to the writers."""

    def __init__(self, answers: Any, documents: Any):
        self._answers = answers
        self._documents = documents

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return getattr(self._answers if name in ANSWERS else self._documents, name)
