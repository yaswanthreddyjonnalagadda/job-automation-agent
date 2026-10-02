"""Which model answers a call: the first in its ladder that is not resting. One place, for every provider.

Every provider meters its models separately (Google's free tier gives each model its own daily allowance; Anthropic
and OpenAI limit each model per minute), so a call is given a ladder -- an ordered list of models -- and climbs
it. A model that says it is spent rests (until Google's daily reset, or an hour for an account out of credit), a
model the key cannot use rests a week, and both are written to data/_model_quota.json so every run on this
computer skips them. A per-minute limit moves straight on; the wait is made only when every model is paused.

Two kinds of call, two ladders (the owner's split, 29 September 2026):
  page  -- a whole page, a written answer, screening answers: the stronger models first;
  quick -- one dropdown choice, one short question, a screenshot: the models with big allowances first.
The ladders for each provider are the owner's, set on Settings (config.answer_models).

A provider's transport says what happened to one request by raising one of the Skip errors below; climb() does
the rest. Nothing here knows any provider's wire format.
"""
from __future__ import annotations

import functools
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from config import GEMINI_QUOTA_RESET_TZ, GEMINI_QUOTA_RESET_UTC_OFFSET_HOURS

logger = logging.getLogger(__name__)

QUOTA_FILE = Path("data/_model_quota.json")
MAX_WAIT = 65.0
MISSING_REST = 7 * 24 * 3600.0
NO_CREDIT_REST = 3600.0

# Which ladder each ClaudeClient method climbs, and which are remembered for the rest of the run.
TIERS = {"plan_page": "page", "answer_screening_questions": "page", "_open_answer": "page",
         "read_page": "quick", "choose_option": "quick", "answer_single_question": "quick"}
REMEMBERED = frozenset({"choose_option", "answer_single_question"})


# --- what a transport reports about one request ---------------------------------------------------------------

class Skip(Exception):
    """This model cannot answer now; try the next. `error` is what the caller would raise if none can."""
    why = "failed"

    def __init__(self, error: Exception, wait: float = 0.0):
        super().__init__(str(error))
        self.error, self.wait = error, wait


class Spent(Skip):
    why = "daily"          # its allowance is used up until the provider's reset


class NoCredit(Skip):
    why = "no credit"      # the account is out of credit: an hour, then look again


class NoFreeTier(Skip):
    why = "no free tier"   # a limit of 0 on this key: it needs billing


class Paused(Skip):
    why = "minute"         # a per-minute limit: `wait` seconds


class Missing(Skip):
    why = "missing"        # the key cannot use this model


class Failed(Skip):
    why = "failed"         # busy, timed out, refused: not remembered, just the next model


# --- the book ---------------------------------------------------------------------------------------------------

def _pacific(now: float) -> datetime:
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(GEMINI_QUOTA_RESET_TZ)
    except Exception:
        zone = timezone(timedelta(hours=GEMINI_QUOTA_RESET_UTC_OFFSET_HOURS))
    return datetime.fromtimestamp(now, zone)


def next_reset(now: Optional[float] = None) -> float:
    """When Google's free allowances start again: the next midnight in its Pacific time."""
    here = _pacific(time.time() if now is None else now)
    return (here + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _today(now: Optional[float] = None) -> str:
    return _pacific(time.time() if now is None else now).date().isoformat()


def _book() -> dict:
    try:
        data = json.loads(QUOTA_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(book: dict) -> None:
    try:
        QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
        QUOTA_FILE.write_text(json.dumps(book, indent=1, sort_keys=True), encoding="utf-8")
    except OSError as exc:
        logger.debug("Could not save the model quota book: %s", exc)


def rest(model: str, until: float, why: str) -> None:
    book = _book()
    book.setdefault(model, {}).update(rest_until=until, why=why)
    _write(book)


def wake(model: str) -> None:
    book = _book()
    if model in book:
        book[model].pop("rest_until", None)
        book[model].pop("why", None)
        _write(book)


def resting(model: str, now: Optional[float] = None) -> tuple[str, float]:
    """(why, until) while the model rests, else ("", 0)."""
    entry = _book().get(model) or {}
    until = float(entry.get("rest_until") or 0)
    if until > (time.time() if now is None else now):
        return str(entry.get("why") or "resting"), until
    return "", 0.0


def count(model: str) -> None:
    book = _book()
    entry = book.setdefault(model, {})
    today = _today()
    if entry.get("day") != today:
        entry["day"], entry["calls"] = today, 0
    entry["calls"] = int(entry.get("calls") or 0) + 1
    _write(book)


def _when(moment: float) -> str:
    """'3:00 AM' today or tomorrow, 'Tue 3:00 AM' later in the week, '6 Oct' after that -- this computer's time."""
    at, now = datetime.fromtimestamp(moment), datetime.now()
    clock = at.strftime("%I:%M %p").lstrip("0")
    days = (at.date() - now.date()).days
    if days <= 1:
        return clock
    return f"{at:%a} {clock}" if days < 7 else f"{at.day} {at:%b}"


def usage(models: list[str]) -> list[dict]:
    """Today's calls and state of each model, for the dashboard."""
    book, now, today = _book(), time.time(), _today()
    rows = []
    for model in dict.fromkeys(models):
        entry = book.get(model) or {}
        why, until = resting(model, now)
        rows.append({"model": model, "calls": int(entry.get("calls") or 0) if entry.get("day") == today else 0,
                     "why": why, "until": _when(until) if why else ""})
    return rows


# The single model a provider used before ladders, still the ladder when none is set.
SINGLE_MODEL = {"gemini": "gemini_model", "claude": "anthropic_model", "openai": "openai_model"}


def ladder_for(config: Any, provider: str, tier: str) -> list[str]:
    """The owner's ordered models for one provider and one kind of call (config.<provider>_<tier>_models)."""
    listed = [m for m in (getattr(config, f"{provider}_{tier}_models", None) or ()) if m]
    if not listed:
        single = str(getattr(config, SINGLE_MODEL.get(provider, ""), "") or "")
        listed = [single] if single else []
    return list(dict.fromkeys(listed))


# --- climbing -----------------------------------------------------------------------------------------------------

def _rest_for(skip: Skip) -> float:
    return {"daily": next_reset(), "no credit": time.time() + NO_CREDIT_REST,
            "no free tier": time.time() + MISSING_REST, "missing": time.time() + MISSING_REST,
            "minute": time.time() + skip.wait}.get(skip.why, 0.0)


def climb(ladder: list[str], send: Callable[[str], Any], *, label: str, tier: str,
          all_spent: Callable[[], Exception], all_paused: Callable[[], Exception]) -> Any:
    """The first answer from the ladder. `send(model)` returns an answer or raises a Skip."""
    paused: dict[str, float] = {}
    spent, failure = False, None
    for model in ladder:
        why, until = resting(model)
        if why == "minute":
            paused[model] = max(until - time.time(), 0.0)
            continue
        if why:
            spent = spent or why in ("daily", "no credit")
            continue
        try:
            answer = send(model)
        except Skip as skip:
            until = _rest_for(skip)
            if until:
                rest(model, until, skip.why)
            if skip.why == "minute":
                paused[model] = skip.wait
            elif skip.why in ("daily", "no credit"):
                spent = True
                logger.info("%s: %s has no allowance left (%s) -- trying the next model", label, model, skip.why)
            elif skip.why in ("missing", "no free tier"):
                logger.info("%s: %s cannot be used with this key (%s) -- skipped for a week", label, model, skip.why)
            failure = skip.error
            continue
        count(model)
        if model != ladder[0]:
            logger.info("%s: answered by %s (%s call)", label, model, tier)
        return answer

    if paused:
        time.sleep(min(min(paused.values()), MAX_WAIT))
        for model in paused:
            wake(model)
        raise all_paused()
    if spent:
        raise all_spent()
    if failure is not None:
        raise failure
    raise all_spent()


# --- the tiers on a client --------------------------------------------------------------------------------------

def on_ladder(base: type, name: str, tier: str, remember: bool):
    """`base`'s method `name`, run with the transport set to `tier` and, for `remember`, answered once a run."""
    original = getattr(base, name)

    @functools.wraps(original)
    def run(self, *args: Any, **kwargs: Any):
        memo = getattr(self, "_remembered", None)
        key = None
        if remember and memo is not None:
            key = (name, json.dumps([args, kwargs], default=str, sort_keys=True))
            if key in memo:
                return memo[key]
        messages = self._client.messages
        before = getattr(messages, "tier", "page")
        messages.tier = tier
        try:
            result = original(self, *args, **kwargs)
        finally:
            messages.tier = before
        if key is not None and (result.get("choice") if isinstance(result, dict) else result):
            memo[key] = result
        return result
    return run


def put_on_ladders(client_class: type, base: type) -> None:
    for name, tier in TIERS.items():
        setattr(client_class, name, on_ladder(base, name, tier, name in REMEMBERED))
