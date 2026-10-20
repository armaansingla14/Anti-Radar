"""Single source of truth for data providers: what exists, how often it should run, and whether it can run.

Consumed by ``services/status.py`` (``/api/status``) and ``config_check`` (startup warnings).
Availability is derived from configuration only (no network calls):

* ``live``      credentials (if any) present, provider can run at full capacity
* ``degraded``  runs, but with a known handicap (e.g. GitHub without a token: 60 req/h)
* ``disabled``  cannot run in this deployment; ``reason`` says why

The fictional dataset is *not* a provider (it is labeled SYNTHETIC elsewhere), and the snapshot
replay is a data source, not a provider, so neither appears here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from radar.settings import Settings, get_settings

Availability = Literal["live", "degraded", "disabled"]


@dataclass(frozen=True)
class ProviderDescriptor:
    key: str
    label: str
    family: str  # source family used by collision scoring (code, research, launch, web, hiring, social)
    cadence_hours: float | None  # expected interval between successful runs (None: on demand)
    # IngestionRun.provider values whose runs count for this provider
    run_providers: tuple[str, ...]
    credential_env: str | None = None
    rate_limit: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class ProviderAvailability:
    key: str
    state: Availability
    reason: str | None = None
    rate_limit: str | None = None

    @property
    def enabled(self) -> bool:
        return self.state != "disabled"


PROVIDERS: tuple[ProviderDescriptor, ...] = (
    ProviderDescriptor("github", "GitHub", "code", 12, ("github",), credential_env="RADAR_GITHUB_TOKEN",
                       rate_limit="5,000 req/h with a token; 60 req/h without"),
    ProviderDescriptor("hackernews", "Hacker News", "launch", 3, ("hackernews",),
                       rate_limit="Algolia public API"),
    ProviderDescriptor("arxiv", "arXiv", "research", 36, ("arxiv",), rate_limit="1 request / 3 s"),
    ProviderDescriptor("website", "Company websites", "web", 48, ("website",)),
    ProviderDescriptor("jobs", "Jobs (ATS)", "hiring", 48, ("jobs",),
                       note="Greenhouse / Lever / Ashby boards found on company careers pages"),
    ProviderDescriptor("x", "X (Twitter)", "social", 24, ("x",), credential_env="X_BEARER_TOKEN",
                       note="manual post ingest stays available without API access"),
)

BY_KEY = {p.key: p for p in PROVIDERS}

# Pseudo-providers that write IngestionRun rows but are not data providers.
PSEUDO_PROVIDERS = ("snapshot", "demo")


def descriptors() -> tuple[ProviderDescriptor, ...]:
    return PROVIDERS


def get(key: str) -> ProviderDescriptor:
    return BY_KEY[key]


def availability(key: str, settings: Settings | None = None, *,
                 github_authenticated: bool | None = None) -> ProviderAvailability:
    """Configuration-derived availability for one provider.

    ``github_authenticated`` lets callers pass a cached token probe (the dev ``gh`` fallback spawns a process).
    """
    s = settings or get_settings()
    d = BY_KEY[key]
    if key == "github":
        authed = bool(s.resolved_github_token()) if github_authenticated is None else github_authenticated
        if not authed:
            return ProviderAvailability(key, "degraded", "unauthenticated — 60 req/h", "60 req/h")
        return ProviderAvailability(key, "live", None, "5,000 req/h")
    if key == "x":
        if not s.x_bearer_token:
            return ProviderAvailability(key, "disabled", "API access not configured (X_BEARER_TOKEN)")
        return ProviderAvailability(key, "live", None, None)
    return ProviderAvailability(key, "live", None, d.rate_limit)


def all_availability(settings: Settings | None = None, *,
                     github_authenticated: bool | None = None) -> dict[str, ProviderAvailability]:
    s = settings or get_settings()
    return {d.key: availability(d.key, s, github_authenticated=github_authenticated) for d in PROVIDERS}


def llm_availability(settings: Settings | None = None) -> ProviderAvailability:
    """Whether live LLM calls are possible (cached outputs and deterministic templates always work)."""
    s = settings or get_settings()
    name = (s.llm_provider or "none").lower()
    if name == "none":
        return ProviderAvailability("llm", "disabled", "LLM_PROVIDER=none — deterministic templates and cached text")
    if name == "anthropic":
        if not s.anthropic_api_key:
            return ProviderAvailability("llm", "disabled", "ANTHROPIC_API_KEY not configured")
    elif name == "openai":
        if not s.openai_api_key:
            return ProviderAvailability("llm", "disabled", "OPENAI_API_KEY not configured")
    else:
        return ProviderAvailability("llm", "disabled", f"unknown LLM_PROVIDER '{name}'")
    cap = s.llm_daily_usd_cap
    return ProviderAvailability("llm", "live", None, f"${cap:.2f}/day cap" if cap and cap > 0 else "no daily cap")
