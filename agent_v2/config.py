"""
agent_v2 configuration module.

Defines environment variables, API endpoints, timeouts, and execution parameters.
Includes anti-loop limits such as MAX_NODE_ATTEMPTS.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# Anti-loop guard: maximum visits per node before terminating or triggering fallback
MAX_NODE_ATTEMPTS: int = 3


@dataclass(frozen=True)
class AppConfig:
    # Execution timeouts & retries
    max_node_attempts: int = MAX_NODE_ATTEMPTS
    page_timeout_ms: int = 30_000
    action_timeout_ms: int = 10_000
    navigation_timeout_ms: int = 45_000
    settle_delay_ms: int = 1_500

    # Browser parameters
    headless: bool = field(
        default_factory=lambda: os.getenv("BROWSER_HEADLESS", "false").strip().lower() in {"1", "true", "yes"}
    )
    user_data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("BROWSER_PROFILE_DIR", "browser_profile")).resolve()
    )

    # Database & Storage
    db_path: Path = field(
        default_factory=lambda: Path(os.getenv("SQLITE_DB_PATH", "data/agent_v2.db")).resolve()
    )
    encryption_key: str = field(
        default_factory=lambda: os.getenv("APP_ENCRYPTION_KEY", "")
    )

    # LLM Settings
    default_llm_provider: Literal["openai", "anthropic", "gemini"] = field(
        default_factory=lambda: os.getenv("LLM_PROVIDER", "anthropic").strip().lower()  # type: ignore
    )
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))

    # Profile & ATS credentials
    ats_email: str = field(default_factory=lambda: os.getenv("ATS_EMAIL", "candidate@example.com"))
    ats_password: str = field(default_factory=lambda: os.getenv("ATS_PASSWORD", ""))

    @classmethod
    def load(cls) -> "AppConfig":
        """Load configuration from environment variables or .env file."""
        env_file = Path(".env")
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key.strip(), val.strip().strip("\"'"))
        return cls()


config = AppConfig.load()

