"""Gemini answers the form; Claude writes the resume and the cover letter.

The owner's split (25 September 2026), after the Anthropic account reached its usage limit. Everything that
answers a question on an application page -- planning a page, reading a screenshot, matching a dropdown choice,
screening answers -- goes to Google's Gemini. The documents (tailoring the resume, the cover letter, and the two
calls that prepare them) stay with Claude, and GeminiClient will not write one even if asked.

GeminiClient is a ClaudeClient with a different model behind it: the same prompts, the same reading of the JSON
that comes back, the same checks on it (a dropdown answer that is not one of the offered choices is dropped).
Only the transport differs, so a prompt fixed once is fixed for both.

Many models, one allowance each (29 September 2026). Google's free tier counts requests per model: one Flash
model gives about 20 a day, and the agent used to ask only that one, so it ran out after two or three
applications while the other models' allowances went unused. Now every call has a ladder of models
(config.DEFAULT_GEMINI_PAGE_MODELS / DEFAULT_GEMINI_QUICK_MODELS): a page plan or an open answer starts with the
strongest models; a single dropdown choice or short question starts with the Flash Lite models, which allow about
500 a day. A model that says its day is spent rests until Google's midnight reset, and that is written to
data/_model_quota.json (model_ladder.py), so the next application does not ask it again. A model Google does not know rests for a
week. A per-minute limit moves straight on to the next model; only when every model is paused for the minute is
the wait made. The same question asked twice in one run is answered once.

What leaves the machine: with FORM_ANSWER_MODE=gemini every page the agent reads, with the facts it answers from
(the profile and the resume text), goes to Google; the resume and the cover letter are written by Claude from the
same facts. BEHAVIOUR.md says so. The key travels in a header, never in an address, and is never logged.
"""
from __future__ import annotations

import logging
import re
import time
from types import SimpleNamespace
from typing import Any, Optional

import requests

import model_ladder
from claude_integration import ClaudeClient, ClaudeIntegrationError
from config import DEFAULT_GEMINI_BASE_URL, DEFAULT_GEMINI_MODEL
from model_ladder import Failed, Missing, NoFreeTier, Paused, Spent

logger = logging.getLogger(__name__)

# Every public method of ClaudeClient belongs to exactly one side (tests/test_gemini_answers.py checks it), so a
# model call added later cannot end up at the wrong company by default.
ANSWERS = frozenset({"plan_page", "read_page", "choose_option", "answer_screening_questions", "answer_single_question"})
DOCUMENTS = frozenset({"tailor_resume", "generate_cover_letter"})

MIN_OUTPUT_TOKENS = 4_096       # Gemini counts its thinking in the limit: a 400-token answer would be cut off
GEMMA_MAX_OUTPUT_TOKENS = 8_192
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
    """Every model is paused for the minute: worth waiting for."""


class GeminiDailyLimit(GeminiStatusError):
    """Every model's allowance for the day is spent. Not worth waiting for: asking again only waits a minute
    and fails, three times over, on every question (KBI, 28 September: 3 minutes a question)."""


class GeminiConnectionError(GeminiError):
    """No answer, or a busy server (Gemini answers 503 often): worth asking again."""


class GeminiTimeout(GeminiConnectionError):
    pass


_KEY_CHARACTERS = re.compile(r"[A-Za-z0-9._-]+")


# --- which models ------------------------------------------------------------------------------------------

_resting = model_ladder.resting
_pacific = model_ladder._pacific
next_reset = model_ladder.next_reset


def models_for(config: Any, tier: str) -> list[str]:
    """The owner's Gemini ladder for one kind of call. A config without ladders (older settings) has one model."""
    return model_ladder.ladder_for(config, "gemini", tier) or [DEFAULT_GEMINI_MODEL]


def usage_today(config: Any) -> list[dict]:
    """Each Gemini model the agent may use, with today's calls and whether it is resting -- for the dashboard."""
    page, quick = models_for(config, "page"), models_for(config, "quick")
    rows = model_ladder.usage(page + quick)
    for row in rows:
        row["page"], row["quick"] = row["model"] in page, row["model"] in quick
    return rows


# --- reading Google's replies --------------------------------------------------------------------------------

def _no_free_tier(reply) -> bool:
    """A 429 saying the limit itself is 0: the model is not on this key's free tier at all."""
    try:
        return bool(re.search(r"limit:\s*0\b", reply.text or ""))
    except Exception:
        return False


def _daily_quota(reply) -> bool:
    """A 429 whose quota is counted per day (Google's QuotaFailure names it, e.g. '...PerDay...')."""
    try:
        return "perday" in (reply.text or "").replace("_", "").lower()
    except Exception:
        return False


def key_problem(key: str) -> Optional[str]:
    """What is wrong with a key, in words, or None. Never contains the key. A key once pasted 4 characters
    short (missing its AIza) made every call fail with a message that did not say why. Google has two kinds:
    the long-standing one (AIza and 35 more characters) and a newer one that starts AQ. -- the owner's is the
    newer, and the first version of this check, which knew only the first, called a working key wrong."""
    if not key:
        return "GEMINI_API_KEY is not set"
    if key != key.strip() or key[0] in "\"'" or key[-1] in "\"'":
        return "GEMINI_API_KEY has spaces or quotes around it: put the bare key after the = sign"
    classic = key.startswith("AIza") and len(key) == 39
    newer = key.startswith("AQ.") and len(key) >= 40
    if not (classic or newer) or not _KEY_CHARACTERS.fullmatch(key):
        return (f"GEMINI_API_KEY does not look like a Google API key (they start with AIza and are 39 characters, "
                f"or start with AQ.; this one is {len(key)} characters): copy the whole key again from "
                f"aistudio.google.com")
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


def _payload(model: str, system: str, messages: list[dict], max_tokens: int, json_reply: bool) -> dict:
    contents = [{"role": "model" if m.get("role") == "assistant" else "user", "parts": _parts(m["content"])}
                for m in messages]
    settings: dict[str, Any] = {"temperature": 0, "maxOutputTokens": max(int(max_tokens), MIN_OUTPUT_TOKENS)}
    if model.startswith("gemma"):
        # Gemma takes no separate instructions and no JSON switch: the instructions lead the message, and
        # the JSON is read out of the text as it is for every model.
        if system and contents:
            contents[0]["parts"].insert(0, {"text": system + "\n\n"})
        settings["maxOutputTokens"] = min(settings["maxOutputTokens"], GEMMA_MAX_OUTPUT_TOKENS)
        return {"contents": contents, "generationConfig": settings}
    if json_reply:
        settings["responseMimeType"] = "application/json"
    return {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents, "generationConfig": settings}


class _Messages:
    """`client.messages.create(...)` as ClaudeClient calls it, answered by the first Gemini model able to.

    `model` is ignored: the owner's ladder for the current tier decides. json_reply=False is for the
    documents, which come back as prose."""

    def __init__(self, config: Any, json_reply: bool = True):
        self._config = config
        self._json_reply = json_reply
        self.tier = "page"

    def create(self, *, model: str, max_tokens: int, system: str, messages: list[dict],
               timeout: Optional[float] = None, **_ignored: Any) -> SimpleNamespace:
        key = str(getattr(self._config, "gemini_api_key", "") or "")
        base = str(getattr(self._config, "gemini_base_url", "") or DEFAULT_GEMINI_BASE_URL).rstrip("/")

        def send(name: str) -> SimpleNamespace:
            try:
                reply = requests.post(f"{base}/models/{name}:generateContent",
                                      json=_payload(name, system, messages, max_tokens, self._json_reply),
                                      headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                                      timeout=timeout)
            except requests.exceptions.Timeout:
                raise Failed(GeminiTimeout("Gemini did not answer in time")) from None
            except requests.exceptions.RequestException as exc:
                raise Failed(GeminiConnectionError(f"could not reach Gemini ({type(exc).__name__})")) from None
            if reply.status_code == 429:
                if _no_free_tier(reply):
                    raise NoFreeTier(GeminiStatusError(f"{name} has no free allowance on this key", 429))
                if _daily_quota(reply):
                    raise Spent(GeminiDailyLimit("the daily allowance is used up", 429))
                raise Paused(GeminiRateLimited("per-minute limit reached", 429), wait=_retry_delay(reply))
            if reply.status_code >= 500:
                raise Failed(GeminiConnectionError(f"Gemini is busy or down ({reply.status_code})"))
            if reply.status_code >= 400:
                message = _scrub(_google_message(reply), key)
                if reply.status_code == 404:        # a model Google has retired, or never gave this key
                    raise Missing(GeminiStatusError(
                        message + f" -- {name} is not available to this key; the next model is used "
                        "(choose the models on Settings, or GEMINI_MODEL / GEMINI_PAGE_MODELS in .env)", 404))
                raise Failed(GeminiStatusError(message, reply.status_code))
            try:
                data = reply.json()
            except ValueError:
                raise Failed(GeminiConnectionError("Gemini sent an answer that could not be read")) from None
            return _as_reply(data)

        return model_ladder.climb(
            models_for(self._config, self.tier), send, label="GEMINI", tier=self.tier,
            all_spent=lambda: GeminiDailyLimit(
                "every model's daily allowance is used up -- they come back at midnight Pacific time "
                "(or add billing at aistudio.google.com)", 429),
            all_paused=lambda: GeminiRateLimited(
                "every model is at its per-minute limit (waited, asking again)", 429))


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
        self._remembered: dict = {}

    @property
    def _model(self) -> str:
        return str(getattr(self._config, "gemini_model", "") or DEFAULT_GEMINI_MODEL)


model_ladder.put_on_ladders(GeminiClient, ClaudeClient)


def _refuse(name: str):
    def refuse(self, *args: Any, **kwargs: Any):
        raise ClaudeIntegrationError(f"{name} is written by Claude, not Gemini: nothing was sent")
    refuse.__name__ = name
    return refuse


for _name in DOCUMENTS:                 # the resume and the cover letter never go to Google from here
    setattr(GeminiClient, _name, _refuse(_name))


class GeminiDocumentClient(GeminiClient):
    """Gemini client allowed to write documents (resume, cover letter).

    Used only as a *fallback* when Claude is unavailable.  It asks for prose (no JSON constraint) because the
    document prompts return prose, not structured data, and climbs the page ladder.

    GeminiClient has the DOCUMENTS methods replaced with _refuse stubs via
    setattr, so subclasses inherit them.  We restore the original ClaudeClient
    implementations here so tailor_resume / generate_cover_letter actually run.

    Unlike the base GeminiClient (which retries page-planning calls with long
    waits), this client fails fast on connection errors so the provider loop in
    prepare_materials can immediately try the next provider (OpenAI, local resume)
    without making the user wait 30-90 seconds per attempt.
    """

    def __init__(self, config: Any):
        self._config = config
        self._client = SimpleNamespace(messages=_Messages(config, json_reply=False))
        self._remembered = {}

    def _call(self, *, system: str, user_message: str, max_tokens: int = 2000) -> str:
        """Single-attempt call -- fails fast so the provider fallback loop moves on."""
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_message}],
                timeout=self._config.claude_request_timeout,
            )
            return "".join(block.text for block in response.content if block.type == "text")
        except (self.RATE_LIMITED, self.CONNECTION_ERROR, self.TIMEOUT_ERROR) as exc:
            raise ClaudeIntegrationError(
                f"Gemini unavailable for document tailoring ({exc}) -- skipping to next provider"
            ) from exc
        except self.STATUS_ERROR as exc:
            raise ClaudeIntegrationError(
                f"Gemini document error (status={exc.status_code}): {exc.message}"
            ) from exc


# Restore the original ClaudeClient implementations for every DOCUMENTS method:
# setattr on the parent class makes those _refuse stubs inherited, so we pin
# the real implementations directly onto this subclass.
for _name in DOCUMENTS:
    setattr(GeminiDocumentClient, _name, getattr(ClaudeClient, _name))


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
