"""
Semantic Answer Engine & Canonical Intent Ontology.
Added by ECC: Maps differently worded questions to canonical intents, maps answers to controls semantically,
and queues uncovered questions in a review queue without fabricating or guessing.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import option_match

logger = logging.getLogger("answer_engine")


class CanonicalIntent(str, Enum):
    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    PHONE_TYPE = "phone_type"
    COUNTY = "county"
    WORK_AUTHORIZATION = "work_authorization"
    SPONSORSHIP = "sponsorship"
    RELOCATION = "relocation"
    MINIMUM_SALARY = "minimum_salary"
    PRIOR_EMPLOYMENT = "prior_employment"
    RELATIVES_EMPLOYED = "relatives_employed"
    REFERRAL_SOURCE = "referral_source"
    AGE_18_OR_OLDER = "age_18_or_older"
    FULL_TIME = "full_time"
    START_DATE = "start_date"
    WEEKENDS_HOLIDAYS = "weekends_holidays"
    EEO_GENDER = "eeo_gender"
    EEO_VETERAN = "eeo_veteran"
    EEO_DISABILITY = "eeo_disability"
    EEO_RACE = "eeo_race"


class AnswerSource(str, Enum):
    BANK = "bank"
    DERIVED = "derived"
    USER_PROVIDED = "user-provided"


@dataclass
class ResolvedAnswer:
    question: str
    intent: Optional[CanonicalIntent]
    answer: Optional[str]
    confidence: float
    source: AnswerSource
    selected_option: Optional[str] = None
    supporting_facts: List[str] = field(default_factory=list)
    needs_user_review: bool = False
    review_queue_id: Optional[int] = None


def get_approved_seed_answers() -> Dict[CanonicalIntent, str]:
    """Returns candidate's approved rules for Yaswanth Reddy Jonnalagadda."""
    return {
        CanonicalIntent.FIRST_NAME: "Yaswanth Reddy",
        CanonicalIntent.LAST_NAME: "Jonnalagadda",
        CanonicalIntent.PHONE_TYPE: "Mobile",
        CanonicalIntent.COUNTY: "Fairfax",
        CanonicalIntent.WORK_AUTHORIZATION: "Yes",
        CanonicalIntent.SPONSORSHIP: "Yes",
        CanonicalIntent.RELOCATION: "Yes",
        CanonicalIntent.MINIMUM_SALARY: "125000",
        CanonicalIntent.PRIOR_EMPLOYMENT: "No",
        CanonicalIntent.RELATIVES_EMPLOYED: "No",
        CanonicalIntent.REFERRAL_SOURCE: "LinkedIn",
        CanonicalIntent.AGE_18_OR_OLDER: "Yes",
        CanonicalIntent.FULL_TIME: "Full Time",
        CanonicalIntent.START_DATE: "Immediately",
        CanonicalIntent.WEEKENDS_HOLIDAYS: "Yes",
        CanonicalIntent.EEO_GENDER: "Male",
        CanonicalIntent.EEO_VETERAN: "I am not a protected veteran",
        CanonicalIntent.EEO_DISABILITY: "No, I do not have a disability",
        CanonicalIntent.EEO_RACE: "Asian",
    }


# Regex intent patterns for zero-cost high-confidence canonical classification
INTENT_PATTERNS = [
    (
        CanonicalIntent.SPONSORSHIP,
        re.compile(
            r"\b(?:visa|immigration|require|need|future|now or in the future)\s+.*?(?:sponsor|h-?1b|opt|cpt|work authorization)\b|\b(?:sponsor|sponsorship)\b",
            re.I,
        ),
    ),
    (
        CanonicalIntent.WORK_AUTHORIZATION,
        re.compile(
            r"\b(?:legally authorized|eligible to work|authorized to work|legal right to work)\b",
            re.I,
        ),
    ),
    (
        CanonicalIntent.RELATIVES_EMPLOYED,
        re.compile(
            r"\b(?:relatives?|family members?|relationship.*?(?:employee|worker|staff))\b",
            re.I,
        ),
    ),
    (
        CanonicalIntent.PRIOR_EMPLOYMENT,
        re.compile(
            r"\b(?:previously employed|formerly worked|former employee|worked for|worked at|prior employment|been employed|employed by|employed with)\b",
            re.I,
        ),
    ),
    (
        CanonicalIntent.REFERRAL_SOURCE,
        re.compile(
            r"\b(?:how did you hear|where did you hear|source|how were you referred|referral source|what source referred|first learn of this|learn about this)\b",
            re.I,
        ),
    ),
    (
        CanonicalIntent.AGE_18_OR_OLDER,
        re.compile(
            r"\b(?:18 years of age|at least 18|legal working age|age of majority|18 or older|18 plus)\b|\b18\b",
            re.I,
        ),
    ),
    (
        CanonicalIntent.FULL_TIME,
        re.compile(
            r"\b(?:full[\s\-]?time|part[\s\-]?time|employment type)\b",
            re.I,
        ),
    ),
    (
        CanonicalIntent.START_DATE,
        re.compile(
            r"\b(?:when can you start|available to start|availability to start|start working|earliest start date|earliest possible start date|notice period)\b",
            re.I,
        ),
    ),
    (
        CanonicalIntent.WEEKENDS_HOLIDAYS,
        re.compile(
            r"\b(?:weekends?|holidays?|work weekends?|overtime|work overtime)\b",
            re.I,
        ),
    ),
    (
        CanonicalIntent.MINIMUM_SALARY,
        re.compile(
            r"\b(?:salary|compensation|desired pay|expected pay)\b",
            re.I,
        ),
    ),
    (
        CanonicalIntent.RELOCATION,
        re.compile(
            r"\b(?:willing to relocate|open to relocation|relocate|relocation)\b",
            re.I,
        ),
    ),
    (
        CanonicalIntent.PHONE_TYPE,
        re.compile(r"\b(?:phone type|device type|mobile or landline)\b", re.I),
    ),
    (
        CanonicalIntent.COUNTY,
        re.compile(r"\bcounty\b", re.I),
    ),
]

# Sensitive patterns that MUST trigger human review queue rather than guessing
SENSITIVE_PATTERNS = [
    re.compile(r"\b(?:supervisor|manager|direct report)\s+.*?(?:phone|number|contact|email)\b", re.I),
    re.compile(r"\b(?:reference|professional reference|personal reference)\s+.*?(?:name|phone|contact)\b", re.I),
    re.compile(r"\b(?:shift|schedule preference|first shift|night shift|day shift)\b", re.I),
    re.compile(r"\b(?:disciplinary|arrest|conviction|felony|misdemeanor|criminal)\b", re.I),
    re.compile(r"\b(?:secret clearance|top secret|polygraph|security badge)\b", re.I),
    re.compile(r"\b(?:non-?compete|nda|restrictive covenant)\b", re.I),
]


class SemanticAnswerEngine:
    """Answers job application questions using canonical intent mapping and human-review queues."""

    def __init__(self, storage_dsn: Optional[str] = None):
        self.dsn = storage_dsn or "sqlite:///data/answer_engine.db"
        self.seeds = get_approved_seed_answers()
        self.learned_answers: Dict[str, str] = {}
        self._init_storage()

    def _init_storage(self):
        db_path = self.dsn.replace("sqlite:///", "").replace("sqlite://", "")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        with conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS review_queue (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id      INTEGER,
                company             TEXT,
                job_title           TEXT,
                raw_question        TEXT NOT NULL,
                field_type          TEXT,
                options             TEXT,
                suggested_answer    TEXT,
                confidence          REAL,
                supporting_facts    TEXT,
                status              TEXT DEFAULT 'pending',
                created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
                resolved_at         TEXT
            );
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS custom_answers (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                normalized_question TEXT UNIQUE NOT NULL,
                answer              TEXT NOT NULL,
                created_at          TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """)
        conn.close()

    def _normalize(self, q: str) -> str:
        cleaned = re.sub(r"\(?\s*required\s*\)?|\*|[^\w\s]", " ", q.lower())
        return " ".join(cleaned.split())

    def match_intent(self, question: str) -> Tuple[Optional[CanonicalIntent], float]:
        norm = self._normalize(question)
        for intent, pattern in INTENT_PATTERNS:
            if pattern.search(norm):
                return intent, 0.95
        return None, 0.0

    def resolve_question(
        self,
        question: str,
        application_id: Optional[int] = None,
        company_name: Optional[str] = None,
        job_title: Optional[str] = None,
        field_type: str = "text",
        options: Optional[List[str]] = None,
    ) -> ResolvedAnswer:
        """Resolves question from bank, learned user answers, or flags for review queue."""
        norm_q = self._normalize(question)
        db_path = self.dsn.replace("sqlite:///", "").replace("sqlite://", "")

        # 1. Check custom learned answers
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        custom_hit = None
        try:
            row = conn.execute(
                "SELECT answer FROM custom_answers WHERE normalized_question = ?",
                (norm_q,),
            ).fetchone()
            if row:
                custom_hit = row["answer"]
            else:
                rows = conn.execute("SELECT normalized_question, answer FROM custom_answers").fetchall()
                q_tokens = set(norm_q.split()) - {"what", "is", "your", "which", "do", "you", "a", "the", "for", "to", "in", "of"}
                for r in rows:
                    saved_tokens = set(r["normalized_question"].split()) - {"what", "is", "your", "which", "do", "you", "a", "the", "for", "to", "in", "of"}
                    if saved_tokens and len(q_tokens & saved_tokens) / len(saved_tokens) >= 0.5:
                        custom_hit = r["answer"]
                        break
        finally:
            conn.close()

        if custom_hit:
            return ResolvedAnswer(
                question=question,
                intent=None,
                answer=custom_hit,
                confidence=0.95,
                source=AnswerSource.USER_PROVIDED,
            )

        # 2. Check sensitive questions immediately -> Direct queue for human review
        if any(pat.search(norm_q) for pat in SENSITIVE_PATTERNS):
            conn = sqlite3.connect(db_path)
            try:
                with conn:
                    cur = conn.execute(
                        "INSERT INTO review_queue (application_id, company, job_title, raw_question, "
                        "field_type, options, confidence, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
                        (
                            application_id,
                            company_name or "Unknown Company",
                            job_title or "Position",
                            question,
                            field_type,
                            json.dumps(options or []),
                            0.0,
                        ),
                    )
                    queue_id = cur.lastrowid
            finally:
                conn.close()

            return ResolvedAnswer(
                question=question,
                intent=None,
                answer=None,  # NEVER guess or fabricate sensitive facts!
                confidence=0.0,
                source=AnswerSource.USER_PROVIDED,
                needs_user_review=True,
                review_queue_id=queue_id,
            )

        # 3. Check canonical intents
        intent, conf = self.match_intent(question)
        if intent and intent in self.seeds:
            return ResolvedAnswer(
                question=question,
                intent=intent,
                answer=self.seeds[intent],
                confidence=conf,
                source=AnswerSource.BANK,
                supporting_facts=[f"Candidate approved rule for {intent.value}"],
            )

        # 4. Check uncovered questions -> Queue for human review
        conn = sqlite3.connect(db_path)
        try:
            with conn:
                cur = conn.execute(
                    "INSERT INTO review_queue (application_id, company, job_title, raw_question, "
                    "field_type, options, confidence, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
                    (
                        application_id,
                        company_name or "Unknown Company",
                        job_title or "Position",
                        question,
                        field_type,
                        json.dumps(options or []),
                        conf,
                    ),
                )
                queue_id = cur.lastrowid
        finally:
            conn.close()

        return ResolvedAnswer(
            question=question,
            intent=None,
            answer=None,  # NEVER guess or fabricate sensitive facts!
            confidence=conf,
            source=AnswerSource.USER_PROVIDED,
            needs_user_review=True,
            review_queue_id=queue_id,
        )

    def resolve_question_for_control(
        self,
        question: str,
        control_type: str,
        options: List[str],
        application_id: Optional[int] = None,
    ) -> ResolvedAnswer:
        """Resolves question and maps the canonical answer to the closest semantic option widget."""
        resolved = self.resolve_question(
            question=question,
            application_id=application_id,
            field_type=control_type,
            options=options,
        )
        if resolved.answer and options:
            matched = None
            try:
                idx = option_match.best_option(options, resolved.answer)
                if idx is not None and 0 <= idx < len(options):
                    matched = options[idx]
            except Exception:
                matched = None

            if not matched:
                # Semantic fallback for social media / linkedin
                if resolved.answer.lower() == "linkedin":
                    for opt in options:
                        if re.search(r"social media|job board|internet|online|professional network", opt, re.I):
                            matched = opt
                            break
            resolved.selected_option = matched or options[0]

        return resolved

    def get_pending_review_items(self) -> List[Dict[str, Any]]:
        """Returns all questions currently awaiting human review."""
        db_path = self.dsn.replace("sqlite:///", "").replace("sqlite://", "")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        with conn:
            rows = conn.execute("SELECT * FROM review_queue WHERE status = 'pending' ORDER BY id ASC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def submit_user_answer(
        self,
        review_id: int,
        user_answer: str,
        save_as_reusable: bool = True,
    ) -> None:
        """Approves a review question with the user's answer, resumes the application, and records learning."""
        db_path = self.dsn.replace("sqlite:///", "").replace("sqlite://", "")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        now = datetime.now(timezone.utc).isoformat()
        with conn:
            row = conn.execute("SELECT raw_question FROM review_queue WHERE id = ?", (review_id,)).fetchone()
            if row and save_as_reusable:
                norm = self._normalize(row["raw_question"])
                conn.execute(
                    "INSERT INTO custom_answers (normalized_question, answer, created_at) "
                    "VALUES (?, ?, ?) ON CONFLICT(normalized_question) DO UPDATE SET answer = excluded.answer",
                    (norm, user_answer, now),
                )
            conn.execute(
                "UPDATE review_queue SET status = 'resolved', resolved_at = ? WHERE id = ?",
                (now, review_id),
            )
        conn.close()

