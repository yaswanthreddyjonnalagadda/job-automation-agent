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

logger = logging.getLogger("state_machine")

STATUS_BLOCKED_VALIDATION_LOOP = "BLOCKED_VALIDATION_LOOP"


def compute_state_fingerprint(page) -> tuple[str, dict[str, Any]]:
    """Computes a page state hash string of:
    1. current URL
    2. active step text indicator
    3. count of visible input fields

    Returns (hash_string, metadata_dict).
    """
    url = getattr(page, "url", "") or ""

    # 1. Active step text indicator
    step_indicator = ""
    try:
        step_indicator = page.evaluate("""() => {
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

    # Compute SHA-256 fingerprint
    raw_key = f"{url.strip().lower()}|{step_indicator.strip().lower()}|{visible_inputs}"
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
    """Automated forensic failure dumper:
    Creates a timestamped folder under runs/<timestamp>_<domain>/ and exports four
    critical diagnostic assets:
    1. screenshot.png (full-page screenshot)
    2. page_state.html (complete raw HTML snapshot)
    3. axtree_dump.json (browser accessibility snapshot)
    4. console_logs.json (captured browser console logs)
    """
    url = getattr(page, "url", "") or ""
    raw_domain = urlparse(url).netloc.lower() or "unknown_domain"
    domain = re.sub(r"[^a-zA-Z0-9.-]", "_", raw_domain).strip("_") or "session"

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dump_dir = Path(base_dir) / f"{timestamp}_{domain}"
    dump_dir.mkdir(parents=True, exist_ok=True)

    # 1. Full-page screenshot (.png)
    screenshot_path = dump_dir / "screenshot.png"
    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
    except Exception as exc:
        logger.debug("Forensic screenshot capture failed: %s", exc)

    # 2. Raw HTML snapshot (page_state.html)
    html_path = dump_dir / "page_state.html"
    try:
        html_content = page.content() or ""
        html_path.write_text(html_content, encoding="utf-8")
    except Exception as exc:
        logger.debug("Forensic HTML capture failed: %s", exc)

    # 3. Accessibility snapshot (axtree_dump.json)
    axtree_path = dump_dir / "axtree_dump.json"
    try:
        axtree_data: Any = None
        if hasattr(page, "accessibility") and hasattr(page.accessibility, "snapshot"):
            axtree_data = page.accessibility.snapshot()
        if not axtree_data:
            axtree_data = {"aria_snapshot": page.locator("body").aria_snapshot(mode="ai", timeout=5_000)}
        axtree_path.write_text(json.dumps(axtree_data, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        logger.debug("Forensic accessibility snapshot failed: %s", exc)
        axtree_path.write_text(json.dumps({"error": str(exc)}, indent=2), encoding="utf-8")

    # 4. Captured browser console logs (console_logs.json)
    logs_path = dump_dir / "console_logs.json"
    try:
        logs_to_write = console_logs
        if logs_to_write is None:
            logs_to_write = getattr(page, "_console_logs", None)
        if logs_to_write is None:
            logs_to_write = []
        logs_path.write_text(json.dumps(logs_to_write, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        logger.debug("Forensic console logs export failed: %s", exc)

    # Diagnostic metadata
    meta_path = dump_dir / "failure_meta.json"
    try:
        meta_path.write_text(
            json.dumps({
                "reason": reason,
                "timestamp": timestamp,
                "url": url,
                "domain": domain,
            }, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    logger.warning("FORENSIC DUMPER: Exported 4 diagnostic assets to %s (Reason: %s)", dump_dir, reason)
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
