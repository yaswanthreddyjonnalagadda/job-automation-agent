"""
Who put a value on the form: the owner, the agent, or the site.

Until now the agent knew one thing for certain -- which values it wrote
itself -- and treated everything else as the owner's answer. That was safe
but blind: a country the site's resume parser guessed, or the first entry
of an alphabetical list the widget fell back to, was protected as if the
owner had chosen it. The agent could not put it right, so someone
hard-coded the wrong values ("Afghanistan", "Badakhshan") as exceptions.

This module replaces the guess with an observation. A small script runs in
every page and frame (Playwright's add_init_script) and records the form
controls a person really typed into or changed:

  * only *trusted* input and change events count -- a site's own scripts
    cannot produce them; and
  * only while the agent is not working -- the agent's own Playwright input
    is trusted too, so the page starts every document marked "agent busy"
    and the agent marks it idle exactly while it waits for the owner (the
    hand-over, a CAPTCHA, a signature, the login).

With that record, a value is:

  EMPTY   -- nothing there;
  AGENT   -- the agent wrote it (safety.AgentValues);
  OWNER   -- a person changed that control while the agent waited;
  SITE    -- present, not the agent's, and no person touched it;
  UNKNOWN -- present and not the agent's, on a page without the observer.

The decisions built on this live in safety.py. This module only observes.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("provenance")

EMPTY = "empty"
AGENT = "agent"
OWNER = "owner"
SITE = "site"
UNKNOWN = "unknown"

OBSERVER_SCRIPT = r"""(() => {
  let state = window.__jaaProvenance;
  if (!state) {
    const edited = [];
    state = { agentBusy: true, edited };
    state.note = (event) => {
      if (!event.isTrusted || state.agentBusy) return;
      const path = event.composedPath ? event.composedPath() : [];
      const target = path.length ? path[0] : event.target;
      if (target && target.nodeType === 1 && !edited.includes(target)) edited.push(target);
    };
    // True when a person changed this control or something inside it,
    // following shadow roots up to their hosts.
    state.ownerEdited = (el) => !!el && edited.some((t) => {
      for (let n = t; n; n = n.parentNode || n.host) { if (n === el) return true; }
      return false;
    });
    window.__jaaProvenance = state;
  }
  // Adding the same listener twice does nothing, and adding it again after
  // document.open() (which erases listeners) brings it back.
  document.addEventListener('input', state.note, true);
  document.addEventListener('change', state.note, true);
})();"""


def install(context: Any) -> None:
    """Observe every page the context opens from now on, and those already open."""
    try:
        context.add_init_script(script=OBSERVER_SCRIPT)
    except Exception as exc:
        logger.debug("Could not register the provenance observer: %s", exc)
        return
    for page in list(getattr(context, "pages", []) or []):
        install_on_page(page)


def install_on_page(page: Any) -> None:
    """Start observing an already-loaded page (idempotent)."""
    for frame in _frames(page):
        try:
            frame.evaluate(OBSERVER_SCRIPT)
        except Exception as exc:
            logger.debug("Could not start the provenance observer in a frame: %s", exc)


def set_agent_busy(page: Any, busy: bool) -> None:
    """Mark whether the agent is working (True) or waiting for the owner (False).

    Every new document starts busy, so a navigation can only make the
    observer more conservative, never attribute the agent's input to the owner.
    """
    for frame in _frames(page):
        try:
            frame.evaluate("b => { if (window.__jaaProvenance) window.__jaaProvenance.agentBusy = b; }", bool(busy))
        except Exception:
            pass


def owner_edited(target: Any, selector: str = "") -> Optional[bool]:
    """Whether a person changed this control while the agent waited.

    `target` is a Playwright locator, or a page/frame together with a
    selector. Returns None when the page has no observer (or the control
    cannot be found), so callers can tell "no" from "cannot know".
    """
    try:
        locator = target.locator(selector).first if selector else target
        return locator.evaluate(
            "el => window.__jaaProvenance ? window.__jaaProvenance.ownerEdited(el) : null",
            timeout=2_000,
        )
    except Exception:
        return None


def _frames(page: Any) -> list:
    frames = getattr(page, "frames", None)
    if frames:
        try:
            return list(frames)
        except Exception:
            pass
    return [page]
