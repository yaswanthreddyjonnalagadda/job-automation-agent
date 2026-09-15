"""
Which site adapter handles a page.

    from sites import adapter_for
    adapter = adapter_for(page.url)

Reusable browser/form logic stays in browser_automation.py; anything specific
to one platform's markup lives in these adapters.
"""

from __future__ import annotations

from .base import SiteAdapter, merge_placeholders
from .eightfold import EightfoldAdapter
from .successfactors import SuccessFactorsAdapter
from .workday import WorkdayAdapter

# Most specific first; the generic adapter is the fallback.
ADAPTERS: tuple[type[SiteAdapter], ...] = (
    SuccessFactorsAdapter,
    WorkdayAdapter,
    EightfoldAdapter,
)

_CACHE: dict[str, SiteAdapter] = {}


def adapter_for(url: str) -> SiteAdapter:
    for adapter_class in ADAPTERS:
        if adapter_class.matches(url or ""):
            return _CACHE.setdefault(adapter_class.name, adapter_class())
    return _CACHE.setdefault("generic", SiteAdapter())


__all__ = ["adapter_for", "SiteAdapter", "merge_placeholders", "ADAPTERS"]
