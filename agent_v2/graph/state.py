"""
graph/state.py - Type definition of the application state dictionary utilized within LangGraph.
Tracks URLs, page signatures, DOM element mappings, node visit counters, and loop gates.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


class AutomationState(TypedDict):
    """LangGraph state representation for the autonomous browser application platform."""

    # Target parameters
    target_url: str
    job_title: str
    company: str
    profile: Dict[str, Any]
    tailored_resume_path: Optional[str]

    # SPA State & Signatures
    current_page_signature: str
    previous_signatures: List[str]
    headings: List[str]

    # Scraped Form Elements & Action Queue
    elements: List[Dict[str, Any]]
    answers_queue: List[Dict[str, Any]]
    filled_count: int

    # Anti-Loop Control & Visit Accounting
    node_visit_counts: Dict[str, int]
    consecutive_stuck_cycles: int
    max_node_attempts: int

    # Lifecycle & Authentication
    auth_state: str  # "authenticated", "needs_login", "bypassed", "failed"
    stage: str  # "init", "preflight", "auth", "form_filling", "review", "submitted", "failed"
    status_notes: str
    error_message: Optional[str]

    # Active runtime objects (passed through context)
    runtime_context: Dict[str, Any]

