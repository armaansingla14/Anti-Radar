"""Stage 5: what an event means for each portfolio company (rule M8).

* The subject itself, when it is a portfolio company: relation SELF, at the event's own materiality.
* Every portfolio company that lists the subject as a comp: relation from the comp type (DIRECT → COMPETITOR,
  ADJACENT, INFRASTRUCTURE, INCUMBENT, OPEN_SOURCE). A MATERIAL event of a DIRECT comp is MATERIAL for the
  portfolio company; of any other comp type it is WATCH. A WATCH event of a DIRECT comp is WATCH; of any other
  comp type it is not recorded (too far removed). NOISE events never create impacts.

The impact reason quotes the comp's ``why_comparable`` from the config; the relation is always a judgement
(INFERENCE), so confidence is the event's confidence scaled by the comp's stated confidence.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from radar.portfolio.types import COMP_RELATION

COMP_CONFIDENCE = {"HIGH": 1.0, "MEDIUM": 0.8, "LOW": 0.6}


@dataclass(frozen=True)
class CompLink:
    portfolio_company_id: uuid.UUID
    portfolio_name: str
    comp_company_id: uuid.UUID
    comp_name: str
    comp_type: str
    why_comparable: str
    confidence: str


@dataclass(frozen=True)
class Impact:
    company_id: uuid.UUID
    relation: str
    reason: str
    confidence: float
    materiality: str
    rule: str


def comp_materiality(event_materiality: str, comp_type: str) -> str | None:
    """M8: the materiality of a comp's event for the portfolio company (None: not recorded)."""
    if event_materiality == "MATERIAL":
        return "MATERIAL" if comp_type == "DIRECT" else "WATCH"
    if event_materiality == "WATCH" and comp_type == "DIRECT":
        return "WATCH"
    return None


def impacts_for(*, subject_id: uuid.UUID, subject_name: str, materiality: str, confidence: float,
                portfolio_ids: set[uuid.UUID], comps_of_subject: list[CompLink]) -> list[Impact]:
    if materiality == "NOISE":
        return []
    out: list[Impact] = []
    if subject_id in portfolio_ids:
        out.append(Impact(subject_id, "SELF", "the portfolio company's own event", round(confidence, 3), materiality,
                          ""))
    for link in sorted(comps_of_subject, key=lambda x: (x.portfolio_name, str(x.portfolio_company_id))):
        if link.portfolio_company_id == subject_id:
            continue
        level = comp_materiality(materiality, link.comp_type)
        if level is None:
            continue
        rel = COMP_RELATION.get(link.comp_type, "ADJACENT")
        kind = link.comp_type.lower().replace("_", " ")
        article = "an" if kind[0] in "aeiou" else "a"
        reason = f"{subject_name} is {article} {kind} comp of {link.portfolio_name}: {link.why_comparable}"
        conf = round(confidence * COMP_CONFIDENCE.get(link.confidence, 0.6), 3)
        out.append(Impact(link.portfolio_company_id, rel, reason[:1000], conf, level, "M8"))
    return out
