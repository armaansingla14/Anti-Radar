"""Structured outputs of the RADAR ANALYST: the Anti Brief and the Anti Memo.

Schemas stay within what structured outputs support (objects, arrays, strings,
enums; every field required, no numeric/length constraints) so the same Pydantic
classes are used for the API call, validation and persistence.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ClaimEpistemic = Literal["FACT", "INFERENCE", "MODEL_OPINION"]


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="One self-contained statement. Do not put citations in the text.")
    epistemic: ClaimEpistemic = Field(
        description="FACT only if directly stated in a cited source; INFERENCE if deduced; MODEL_OPINION otherwise."
    )
    source_ids: list[str] = Field(description="Ids of the <source> blocks supporting this claim, e.g. ['S1','S4'].")

    @field_validator("text")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("claim text must not be empty")
        return v


class AntiBrief(BaseModel):
    """Full research brief from an investigation."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(description="Name of the company or person investigated.")
    date: str = Field(description="ISO date of the brief.")
    what_they_do: list[Claim]
    why_now: list[Claim] = Field(description="Why this is surfacing now: recent changes and their timing.")
    what_changed: list[Claim] = Field(description="Concrete recent changes in the signals.")
    founders: list[Claim]
    technical_edge: list[Claim]
    market: list[Claim]
    competitors: list[Claim]
    why_this_could_matter: list[Claim]
    why_this_could_be_wrong: list[Claim] = Field(description="The strongest skeptical case.")
    contradictory_signals: list[Claim]
    questions_to_ask_founders: list[str]
    signals: list[Claim] = Field(description="The most important individual observed signals.")
    unknowns: list[str] = Field(description="Important things we could not establish from evidence.")


BRIEF_CLAIM_SECTIONS: tuple[str, ...] = (
    "what_they_do",
    "why_now",
    "what_changed",
    "founders",
    "technical_edge",
    "market",
    "competitors",
    "why_this_could_matter",
    "why_this_could_be_wrong",
    "contradictory_signals",
    "signals",
)


class AntiMemoDraft(BaseModel):
    """The LLM-produced part of the one-page Anti Memo."""

    model_config = ConfigDict(extra="forbid")

    company: str
    one_liner: str = Field(description="One neutral sentence describing what they build. No hype.")
    why_on_radar: list[Claim]
    why_now: list[Claim]
    founders: list[Claim]
    technical_insight: list[Claim]
    what_others_may_be_missing: list[Claim]
    market: list[Claim]
    competition: list[Claim]
    evidence: list[Claim]
    risks: list[Claim]
    questions_to_answer: list[str]


class AntiMemo(AntiMemoDraft):
    """Persisted memo: the draft plus a deterministic score summary (never written by an LLM)."""

    anti_score_summary: str


MEMO_CLAIM_SECTIONS: tuple[str, ...] = (
    "why_on_radar",
    "why_now",
    "founders",
    "technical_insight",
    "what_others_may_be_missing",
    "market",
    "competition",
    "evidence",
    "risks",
)


class MeetingBrief(BaseModel):
    """Pre-meeting brief on one person (``ResearchReport.kind == 'meeting_brief'``).

    Sources are restricted to the person's own evidence (their profile, signals, papers,
    repositories, organizations and formation candidate); citations to anything else are removed
    by the validator."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="The person's display name.")
    date: str = Field(description="ISO date of the brief.")
    context_30s: list[Claim] = Field(description="Who this is in two or three sentences: the 30-second context.")
    why_on_radar: list[Claim] = Field(description="Why ANTI RADAR surfaced this person.")
    what_they_built: list[Claim] = Field(description="Concrete things they built: repositories, papers, projects.")
    technical_background: list[Claim] = Field(description="Technical background and affiliations (label self-reported).")
    what_changed: list[Claim] = Field(description="Recent changes: new orgs, repos, papers, activity.")
    why_anti_fund: list[Claim] = Field(description="Why this could be relevant to Anti Fund's thesis.")
    questions: list[str] = Field(description="Exactly five questions to ask in the meeting.")
    things_to_verify: list[str] = Field(description="Claims to verify before or during the meeting.")
    unknowns: list[str] = Field(description="Important things the evidence does not establish.")


MEETING_CLAIM_SECTIONS: tuple[str, ...] = (
    "context_30s",
    "why_on_radar",
    "what_they_built",
    "technical_background",
    "what_changed",
    "why_anti_fund",
)
