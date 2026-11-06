"""Public portfolio configuration: ``config/anti_fund_portfolio.yaml``.

The file lists publicly disclosed portfolio companies (as stated on the public source, e.g. antifund.com) with
hand-verified identifiers only, and for each company its comps with a type, the reason it is comparable, a public
source URL and a confidence. Validation is strict:

* unknown keys are rejected (a typo must not silently drop an identifier);
* comp types, statuses, confidences and ATS providers are enumerated;
* every comp has a ``source_url``;
* private investment fields (ownership, cost basis, marks, reserves, board status, internal notes…) are refused
  anywhere in a public config, with a message naming the key. ``PortfolioPosition.private_fields`` exists for a
  future firm mode only and stays ``{}`` in public mode.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from radar.providers.domains import company_domain, registrable_domain

DEFAULT_PORTFOLIO_NAME = "Anti Fund"
CONFIG_FILENAME = "anti_fund_portfolio.yaml"

CompType = Literal["DIRECT", "ADJACENT", "INFRASTRUCTURE", "INCUMBENT", "OPEN_SOURCE"]
CompConfidence = Literal["HIGH", "MEDIUM", "LOW"]
PositionStatus = Literal["active", "acquired", "public", "exited"]
AtsProvider = Literal["greenhouse", "lever", "ashby"]

# Keys that describe a private position. Never accepted in a public config (any nesting level).
PRIVATE_KEYS = frozenset({
    "ownership", "ownership_pct", "ownership_percent", "cost_basis", "cost", "mark", "marks", "fair_value",
    "reserves", "reserve", "round_participation", "participation", "check_size", "investment_amount", "amount_invested",
    "board_status", "board_seat", "board", "internal_owner", "owner", "internal_notes", "notes_internal",
    "private_notes", "private_revenue", "revenue", "arr", "mrr", "customer_count", "customers_count",
    "internal_metrics", "private_fields", "valuation", "pro_rata", "moic", "irr",
})


class PortfolioConfigError(ValueError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AtsBoard(_Strict):
    provider: AtsProvider
    slug: str = Field(min_length=1, max_length=100)

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,99}", v):
            raise ValueError(f"malformed ATS slug {v!r}")
        return v


def config_domain(website: str | None) -> str | None:
    """The configured identity domain: the company domain, or the registrable domain for a company whose own site
    is a platform host (huggingface.co is Hugging Face itself)."""
    return company_domain(website) or registrable_domain(website)


def _website(v: str) -> str:
    v = v.strip()
    if "://" not in v:
        v = f"https://{v}"
    if not v.startswith(("https://", "http://")) or not config_domain(v):
        raise ValueError(f"{v!r} is not a website URL")
    return v.rstrip("/")


def _github_org(v: str | None) -> str | None:
    if v is None:
        return None
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", v):
        raise ValueError(f"malformed GitHub org {v!r}")
    return v


class CompConfig(_Strict):
    name: str = Field(min_length=1, max_length=300)
    website: str
    github_org: str | None = None
    ats: AtsBoard | None = None
    usaspending_recipient: str | None = Field(default=None, max_length=300)
    type: CompType
    why_comparable: str = Field(min_length=10, max_length=1000)
    source_url: str = Field(min_length=8, max_length=2000)
    confidence: CompConfidence

    @field_validator("website")
    @classmethod
    def _check_website(cls, v: str) -> str:
        return _website(v)

    @field_validator("github_org")
    @classmethod
    def _check_github_org(cls, v: str | None) -> str | None:
        return _github_org(v)

    @field_validator("source_url")
    @classmethod
    def _src(cls, v: str) -> str:
        if not v.startswith(("https://", "http://")):
            raise ValueError("comp source_url must be an http(s) URL")
        return v


class CompanyConfig(_Strict):
    name: str = Field(min_length=1, max_length=300)
    slug: str | None = Field(default=None, max_length=200)
    website: str
    github_org: str | None = None
    ats: AtsBoard | None = None
    newsroom_url: str | None = None
    usaspending_recipient: str | None = Field(default=None, max_length=300)
    public_stage: str | None = Field(default=None, max_length=80)  # exactly as the public source states it
    public_year: int | None = Field(default=None, ge=1990, le=2100)
    sector: str | None = Field(default=None, max_length=200)
    thesis_tags: list[str] = Field(default_factory=list)
    status: PositionStatus = "active"
    note: str | None = Field(default=None, max_length=1000)  # e.g. why an identifier was left out
    comps: list[CompConfig] = Field(default_factory=list)

    @field_validator("website")
    @classmethod
    def _check_website(cls, v: str) -> str:
        return _website(v)

    @field_validator("github_org")
    @classmethod
    def _check_github_org(cls, v: str | None) -> str | None:
        return _github_org(v)

    @field_validator("newsroom_url")
    @classmethod
    def _newsroom(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v if v.startswith(("https://", "http://")) else f"https://{v}"

    @model_validator(mode="after")
    def _unique_comps(self) -> CompanyConfig:
        # newsroom_url may sit on the domain the website redirects to (physicalintelligence.company -> pi.website).
        names = [c.name.lower() for c in self.comps]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate comp for {self.name}")
        return self

    @property
    def domain(self) -> str | None:
        return config_domain(self.website)


class SourceConfig(_Strict):
    url: str = Field(min_length=8)
    checked_at: date
    note: str | None = None


class PortfolioConfig(_Strict):
    portfolio_name: str = DEFAULT_PORTFOLIO_NAME
    source: SourceConfig
    companies: list[CompanyConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> PortfolioConfig:
        doms = [c.domain for c in self.companies]
        dup = sorted({d for d in doms if d and doms.count(d) > 1})
        if dup:
            raise ValueError(f"duplicate portfolio company domain(s): {', '.join(dup)}")
        return self


def find_private_keys(data: Any, path: str = "") -> list[str]:
    """Dotted paths of every private-position key in ``data`` (any nesting level)."""
    out: list[str] = []
    if isinstance(data, dict):
        for k, v in data.items():
            p = f"{path}.{k}" if path else str(k)
            if str(k).strip().lower() in PRIVATE_KEYS:
                out.append(p)
            out += find_private_keys(v, p)
    elif isinstance(data, list):
        for i, v in enumerate(data):
            out += find_private_keys(v, f"{path}[{i}]")
    return out


def parse_config(data: Any) -> PortfolioConfig:
    if not isinstance(data, dict):
        raise PortfolioConfigError("portfolio config must be a mapping")
    private = find_private_keys(data)
    if private:
        raise PortfolioConfigError(
            "private investment fields are not allowed in a public portfolio config: " + ", ".join(private))
    try:
        return PortfolioConfig.model_validate(data)
    except ValidationError as e:
        raise PortfolioConfigError(f"invalid portfolio config: {e}") from None


def default_path() -> Path:
    from radar.settings import ROOT, get_settings

    s = get_settings()
    configured = getattr(s, "portfolio_path", None)
    if configured:
        return Path(configured)
    candidates = [ROOT / "config" / CONFIG_FILENAME, Path(s.thesis_path).parent / CONFIG_FILENAME]
    return next((p for p in candidates if p.exists()), candidates[0])


def load_config(path: Path | str | None = None) -> PortfolioConfig:
    p = Path(path) if path else default_path()
    if not p.exists():
        raise PortfolioConfigError(f"portfolio config not found: {p} (write config/{CONFIG_FILENAME})")
    try:
        data = yaml.safe_load(p.read_text())
    except yaml.YAMLError as e:
        raise PortfolioConfigError(f"{p.name}: invalid YAML: {e}") from None
    return parse_config(data)
