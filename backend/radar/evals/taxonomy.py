"""The evaluation vocabulary: failure categories, severities, review statuses and the agent registry.

Definitions live here once; the evaluator, the API (``taxonomy``) and docs/AGENT_EVALUATION.md read them, so
the meaning of a category cannot drift between what the code flags and what the reader is told.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

EVALUATOR_VERSION = "eval-1"


class FailureCategory(StrEnum):
    ATTRIBUTION_ERROR = "ATTRIBUTION_ERROR"
    UNSUPPORTED_CLAIM = "UNSUPPORTED_CLAIM"
    CITATION_MISMATCH = "CITATION_MISMATCH"
    AFFILIATION_INFERENCE = "AFFILIATION_INFERENCE"
    OVERCONFIDENT_INFERENCE = "OVERCONFIDENT_INFERENCE"
    MISSING_COUNTEREVIDENCE = "MISSING_COUNTEREVIDENCE"
    STALE_INFORMATION = "STALE_INFORMATION"
    SOURCE_QUALITY = "SOURCE_QUALITY"
    ENTITY_RESOLUTION_ERROR = "ENTITY_RESOLUTION_ERROR"
    RANKING_ERROR = "RANKING_ERROR"
    PROMPT_INJECTION_ATTEMPT = "PROMPT_INJECTION_ATTEMPT"
    TOOL_FAILURE = "TOOL_FAILURE"
    STRUCTURED_OUTPUT_FAILURE = "STRUCTURED_OUTPUT_FAILURE"


CATEGORY_DEFINITIONS: dict[str, str] = {
    FailureCategory.ATTRIBUTION_ERROR: "Something said or done by one person or organization credited to another "
    "(e.g. a repository's work attributed to a minor contributor).",
    FailureCategory.UNSUPPORTED_CLAIM: "A statement presented as established that no cited source supports.",
    FailureCategory.CITATION_MISMATCH: "A citation that does not exist, or whose source does not appear to say "
                                       "what the claim says.",
    FailureCategory.AFFILIATION_INFERENCE: "Employment or affiliation inferred from weak evidence (a talk, an event, "
                                           "a paper, a self-reported field) and stated as fact.",
    FailureCategory.OVERCONFIDENT_INFERENCE: "An inference labelled or ranked with more confidence than its evidence "
                                             "allows (hedged FACT, thin evidence ranked high).",
    FailureCategory.MISSING_COUNTEREVIDENCE: "Available evidence against the conclusion was ignored.",
    FailureCategory.STALE_INFORMATION: "A claim rests on a source too old to describe the present.",
    FailureCategory.SOURCE_QUALITY: "The cited source is the wrong kind for the claim (own marketing for customers, "
                                    "noise repositories for formation, partial coverage for team convergence).",
    FailureCategory.ENTITY_RESOLUTION_ERROR: "Records about different entities merged, or a non-person (bot, tool "
                                             "account) treated as a person.",
    FailureCategory.RANKING_ERROR: "A deterministic score or ranking placed an entity where its evidence does not "
                                   "put it, without any single claim being wrong.",
    FailureCategory.PROMPT_INJECTION_ATTEMPT: "A source contained instructions aimed at the model. Informational when "
                                              "the output ignored them; HIGH when the output followed them.",
    FailureCategory.TOOL_FAILURE: "A tool call, fetch or model call failed during the run.",
    FailureCategory.STRUCTURED_OUTPUT_FAILURE: "The output did not match its schema, or the model output was invalid "
                                               "and replaced by the template.",
}


class Severity(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"  # flags only; a failure is HIGH / MEDIUM / LOW


FAILURE_SEVERITIES: tuple[str, ...] = (Severity.HIGH, Severity.MEDIUM, Severity.LOW)


class ReviewStatus(StrEnum):
    AUTOMATIC = "AUTOMATIC"
    HUMAN_REVIEWED = "HUMAN_REVIEWED"


FAILURE_STATUSES: tuple[str, ...] = ("OPEN", "CONFIRMED", "FIXED", "DISMISSED")


@dataclass(frozen=True)
class Agent:
    key: str
    name: str
    kind: str | None  # ResearchReport.kind this agent writes (None: it writes no report)
    purposes: tuple[str, ...]  # LLMCall.purpose values
    description: str
    deterministic: bool = False
    extra_kinds: tuple[str, ...] = ()  # further ResearchReport kinds written by the same agent

    @property
    def kinds(self) -> tuple[str, ...]:
        return ((self.kind,) if self.kind else ()) + self.extra_kinds


AGENTS: tuple[Agent, ...] = (
    Agent("analyst", "RADAR ANALYST", "brief", ("analyst.investigate", "analyst.brief"),
          "Investigates one company or person and writes a sourced Anti Brief (tool loop, or a template)."),
    Agent("meeting", "MEETING BRIEF", "meeting_brief", ("analyst.meeting_brief",),
          "One-page pre-meeting brief on a person from their own evidence."),
    Agent("memo", "ANTI MEMO", "memo", ("analyst.memo",),
          "Compresses an Anti Brief into a one-page memo with the same sources."),
    Agent("morning", "MORNING BRIEF", "morning_brief", ("narrative.morning_brief",),
          "The daily brief: what changed, founders and companies to know."),
    Agent("diligence", "DILIGENCE ANALYST", "diligence", ("diligence.extract",),
          "Extracts a company's material claims and checks them against public evidence."),
    Agent("portfolio", "PORTFOLIO ANALYST", "portfolio_review", (),
          "Portfolio Pulse: per-company change memos and the daily Portfolio Brief from typed, rule-classified "
          "material events (deterministic templates).", True, ("portfolio_brief",)),
    Agent("narratives", "NARRATIVES", None,
          ("narrative.why_now", "narrative.why_anti", "narrative.risks", "narrative.paper"),
          "Short generated narratives on entity pages (why now, why Anti Fund, risks, paper summaries)."),
    Agent("search", "SEARCH PARSER", None, ("search.parse",), "Turns a natural-language search into filters."),
    Agent("thesis", "THESIS CLASSIFIER", None, ("topics.refine",), "Refines thesis topic tags for an entity."),
)

# Deterministic components: they own failures (ranking / resolution / copy) but write no evaluated report.
COMPONENTS: tuple[Agent, ...] = (
    Agent("formation", "FORMATION SCORER", None, (), "Formation candidates and their tiers (scoring/formation.py).",
          True),
    Agent("meet", "MEET RANKER", None, (), "Who should we meet this week (services/meet.py).", True),
    Agent("resolution", "ENTITY RESOLUTION", None, (), "People/organisation resolution and bot filtering.", True),
    Agent("presenters", "PRESENTERS", None, (), "Deterministic page copy (services/presenters.py).", True),
    Agent("guard", "INJECTION GUARD", None, (), "Prompt-injection detection on untrusted content (llm/guard.py).",
          True),
)

_ALIASES = {
    "analyst": "RADAR ANALYST", "brief": "RADAR ANALYST", "meeting": "MEETING BRIEF", "meeting_brief": "MEETING BRIEF",
    "memo": "ANTI MEMO", "diligence": "DILIGENCE ANALYST", "morning": "MORNING BRIEF",
    "morning_brief": "MORNING BRIEF", "narratives": "NARRATIVES", "search": "SEARCH PARSER",
    "thesis": "THESIS CLASSIFIER", "formation": "FORMATION SCORER", "meet": "MEET RANKER",
    "resolution": "ENTITY RESOLUTION", "presenters": "PRESENTERS", "guard": "INJECTION GUARD",
    "portfolio": "PORTFOLIO ANALYST", "portfolio_review": "PORTFOLIO ANALYST", "portfolio_brief": "PORTFOLIO ANALYST",
}


def all_agents() -> tuple[Agent, ...]:
    return AGENTS + COMPONENTS


def agent_by_name(name: str) -> Agent | None:
    return next((a for a in all_agents() if a.name == name), None)


def agent_for_kind(kind: str | None) -> Agent | None:
    return next((a for a in AGENTS if kind and kind in a.kinds), None)


def agent_for_purpose(purpose: str | None) -> Agent | None:
    return next((a for a in AGENTS if purpose and purpose in a.purposes), None)


def resolve_agent_alias(alias: str | None) -> str | None:
    """'analyst' / 'RADAR ANALYST' / 'radar analyst' -> 'RADAR ANALYST'; None when unknown."""
    if not alias:
        return None
    a = alias.strip()
    if a.lower() in _ALIASES:
        return _ALIASES[a.lower()]
    for ag in all_agents():
        if ag.name.lower() == a.lower() or ag.key == a.lower():
            return ag.name
    return None


def known_agent_names() -> set[str]:
    return {a.name for a in all_agents()}
