"""Load and validate config/anti_fund_thesis.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from radar.settings import get_settings


class Subcategory(BaseModel):
    key: str = ""
    label: str
    keywords: list[str] = Field(default_factory=list)


class Theme(BaseModel):
    key: str = ""
    label: str
    weight: float = 1.0
    subcategories: dict[str, Subcategory]


class CollisionCfg(BaseModel):
    window_days: int = 42
    decay_days: float = 21
    independence_bonus: float = 0.5
    tight_span_days: int = 14
    tight_span_multiplier: float = 1.2
    saturation: float = 2.0


class AttentionCfg(BaseModel):
    flag_tech_min: float = 0.75
    flag_attention_max: float = 0.40
    low_coverage_penalty: float = 0.7


class FeedbackCfg(BaseModel):
    step: float = 0.05
    min_multiplier: float = 0.8
    max_multiplier: float = 1.2
    max_point_shift: float = 10


class NoveltyCfg(BaseModel):
    half_life_days: float = 45


class ScoringCfg(BaseModel):
    weights: dict[str, float]
    collision: CollisionCfg = CollisionCfg()
    novelty: NoveltyCfg = NoveltyCfg()
    attention_gap: AttentionCfg = AttentionCfg()
    cohort_min: int = 12
    feedback: FeedbackCfg = FeedbackCfg()


class ArxivCfg(BaseModel):
    categories: list[str]
    max_results_per_category: int = 40


class Thesis(BaseModel):
    fund: str
    tagline: str
    version: str | float
    stages: dict[str, float]
    themes: dict[str, Theme]
    discovery_queries: list[str]
    arxiv: ArxivCfg
    affiliation_tiers: dict[str, list[str]]
    formation_phrases: list[str]
    hiring_roles: dict[str, list[str]]
    scoring: ScoringCfg

    def model_post_init(self, _ctx) -> None:  # fill keys for convenience
        for tk, theme in self.themes.items():
            theme.key = tk
            for sk, sub in theme.subcategories.items():
                sub.key = f"{tk}.{sk}"

    def stage_multiplier(self, stage: str | None) -> float:
        return self.stages.get(stage or "unknown", self.stages.get("unknown", 0.85))

    def affiliation_tier(self, org: str | None) -> float:
        """1.0 for tier-1, 0.6 for tier-2, else 0. Case-insensitive whole-phrase match."""
        if not org:
            return 0.0
        o = org.lower()
        for name in self.affiliation_tiers.get("tier_1", []):
            if _phrase_in(name.lower(), o):
                return 1.0
        for name in self.affiliation_tiers.get("tier_2", []):
            if _phrase_in(name.lower(), o):
                return 0.6
        return 0.0

    def matched_affiliation(self, org: str | None) -> str | None:
        if not org:
            return None
        o = org.lower()
        for tier in ("tier_1", "tier_2"):
            for name in self.affiliation_tiers.get(tier, []):
                if _phrase_in(name.lower(), o):
                    return name
        return None


def _phrase_in(phrase: str, text: str) -> bool:
    import re

    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text) is not None


def load_thesis(path: Path | None = None) -> Thesis:
    p = Path(path or get_settings().thesis_path)
    with p.open() as f:
        return Thesis.model_validate(yaml.safe_load(f))


@lru_cache
def get_thesis() -> Thesis:
    return load_thesis()
