"""The Claude Code session as the agent's brain, in place of the paid API.

The agent reads a page, writes what it is looking at into data/_ask_page.json
(with the page itself beside it in data/_ask_page.txt) and waits. The session
picks that up, works out the answers -- asking the owner about anything only he
can answer, and remembering it for next time -- and writes the plan back to
data/_plan_page.json. The agent picks it up and carries on.

Nothing about what the agent is allowed to do changes: a plan is still only a
proposal, and page_agent.py enforces the rules that matter (no CAPTCHA, no
contradicting the profile on legal questions, no submitting unless every check
passes).

Set AGENT_BRAIN=session in .env to use this; AGENT_BRAIN=api goes back to
calling Anthropic directly.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

POLL_SECONDS = 2.0
DEFAULT_TIMEOUT = 3_600      # an hour: the session may be mid-answer with the owner


class SessionPlanner:
    """Answers page_agent's questions by handing them to the session.

    Anything this does not take over (tailoring a resume, writing a cover
    letter) is passed to the API client it was given, so a run that still has
    credit keeps working exactly as before.
    """

    ASK_JSON = "_ask_page.json"
    ASK_PAGE = "_ask_page.txt"
    PLAN_JSON = "_plan_page.json"

    def __init__(self, config: Any, folder: Path | str = "data",
                 fallback: Optional[Any] = None, timeout_seconds: Optional[int] = None):
        self._config = config
        self._folder = Path(folder)
        self._folder.mkdir(parents=True, exist_ok=True)
        self._fallback = fallback
        self._timeout = int(timeout_seconds or os.getenv("SESSION_BRAIN_TIMEOUT", DEFAULT_TIMEOUT))
        self.now_applying = ""      # the job being applied to, so the session knows what it is reading

    # -- what the session answers -------------------------------------------
    def plan_page(self, snapshot: str, facts: dict[str, Any], feedback: str = "") -> dict[str, Any]:
        """How to answer one page. Same contract as ClaudeClient.plan_page."""
        return self._ask("plan_page", snapshot, {"facts": facts, "feedback": feedback})

    # -- everything else goes on as before ----------------------------------
    def __getattr__(self, name: str) -> Any:
        fallback = self.__dict__.get("_fallback")
        if fallback is None:
            raise AttributeError(name)
        return getattr(fallback, name)

    # -- the waiting ---------------------------------------------------------
    def _ask(self, kind: str, page_text: str, extra: dict[str, Any]) -> dict[str, Any]:
        ask, page_file, plan = (self._folder / self.ASK_JSON, self._folder / self.ASK_PAGE,
                                self._folder / self.PLAN_JSON)
        for stale in (ask, page_file, plan):
            stale.unlink(missing_ok=True)

        page_file.write_text(page_text or "", encoding="utf-8")
        question = {
            "asked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "kind": kind,
            "job": self.now_applying,
            "page_file": str(page_file),
            "answer_into": str(plan),
            **extra,
        }
        self._write(ask, question)
        logger.info("ASKING THE SESSION: %s -- the page is in %s, the plan goes in %s",
                    self.now_applying or kind, page_file, plan)

        waited = 0.0
        while waited < self._timeout:
            answer = self._read(plan)
            if answer is not None:
                for done in (ask, page_file, plan):
                    done.unlink(missing_ok=True)
                logger.info("THE SESSION ANSWERED after %d seconds", int(waited))
                return answer
            time.sleep(POLL_SECONDS)
            waited += POLL_SECONDS
            if waited % 120 < POLL_SECONDS:
                logger.info("still waiting for the session (%d minutes)", int(waited // 60))
        ask.unlink(missing_ok=True)
        raise TimeoutError(f"the Claude Code session did not answer within {self._timeout // 60} minutes; "
                           f"open the session and it will read {page_file}")

    @staticmethod
    def _write(path: Path, body: dict[str, Any]) -> None:
        """Written beside the file and moved into place, so nothing ever reads
        half of it."""
        tmp = path.with_suffix(".writing")
        tmp.write_text(json.dumps(body, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        tmp.replace(path)

    @staticmethod
    def _read(path: Path) -> Optional[dict[str, Any]]:
        """The plan, or None while it is not there yet or not yet complete."""
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return None        # still being written; try again on the next turn
        return data if isinstance(data, dict) else None
