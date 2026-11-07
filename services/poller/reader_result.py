"""
Shared Reader Result contract for ATS pollers and parsers.
Added by ECC: Step 0 of the job source service plan.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ReaderResult:
    """
    Standardized result returned by ATS board readers.

    Attributes:
        postings: List of parsed, normalized job posting dictionaries.
        complete: True only when the entire board was read (no pagination cutoffs or network drops).
        error: Error message string if any error occurred, or None.
    """
    postings: List[Dict[str, Any]] = field(default_factory=list)
    complete: bool = False
    error: Optional[str] = None
