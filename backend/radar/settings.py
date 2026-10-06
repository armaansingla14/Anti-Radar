"""Runtime configuration. Every external credential is an environment variable."""

from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT / ".env", ".env"), extra="ignore")

    database_url: str = "postgresql+psycopg://radar:radar@localhost:5432/radar"
    thesis_path: Path = ROOT / "config" / "anti_fund_thesis.yaml"

    # Sources
    github_token: str | None = None
    x_bearer_token: str | None = None
    http_user_agent: str = "AntiRadar/0.1 (+https://github.com/anti-radar; research crawler)"

    # LLM
    llm_provider: str = "anthropic"  # anthropic | openai | none
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    # Cheap model for classification / extraction; strong model for briefs, memos, investigation.
    llm_fast_model: str = "claude-haiku-4-5"
    llm_standard_model: str = "claude-sonnet-5-5"
    llm_deep_model: str = "claude-opus-5-5"
    openai_fast_model: str = "gpt-5-mini"
    openai_deep_model: str = "gpt-5"
    llm_max_usd_per_investigation: float = 1.50
    # Global spend cap across all live LLM calls per UTC day (summed from llm_calls). 0 or less disables it.
    llm_daily_usd_cap: float = 10.0
    # Live (uncached) LLM query-parse calls allowed per minute from search, process-wide.
    llm_search_per_minute: int = 20

    # Behaviour
    demo_mode: bool = True  # include demo/fictional entities in feeds (always labeled)
    cors_origins: str = "http://localhost:3000"
    today_override: str | None = None  # ISO date; makes "today" deterministic for demos/tests

    def resolved_github_token(self) -> str | None:
        """GITHUB_TOKEN, else fall back to a locally authenticated `gh` CLI (host dev only)."""
        if self.github_token:
            return self.github_token
        if shutil.which("gh"):
            try:
                out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
                tok = out.stdout.strip()
                return tok or None
            except Exception:  # noqa: BLE001
                return None
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
