"""
graph/workflow.py - LangGraph state machine engine for autonomous application workflows.
Implements nodes for navigation, authentication, DOM extraction, human form filling,
progression, and loop prevention with MAX_NODE_ATTEMPTS isolation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Literal
from langgraph.graph import END, START, StateGraph

from agent_v2.config import MAX_NODE_ATTEMPTS
from agent_v2.core.interceptors import OverlayInterceptor
from agent_v2.core.parser import DOMParser, FormElement
from agent_v2.graph.state import AutomationState
from agent_v2.intelligence.inference import InferenceEngine
from agent_v2.intelligence.memory import SemanticQuestionMemory

logger = logging.getLogger("agent_v2.workflow")


def _record_visit(state: AutomationState, node_name: str) -> None:
    """Increment visit count for a given node to track loop iterations."""
    counts = state.setdefault("node_visit_counts", {})
    counts[node_name] = counts.get(node_name, 0) + 1


# -----------------------------------------------------------------------------
# Node Implementations
# -----------------------------------------------------------------------------
async def node_preflight(state: AutomationState) -> Dict[str, Any]:
    """Navigates to URL, dismisses cookie overlays, and sets up observer."""
    _record_visit(state, "preflight")
    page = state["runtime_context"]["page"]
    url = state["target_url"]

    logger.info("[Workflow] Navigating to %s", url)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=40_000)
        await asyncio.sleep(2.0)
        await OverlayInterceptor.inject_observer(page)
        await OverlayInterceptor.dismiss_active_overlays(page)

        # Handle 'Apply' or 'Apply without an Account' if on job description
        apply_btn = page.locator("button:has-text('Apply without an Account'), button:has-text('Apply')").first
        if await apply_btn.count() > 0 and await apply_btn.is_visible():
            await apply_btn.click(timeout=3_000)
            await asyncio.sleep(2.5)

        return {"stage": "auth", "error_message": None}
    except Exception as exc:
        logger.error("[Workflow] Preflight failed: %s", exc)
        return {"stage": "failed", "error_message": str(exc)}


async def node_handle_auth(state: AutomationState) -> Dict[str, Any]:
    """Evaluates whether authentication is needed and executes sign-in if offered."""
    _record_visit(state, "handle_auth")
    page = state["runtime_context"]["page"]

    # Check for visible password boxes
    pw_inputs = page.locator("input[type='password']:visible")
    if await pw_inputs.count() > 0:
        logger.info("[Workflow] Password field detected; attempting credential injection...")
        # Check if 'Apply without an account' option exists to bypass
        bypass = page.locator("button:has-text('Apply without an Account'), a:has-text('Apply without an Account')").first
        if await bypass.count() > 0 and await bypass.is_visible():
            await bypass.click(timeout=3_000)
            await asyncio.sleep(2.0)
            return {"auth_state": "bypassed", "stage": "form_filling"}

        # Attempt auto-login with credentials
        profile = state.get("profile", {})
        email = profile.get("email", "")
        # Fill email
        email_inp = page.locator("input[type='email'], input[name*='email' i], input[id*='email' i]").first
        if await email_inp.count() > 0:
            await email_inp.fill(email)
            await pw_inputs.first.fill("DemoPassword123!")
            submit_btn = page.locator("button:has-text('Sign In'), button:has-text('Log In')").first
            if await submit_btn.count() > 0:
                await submit_btn.click()
                await asyncio.sleep(3.0)

    return {"auth_state": "authenticated", "stage": "form_filling"}


async def node_parse_dom(state: AutomationState) -> Dict[str, Any]:
    """Scrapes Shadow DOM and computes Page State Signature."""
    _record_visit(state, "parse_dom")
    page = state["runtime_context"]["page"]
    snapshot = await DOMParser.extract_snapshot(page)

    prev_sigs = state.get("previous_signatures", [])
    stuck_cycles = state.get("consecutive_stuck_cycles", 0)

    if prev_sigs and prev_sigs[-1] == snapshot.signature:
        stuck_cycles += 1
        logger.warning("[Workflow] Page signature identical to previous step (stuck cycle: %d)", stuck_cycles)
    else:
        stuck_cycles = 0

    return {
        "current_page_signature": snapshot.signature,
        "previous_signatures": prev_sigs + [snapshot.signature],
        "headings": snapshot.headings,
        "elements": [vars(el) for el in snapshot.elements],
        "consecutive_stuck_cycles": stuck_cycles,
        "stage": "form_filling",
    }


async def node_fill_form(state: AutomationState) -> Dict[str, Any]:
    """Answers and fills form elements using InferenceEngine and SemanticMemory."""
    _record_visit(state, "fill_form")
    page = state["runtime_context"]["page"]
    inference: InferenceEngine = state["runtime_context"]["inference"]
    memory: SemanticQuestionMemory = state["runtime_context"]["memory"]
    profile = state.get("profile", {})
    raw_elements = state.get("elements", [])

    elements = [FormElement(**data) for data in raw_elements if not data.get("disabled")]
    filled_count = 0

    for el in elements:
        # Skip buttons and non-inputs
        if el.tag not in ("input", "select", "textarea"):
            continue

        # Check memory first
        historical, conf = await memory.recall_answer(el.label or el.name) if (el.label or el.name) else (None, 0.0)

        # Decide answer
        answer = await inference.decide_answer(el, profile, historical_answer=historical)
        if not answer:
            continue

        # Execute input into DOM
        try:
            target_selector = el.selector or f"#{el.id}" if el.id else f"[name='{el.name}']" if el.name else ""
            if not target_selector:
                continue

            loc = page.locator(target_selector).first
            if not await loc.count() or not await loc.is_visible():
                continue

            if el.tag == "select":
                await loc.select_option(label=answer)
            elif el.type == "checkbox":
                if answer.lower() in ("true", "yes", "1"):
                    await loc.check(force=True)
                else:
                    await loc.uncheck(force=True)
            else:
                await loc.fill(answer)

            filled_count += 1
            # Record verified answer to semantic memory
            if el.label:
                await memory.record_answer(el.label, answer, field_type=el.type or el.tag)
        except Exception as exc:
            logger.debug("Could not fill field %s: %s", el.identifier, exc)

    logger.info("[Workflow] Filled %d form fields on current step", filled_count)
    return {"filled_count": filled_count, "stage": "advance"}


async def node_advance_step(state: AutomationState) -> Dict[str, Any]:
    """Clicks progression buttons ('Next', 'Continue', 'Save', 'Submit')."""
    _record_visit(state, "advance_step")
    page = state["runtime_context"]["page"]

    button_selectors = [
        "button:has-text('Save and Continue')",
        "button:has-text('Next')",
        "button:has-text('Continue')",
        "button:has-text('Submit Application')",
        "button:has-text('Submit')",
        "button:has-text('Save')",
    ]

    for sel in button_selectors:
        btn = page.locator(sel).first
        if await btn.count() > 0 and await btn.is_visible():
            logger.info("[Workflow] Advancing step by clicking %s", sel)
            await btn.click(timeout=5_000)
            await asyncio.sleep(2.5)
            break

    return {"stage": "verify"}


async def node_verify_completion(state: AutomationState) -> Dict[str, Any]:
    """Checks if confirmation screen or already-applied state is reached."""
    _record_visit(state, "verify_completion")
    page = state["runtime_context"]["page"]
    body_text = (await page.locator("body").inner_text(timeout=3_000)).lower()

    confirmation_tokens = [
        "thank you for applying",
        "your application has been submitted",
        "application submitted",
        "application received",
        "you've already applied",
        "already applied for this job",
    ]

    for token in confirmation_tokens:
        if token in body_text:
            logger.info("[Workflow] Application confirmed complete: %r", token)
            return {"stage": "submitted", "status_notes": f"Confirmed complete: {token}"}

    return {"stage": "parse_dom"}


async def node_fallback_recovery(state: AutomationState) -> Dict[str, Any]:
    """Handles unresponsive states or isolated loops by attempting recovery."""
    _record_visit(state, "fallback_recovery")
    page = state["runtime_context"]["page"]
    logger.warning("[Workflow] Fallback recovery activated. Clearing active overlays and settling...")

    await OverlayInterceptor.dismiss_active_overlays(page)
    await asyncio.sleep(2.0)

    # If already visited recovery multiple times, terminate gracefully
    visits = state.get("node_visit_counts", {}).get("fallback_recovery", 0)
    if visits >= state.get("max_node_attempts", MAX_NODE_ATTEMPTS):
        logger.error("[Workflow] MAX_NODE_ATTEMPTS reached in fallback recovery. Halting state machine.")
        return {"stage": "failed", "error_message": "Exceeded maximum loop attempts without progression"}

    return {"stage": "parse_dom", "consecutive_stuck_cycles": 0}


# -----------------------------------------------------------------------------
# Conditional Routing & Loop Guards
# -----------------------------------------------------------------------------
def router_next_step(state: AutomationState) -> Literal["parse_dom", "fallback_recovery", "submitted", "failed"]:
    """Deterministic routing function enforcing MAX_NODE_ATTEMPTS anti-loop limits."""
    stage = state.get("stage", "")
    stuck_cycles = state.get("consecutive_stuck_cycles", 0)
    counts = state.get("node_visit_counts", {})
    max_attempts = state.get("max_node_attempts", MAX_NODE_ATTEMPTS)

    if stage == "submitted":
        return "submitted"
    if stage == "failed":
        return "failed"

    # Anti-loop check: if stuck cycles or any individual node exceeds max_node_attempts
    if stuck_cycles >= max_attempts or any(cnt > (max_attempts * 3) for cnt in counts.values()):
        logger.warning("[Router] Anti-loop tripped (stuck_cycles=%d). Diverting to fallback recovery.", stuck_cycles)
        return "fallback_recovery"

    return "parse_dom"


# -----------------------------------------------------------------------------
# Graph Construction
# -----------------------------------------------------------------------------
def build_automation_graph() -> StateGraph:
    """Assembles and compiles the LangGraph state machine."""
    workflow = StateGraph(AutomationState)

    # Register nodes
    workflow.add_node("preflight", node_preflight)
    workflow.add_node("handle_auth", node_handle_auth)
    workflow.add_node("parse_dom", node_parse_dom)
    workflow.add_node("fill_form", node_fill_form)
    workflow.add_node("advance_step", node_advance_step)
    workflow.add_node("verify_completion", node_verify_completion)
    workflow.add_node("fallback_recovery", node_fallback_recovery)

    # Register edges
    workflow.add_edge(START, "preflight")
    workflow.add_edge("preflight", "handle_auth")
    workflow.add_edge("handle_auth", "parse_dom")
    workflow.add_edge("parse_dom", "fill_form")
    workflow.add_edge("fill_form", "advance_step")
    workflow.add_edge("advance_step", "verify_completion")

    # Conditional branching from verify_completion
    workflow.add_conditional_edges(
        "verify_completion",
        router_next_step,
        {
            "parse_dom": "parse_dom",
            "fallback_recovery": "fallback_recovery",
            "submitted": END,
            "failed": END,
        },
    )

    # Edge from fallback recovery back to parse_dom or END
    workflow.add_conditional_edges(
        "fallback_recovery",
        lambda s: END if s.get("stage") == "failed" else "parse_dom",
        {
            "parse_dom": "parse_dom",
            END: END,
        },
    )

    return workflow.compile()

