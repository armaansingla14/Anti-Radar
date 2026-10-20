"""Runtime configuration. Every external credential is an environment variable.

Environments (``APP_ENV``):

* ``development`` — local dev; localhost defaults are fine, writes are open unless ADMIN_TOKEN is set.
* ``demo``        — the public verified-snapshot backend (``DEMO_SNAPSHOT=true``); no admin token required.
* ``production``  — the live backend; startup validation (``radar.config_check``) requires a non-local
                    DATABASE_URL, an https FRONTEND_URL and an ADMIN_TOKEN.
"""

from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_URL = "postgresql+psycopg://radar:radar@localhost:5432/radar"

AppEnv = Literal["development", "demo", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT / ".env", ".env"), extra="ignore", populate_by_name=True)

    # Environment
    app_env: AppEnv = "development"

    # Database. DATABASE_URL is the runtime (pooled) URL; DATABASE_URL_DIRECT bypasses the pooler and is used
    # by Alembic migrations (Neon: the non "-pooler" host).
    database_url: str = DEFAULT_DATABASE_URL
    database_url_direct: str | None = None
    db_pool_size: int = 10
    db_max_overflow: int = 10
    thesis_path: Path = ROOT / "config" / "anti_fund_thesis.yaml"

    # Sources
    # GitHub Actions forbids secrets named GITHUB_*, so RADAR_GITHUB_TOKEN is accepted too (and wins).
    github_token: str | None = Field(
        default=None, validation_alias=AliasChoices("RADAR_GITHUB_TOKEN", "GITHUB_TOKEN", "github_token"))
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
    # Include the clearly-labeled fictional (SYNTHETIC) dataset. DEMO_MODE is the legacy name.
    demo_synthetic: bool = Field(
        default=False, validation_alias=AliasChoices("DEMO_SYNTHETIC", "DEMO_MODE", "demo_synthetic"))
    # This backend serves the verified real-data snapshot: the clock stays pinned to the snapshot as-of date.
    demo_snapshot: bool = False
    cors_origins: str = "http://localhost:3000"
    frontend_url: str | None = None
    today_override: str | None = None  # ISO date; makes "today" deterministic for demos/tests

    # Security: when set, non-GET API requests need header X-Radar-Admin == admin_token.
    admin_token: str | None = None
    # Local production-parity runs only: downgrade the localhost / https checks to warnings.
    allow_local_production: bool = False

    @field_validator("database_url_direct", "github_token", "x_bearer_token", "anthropic_api_key",
                     "openai_api_key", "frontend_url", "today_override", "admin_token", mode="before")
    @classmethod
    def _blank_is_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v.strip() if isinstance(v, str) else v

    @field_validator("database_url", "database_url_direct", mode="after")
    @classmethod
    def _psycopg_driver(cls, v):
        """Neon/Render hand out ``postgres://`` / ``postgresql://`` URLs; RADAR uses psycopg 3."""
        if isinstance(v, str):
            for prefix in ("postgres://", "postgresql://"):
                if v.startswith(prefix):
                    return "postgresql+psycopg://" + v[len(prefix):]
        return v

    @property
    def demo_mode(self) -> bool:
        """Back-compat alias for ``demo_synthetic`` (read-only)."""
        return self.demo_synthetic

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_deployed(self) -> bool:
        """A hosted environment (production or the public demo backend)."""
        return self.app_env in ("production", "demo")

    @property
    def migration_url(self) -> str:
        """URL for schema migrations: the direct (non-pooled) URL when configured."""
        return self.database_url_direct or self.database_url

    def cors_origin_list(self) -> list[str]:
        origins = [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]
        if self.frontend_url:
            fu = self.frontend_url.rstrip("/")
            if fu not in origins:
                origins.append(fu)
        return origins

    def resolved_github_token(self) -> str | None:
        """GITHUB_TOKEN / RADAR_GITHUB_TOKEN, else (development only) a locally authenticated `gh` CLI."""
        if self.github_token:
            return self.github_token
        if self.app_env != "development":
            return None
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
