"""Anti Memo: a concise one-page memo compressed from an Anti Brief.

The memo reuses the brief's source list (same S-ids), so citations are validated
against exactly the sources the brief was built from. ``anti_score_summary`` is
always filled deterministically from the latest ScoreSnapshot — never by a model.
"""

from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.agent.analyst import investigate
from radar.agent.brief_schema import AntiBrief, AntiMemo, AntiMemoDraft, Claim
from radar.agent.render import memo_to_markdown
from radar.agent.tools import latest_score, resolve_entity
from radar.llm.guard import SYSTEM_GUARD, derived_block, inline_untrusted, validate_citations
from radar.llm.prompts import load_prompt
from radar.llm.provider import BaseProvider, LLMError, NullProvider, get_llm
from radar.models import Company, ResearchReport, ScoreSnapshot

log = logging.getLogger(__name__)

_COMPONENT_LABELS = {
    "technical_momentum": "technical momentum", "founder_signal": "founder signal", "acceleration": "acceleration",
    "thesis_fit": "thesis fit", "novelty": "novelty", "attention_gap": "attention gap",
}


def anti_score_summary(score: ScoreSnapshot | None) -> str:
    """Deterministic one-paragraph score summary."""
    if score is None:
        return "No Anti Score has been computed for this entity yet."
    parts = [f"Anti Score {score.score:.0f}/100 as of {score.day.isoformat()} (model {score.version})."]
    comps = []
    for key, comp in (score.components or {}).items():
        if isinstance(comp, dict) and comp.get("value") is not None:
            v, m = comp["value"], comp.get("max")
            comps.append(f"{_COMPONENT_LABELS.get(key, key.replace('_', ' '))} {float(v):.1f}"
                         + (f"/{float(m):.0f}" if m is not None else ""))
    if comps:
        parts.append("Components: " + ", ".join(comps) + ".")
    col = score.collision or {}
    if col.get("summary"):
        parts.append(f"Signal collision: {col['summary']}.")
    att = score.attention or {}
    if att.get("flagged"):
        parts.append(f"Attention gap flagged (technical {att.get('technical')} vs attention {att.get('attention')}).")
    return " ".join(parts)


def _first(claims: list[Claim], n: int) -> list[Claim]:
    return [c.model_copy() for c in claims[:n]]


def template_memo(brief: AntiBrief) -> AntiMemoDraft:
    """Deterministic compression: the leading claims of each brief section."""
    if brief.what_they_do:
        one_liner = brief.what_they_do[0].text
    else:
        one_liner = f"{brief.subject}: what they build is unknown from collected evidence."
    return AntiMemoDraft(
        company=brief.subject, one_liner=one_liner,
        why_on_radar=_first(brief.why_this_could_matter, 2) + _first(brief.signals, 2),
        why_now=_first(brief.why_now, 2) + _first(brief.what_changed, 2),
        founders=_first(brief.founders, 4), technical_insight=_first(brief.technical_edge, 3),
        what_others_may_be_missing=[], market=_first(brief.market, 2), competition=_first(brief.competitors, 3),
        evidence=_first(brief.signals, 4),
        risks=_first(brief.why_this_could_be_wrong, 3) + _first(brief.contradictory_signals, 2),
        questions_to_answer=brief.questions_to_ask_founders[:6],
    )


def _select_brief(session: Session, entity_id: uuid.UUID, report_id: str | uuid.UUID | None) -> ResearchReport | None:
    if report_id:
        r = session.get(ResearchReport, uuid.UUID(str(report_id)))
        if r is None or r.kind != "brief" or r.entity_id != entity_id:
            raise ValueError(f"report {report_id} is not a brief for this entity")
        return r
    return session.scalar(
        select(ResearchReport).where(ResearchReport.entity_id == entity_id, ResearchReport.kind == "brief",
                                     ResearchReport.status == "complete")
        .order_by(ResearchReport.created_at.desc()).limit(1)
    )


def generate_memo(
    session: Session,
    entity_type: str,
    entity_id: str | uuid.UUID,
    report_id: str | uuid.UUID | None = None,
    *,
    provider: BaseProvider | None = None,
) -> ResearchReport:
    """Create and persist a ResearchReport (kind 'memo') for the entity."""
    entity = resolve_entity(session, entity_type, str(entity_id))
    base = _select_brief(session, entity.id, report_id)
    if base is None:
        base = investigate(session, entity_type, entity.id, live_fetch=False, provider=NullProvider())
    brief = AntiBrief.model_validate(base.body)
    sources = list(base.sources or [])
    valid_ids = {s["id"] for s in sources if isinstance(s, dict) and "id" in s}
    provider = provider or get_llm()

    draft: AntiMemoDraft | None = None
    generator, model, cost = "template", None, 0.0
    validation: dict | None = None
    try:
        # Brief JSON and source titles/URLs carry external text: escaped and fenced as data, never instructions.
        src_list = "\n".join(f"{inline_untrusted(str(s['id']), 12)}: {inline_untrusted(s.get('title'), 200)} "
                             f"({inline_untrusted(s.get('source_type'), 30)}) {inline_untrusted(s.get('url'), 500)}"
                             for s in sources)
        user = (f"Brief (JSON):\n{derived_block(json.dumps(brief.model_dump(mode='json'), ensure_ascii=False), 'brief')}"
                f"\n\nSources the ids refer to:\n{derived_block(src_list, 'source list')}")
        res = provider.structured("analyst.memo", "deep", SYSTEM_GUARD + "\n\n" + load_prompt("memo"), user,
                                  AntiMemoDraft, cache=True, max_tokens=8000)
        draft = res.parsed
        validation = validate_citations(draft, valid_ids).as_dict()
        generator = "cached" if res.cached else "llm"
        model, cost = res.model, res.cost_usd
    except LLMError as e:
        log.info("memo falling back to template: %s", e)
    if draft is None:
        draft = template_memo(brief)
        validation = validate_citations(draft, valid_ids).as_dict()

    draft.company = entity.name
    memo = AntiMemo(**draft.model_dump(), anti_score_summary=anti_score_summary(latest_score(session, entity.id)))
    report = ResearchReport(
        kind="memo", entity_type="company" if isinstance(entity, Company) else "person", entity_id=entity.id,
        title=f"Anti Memo — {entity.name}", body=memo.model_dump(mode="json"),
        markdown=memo_to_markdown(memo, sources, generator=generator, model=model), sources=sources, model=model,
        generator=generator, status="complete", cost_usd=cost, is_demo=entity.is_demo,
        trace=[{"type": "based_on", "report_id": str(base.id), "generator": base.generator},
               {"type": "validation", **(validation or {})}],
    )
    session.add(report)
    session.flush()
    return report
