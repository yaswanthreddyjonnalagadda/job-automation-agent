"""
intelligence/memory.py - Local semantic index manager for form questions and verified answers.
Maps newly encountered question variations back to previously settled responses using text vector similarity.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Optional
from agent_v2.storage.db_manager import AsyncDatabaseManager


def _normalize_text(text: str) -> str:
    """Lowercase, strip special characters, and collapse extra whitespace."""
    text = text.lower()
    text = re.sub(r"[\*:\?\.!,;/\(\)\[\]]", " ", text)
    return " ".join(text.split())


def _cosine_similarity(vec1: dict[str, float], vec2: dict[str, float]) -> float:
    """Compute cosine similarity between two word-frequency dictionaries."""
    intersection = set(vec1.keys()) & set(vec2.keys())
    numerator = sum(vec1[x] * vec2[x] for x in intersection)

    sum1 = sum(v ** 2 for v in vec1.values())
    sum2 = sum(v ** 2 for v in vec2.values())
    denominator = math.sqrt(sum1) * math.sqrt(sum2)

    if not denominator:
        return 0.0
    return float(numerator) / denominator


def _text_to_vector(text: str) -> dict[str, float]:
    """Tokenize and compute term frequency vector."""
    words = _normalize_text(text).split()
    vec: dict[str, float] = {}
    for word in words:
        vec[word] = vec.get(word, 0.0) + 1.0
    return vec


class SemanticQuestionMemory:
    """Semantic question-answer memory manager using cosine similarity over normalized tokens."""

    def __init__(self, db_manager: AsyncDatabaseManager):
        self.db = db_manager
        self._cached_entries: list[dict[str, Any]] = []
        self._loaded: bool = False

    async def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._cached_entries = await self.db.list_form_answers()
            self._loaded = True

    async def recall_answer(
        self,
        question: str,
        threshold: float = 0.72,
        domain: Optional[str] = None,
    ) -> Optional[tuple[str, float]]:
        """Searches memory for a matching question. Returns (verified_answer, confidence)."""
        await self._ensure_loaded()
        if not self._cached_entries or not question:
            return None

        # Direct hash lookup first
        q_norm = _normalize_text(question)
        q_hash = hashlib.md5(q_norm.encode("utf-8")).hexdigest()
        for entry in self._cached_entries:
            if entry["question_hash"] == q_hash:
                return entry["verified_answer"], 1.0

        # Semantic cosine similarity scan
        query_vec = _text_to_vector(question)
        best_score = 0.0
        best_answer: Optional[str] = None

        for entry in self._cached_entries:
            cached_vec = _text_to_vector(entry["normalized_question"])
            sim = _cosine_similarity(query_vec, cached_vec)

            # Boost score slightly if on the same employer domain
            if domain and entry.get("domain") and entry["domain"] == domain:
                sim = min(1.0, sim + 0.1)

            if sim > best_score:
                best_score = sim
                best_answer = entry["verified_answer"]

        if best_score >= threshold and best_answer is not None:
            return best_answer, best_score

        return None

    async def record_answer(
        self,
        question: str,
        verified_answer: str,
        field_type: str = "text",
        domain: Optional[str] = None,
    ) -> None:
        """Stores a newly verified answer into persistent memory and updates cache."""
        q_norm = _normalize_text(question)
        q_hash = hashlib.md5(q_norm.encode("utf-8")).hexdigest()

        await self.db.save_form_answer(
            question_hash=q_hash,
            normalized_question=q_norm,
            verified_answer=verified_answer,
            field_type=field_type,
            domain=domain,
        )

        # Invalidate or append to local cache
        existing = next((e for e in self._cached_entries if e["question_hash"] == q_hash), None)
        if existing:
            existing["verified_answer"] = verified_answer
        else:
            self._cached_entries.append({
                "question_hash": q_hash,
                "normalized_question": q_norm,
                "verified_answer": verified_answer,
                "field_type": field_type,
                "domain": domain,
            })

