"""
storage/db_manager.py - Async SQLite database controller using aiosqlite.
Manages tables: identities, user_credentials, form_field_dictionary with clean CRUD execution logic.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Optional
import aiosqlite

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS identities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    phone TEXT,
    address TEXT,
    linkedin_url TEXT,
    github_url TEXT,
    portfolio_url TEXT,
    raw_profile_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT UNIQUE NOT NULL,
    username TEXT NOT NULL,
    encrypted_password TEXT NOT NULL,
    auth_method TEXT DEFAULT 'password',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_credentials_domain ON user_credentials (domain);

CREATE TABLE IF NOT EXISTS form_field_dictionary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_hash TEXT UNIQUE NOT NULL,
    normalized_question TEXT NOT NULL,
    field_type TEXT DEFAULT 'text',
    verified_answer TEXT NOT NULL,
    domain TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_dictionary_hash ON form_field_dictionary (question_hash);
CREATE INDEX IF NOT EXISTS idx_dictionary_domain ON form_field_dictionary (domain);
"""


class AsyncDatabaseManager:
    """Async local relational storage manager backed by aiosqlite."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        """Create tables and indexes if they do not exist."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(SCHEMA_SQL)
            await db.commit()

    # -------------------------------------------------------------------------
    # Identities CRUD
    # -------------------------------------------------------------------------
    async def save_identity(
        self,
        full_name: str,
        email: str,
        phone: Optional[str] = None,
        address: Optional[str] = None,
        linkedin_url: Optional[str] = None,
        github_url: Optional[str] = None,
        portfolio_url: Optional[str] = None,
        raw_profile_json: Optional[str] = None,
    ) -> int:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        query = """
        INSERT INTO identities (full_name, email, phone, address, linkedin_url, github_url, portfolio_url, raw_profile_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(email) DO UPDATE SET
            full_name=excluded.full_name,
            phone=excluded.phone,
            address=excluded.address,
            linkedin_url=excluded.linkedin_url,
            github_url=excluded.github_url,
            portfolio_url=excluded.portfolio_url,
            raw_profile_json=excluded.raw_profile_json,
            updated_at=excluded.updated_at
        """
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                query,
                (full_name, email, phone, address, linkedin_url, github_url, portfolio_url, raw_profile_json, now, now),
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def get_identity(self, email: str) -> Optional[dict[str, Any]]:
        query = "SELECT * FROM identities WHERE email = ?"
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, (email,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    # -------------------------------------------------------------------------
    # User Credentials CRUD
    # -------------------------------------------------------------------------
    async def save_credential(
        self,
        domain: str,
        username: str,
        encrypted_password: str,
        auth_method: str = "password",
    ) -> None:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        normalized_domain = domain.lower().strip()
        query = """
        INSERT INTO user_credentials (domain, username, encrypted_password, auth_method, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(domain) DO UPDATE SET
            username=excluded.username,
            encrypted_password=excluded.encrypted_password,
            auth_method=excluded.auth_method,
            updated_at=excluded.updated_at
        """
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                query,
                (normalized_domain, username, encrypted_password, auth_method, now, now),
            )
            await db.commit()

    async def get_credential(self, domain: str) -> Optional[dict[str, Any]]:
        normalized_domain = domain.lower().strip()
        query = "SELECT * FROM user_credentials WHERE domain = ?"
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, (normalized_domain,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    # -------------------------------------------------------------------------
    # Form Field Dictionary CRUD
    # -------------------------------------------------------------------------
    async def save_form_answer(
        self,
        question_hash: str,
        normalized_question: str,
        verified_answer: str,
        field_type: str = "text",
        domain: Optional[str] = None,
    ) -> None:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        query = """
        INSERT INTO form_field_dictionary (question_hash, normalized_question, field_type, verified_answer, domain, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(question_hash) DO UPDATE SET
            normalized_question=excluded.normalized_question,
            verified_answer=excluded.verified_answer,
            field_type=excluded.field_type,
            domain=COALESCE(excluded.domain, form_field_dictionary.domain),
            updated_at=excluded.updated_at
        """
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                query,
                (question_hash, normalized_question, field_type, verified_answer, domain, now, now),
            )
            await db.commit()

    async def get_form_answer(self, question_hash: str) -> Optional[dict[str, Any]]:
        query = "SELECT * FROM form_field_dictionary WHERE question_hash = ?"
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, (question_hash,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def list_form_answers(self, domain: Optional[str] = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM form_field_dictionary"
        params: tuple = ()
        if domain:
            query += " WHERE domain = ?"
            params = (domain.lower().strip(),)
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

