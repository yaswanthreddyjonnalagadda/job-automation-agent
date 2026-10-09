"""
State machine, loop-detection circuit breaker, and automated forensic failure dumper.

Tasks covered:
- Task 4.1: Loop-detection circuit breaker computing SHA-256 state fingerprints
  (current URL, active step indicator, visible input count) and tripping with
  BLOCKED_VALIDATION_LOOP after 3 consecutive identical cycles.
- Task 4.2: Automated forensic failure dumper creating runs/<timestamp>_<domain>/
  exporting screenshot.png, page_state.html, axtree_dump.json, and console_logs.json.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from perception import hide_secrets, hide_secrets_in_tree

logger = logging.getLogger("state_machine")

STATUS_BLOCKED_VALIDATION_LOOP = "BLOCKED_VALIDATION_LOOP"


def step_indicator_text(page) -> str:
    """The active wizard/progress-bar step's own text, read straight from whichever of a
    handful of common markup patterns the page actually uses -- empty if none is found.

    One place for this selector list: `recovery.compute_page_identity` (Phase 0-B3) reuses
    it for a coarse, restart-safe "which stage is this" signal, rather than keeping its own
    copy that could drift from this one."""
    try:
        return page.evaluate("""() => {
            const selectors = [
                "[aria-current='step']",
                "[data-automation-id='progressBarActiveStep']",
                "[class*='active-step' i]",
                "[class*='step-active' i]",
                "[class*='current-step' i]",
                "[class*='step'][class*='active']",
                ".step-indicator.active",
                ".step-indicator",
                ".step.active",
                "li.active",
                "[role='progressbar'] [aria-valuenow]",
                "[class*='step-indicator']",
                "[class*='wizard-step']"
            ];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el && el.innerText && el.innerText.trim()) {
                    return el.innerText.trim().replace(/\\s+/g, ' ');
                }
            }
            return "";
        }""") or ""
    except Exception as exc:
        logger.debug("Could not extract step indicator: %s", exc)
        return ""


def compute_state_fingerprint(page) -> tuple[str, dict[str, Any]]:
    """Computes a page state hash string of:
    1. current URL
    2. active step text indicator
    3. count of visible input fields

    Returns (hash_string, metadata_dict).
    """
    url = getattr(page, "url", "") or ""

    # 1. Active step text indicator
    step_indicator = step_indicator_text(page)

    # 2. Count of visible input fields
    visible_inputs = 0
    try:
        visible_inputs = int(page.evaluate("""() => {
            const isVisible = el => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                return !!(el.offsetParent || (el.getClientRects && el.getClientRects().length));
            };
            const inputs = Array.from(document.querySelectorAll('input:not([type="hidden"]), select, textarea'));
            return inputs.filter(isVisible).length;
        }""") or 0)
    except Exception as exc:
        logger.debug("Could not count visible inputs: %s", exc)

    # Changes to committed answers are progress even on the same wizard step.
    # Keep only a digest in diagnostics, never the personal field values.
    answer_digest = ''
    try:
        answers = page.evaluate("""() => {
            const shown = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
            return [...document.querySelectorAll('input,select,textarea,[role=checkbox],[role=radio],[role=listbox],[data-automation-id=selectedItem],[data-automation-id=moniker],[aria-haspopup=listbox]')]
                .filter(e => shown(e) && e.type !== 'hidden' && e.type !== 'password')
                .map(e => [e.id || e.name || e.getAttribute('data-automation-id') || e.tagName,
                           e.value || '', e.checked || e.getAttribute('aria-checked') || '',
                           e.tagName === 'INPUT' || e.tagName === 'TEXTAREA' ? '' : (e.textContent || '').trim()]);
        }""")
        answer_digest = hashlib.sha256(json.dumps(answers, sort_keys=True).encode()).hexdigest()
    except Exception as exc:
        logger.debug("Could not fingerprint committed answers: %s", type(exc).__name__)
    # Compute SHA-256 fingerprint
    raw_key = f"{url.strip().lower()}|{step_indicator.strip().lower()}|{visible_inputs}|{answer_digest}"
    hash_str = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    meta = {
        "url": url,
        "step_indicator": step_indicator,
        "step_text": step_indicator,
        "visible_inputs": visible_inputs,
        "raw_key": raw_key,
        "fingerprint": hash_str,
    }
    return hash_str, meta


def dump_forensic_failure(
    page,
    reason: str = "",
    base_dir: Path = Path("runs"),
    console_logs: Optional[list] = None,
) -> Path:
    """Export storage-only, privacy-safe structural evidence; failures omit assets."""
    import diagnostics
    domain = urlparse(getattr(page, "url", "") or "").hostname or "session"
    domain = re.sub(r"[^a-zA-Z0-9.-]", "_", domain)[:100]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    dump_dir = Path(base_dir) / f"{timestamp}_{domain}"
    diagnostics.capture_bundle(page, dump_dir, reason=reason, console_logs=console_logs,
                               screenshot_name="screenshot.png", html_name="page_state.html")
    logger.warning("FORENSIC_CAPTURE: sanitized diagnostic capture attempted")
    return dump_dir



class StateFingerprintCircuitBreaker:
    """Loop-detection circuit breaker. Before clicking any page progression
    button ('Next', 'Save & Continue'), compute a page state hash string.
    If this state fingerprint remains identical across 3 consecutive cycles,
    trip the breaker with state BLOCKED_VALIDATION_LOOP, trigger the forensic
    dumper, and signal clean exit."""

    def __init__(self, consecutive_threshold: int = 3, base_dir: Path = Path("runs")):
        self.consecutive_threshold = consecutive_threshold
        self.base_dir = base_dir
        self.last_fingerprint: str = ""
        self.identical_count: int = 0
        self.tripped: bool = False
        self.state: str = "IDLE"
        self.history: list[tuple[str, dict[str, Any]]] = []

    @property
    def consecutive_matches(self) -> int:
        return self.identical_count

    def check(self, page) -> tuple[bool, str, dict[str, Any]]:
        """Computes current fingerprint and checks whether it matches the previous state.
        Returns (tripped, state, meta)."""
        fingerprint, meta = compute_state_fingerprint(page)
        if fingerprint == self.last_fingerprint and self.last_fingerprint != "":
            self.identical_count += 1
        else:
            self.last_fingerprint = fingerprint
            self.identical_count = 1

        self.history.append((fingerprint, meta))

        if self.identical_count >= self.consecutive_threshold:
            self.tripped = True
            self.state = STATUS_BLOCKED_VALIDATION_LOOP
            logger.warning(
                "CIRCUIT BREAKER TRIPPED: State fingerprint identical across %d consecutive cycles (%s)",
                self.identical_count,
                fingerprint[:12],
            )
            return True, self.state, meta

        return False, self.state, meta

    def trip_and_dump(
        self,
        page,
        reason: str = "Identical state fingerprint loop detected",
        base_dir: Optional[Path] = None,
        tracker=None,
        key: str = "",
        console_logs: Optional[list] = None,
    ) -> Path:
        """Explicitly trips the circuit breaker, exports all forensic assets,
        and optionally updates the tracker status."""
        self.tripped = True
        self.state = STATUS_BLOCKED_VALIDATION_LOOP
        target_dir = base_dir if base_dir is not None else self.base_dir
        dump_dir = dump_forensic_failure(page, reason=reason, base_dir=target_dir, console_logs=console_logs)

        if tracker is not None and key and hasattr(tracker, "update_status"):
            try:
                tracker.update_status(
                    key,
                    STATUS_BLOCKED_VALIDATION_LOOP,
                    notes=f"Circuit breaker tripped: {reason} (evidence in {dump_dir})",
                )
            except Exception as exc:
                logger.debug("Could not update tracker with circuit breaker status: %s", exc)

        return dump_dir

    def reset(self) -> None:
        """Resets the circuit breaker state for a new application run."""
        self.last_fingerprint = ""
        self.identical_count = 0
        self.tripped = False
        self.state = "IDLE"
        self.history.clear()
