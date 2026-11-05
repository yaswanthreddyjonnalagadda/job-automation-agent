"""
SearchSpec data model (Pydantic v2) for profile-driven job discovery and matching.
Added by ECC: Replaces static hardcoded search queries with versioned candidate instructions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class SearchSpec(BaseModel):
    """
    Validated candidate search specification driving all pollers, query expansion,
    and 3-valued filtering.
    """
    version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw_instructions: str = ""
    target_titles: List[str] = Field(default_factory=list)
    title_synonyms: Dict[str, List[str]] = Field(default_factory=dict)
    adjacent_titles: List[str] = Field(default_factory=list)
    seniority_levels: List[str] = Field(default_factory=lambda: ["Senior", "Lead", "Staff", "Mid"])
    must_have_keywords: List[str] = Field(default_factory=list)
    nice_to_have_keywords: List[str] = Field(default_factory=list)
    locations: List[str] = Field(default_factory=lambda: ["Fairfax, VA", "Arlington, VA", "Remote"])
    workplace_types: List[str] = Field(default_factory=lambda: ["remote", "hybrid", "onsite"])
    salary_floor_annual: Optional[int] = 125000
    salary_currency: str = "USD"
    employment_types: List[str] = Field(default_factory=lambda: ["full-time"])
    requires_visa_sponsorship: bool = True
    excluded_companies: List[str] = Field(default_factory=list)
    excluded_keywords: List[str] = Field(default_factory=list)
    posting_age_window_hours: int = 72
    hard_criteria: Dict[str, bool] = Field(default_factory=lambda: {
        "sponsorship": True,
        "location": True,
        "title": True,
        "salary": False,
        "seniority": False,
    })
    is_active: bool = True

    @field_validator("salary_floor_annual")
    @classmethod
    def validate_salary(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 0:
            raise ValueError("salary_floor_annual cannot be negative")
        return v

    def all_wanted_titles(self) -> List[str]:
        """Returns flattened list of target titles plus all synonyms and adjacent titles."""
        titles: List[str] = []
        for t in self.target_titles:
            if t not in titles:
                titles.append(t)
        for _, syns in self.title_synonyms.items():
            for s in syns:
                if s not in titles:
                    titles.append(s)
        for a in self.adjacent_titles:
            if a not in titles:
                titles.append(a)
        return titles

    def get_expanded_queries(self, max_queries: int = 25) -> List[Dict[str, str]]:
        """
        Generates cross-product of (Target Titles + Top Synonyms) x (Primary Locations)
        capped at max_queries to prevent rate limit starvation.
        """
        expanded: List[Dict[str, str]] = []
        titles = self.target_titles.copy()
        for t, syns in self.title_synonyms.items():
            for s in syns[:2]:
                if s not in titles:
                    titles.append(s)

        locs = self.locations.copy()
        if not locs:
            locs = ["Remote"]

        for title in titles:
            for loc in locs:
                expanded.append({
                    "title": title,
                    "location": loc,
                    "is_remote": loc.strip().lower() == "remote",
                })
                if len(expanded) >= max_queries:
                    return expanded

        return expanded

