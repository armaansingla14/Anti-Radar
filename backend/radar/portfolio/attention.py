"""Stage 6: Needs Attention — which portfolio companies to look at now, and why (deterministic).

Inputs are the impacts of the review window (own events and competitor events) for each portfolio company.

    score = 3 · material own + 2 · material via comps + 1 · watch (capped at 5)
            + 3 if any risk signal + (independent source families − 1) + recency (2 if the newest is <= 7 days old,
            1 if <= 14 days)

Priority: HIGH when >= 2 material events including one of the company's own, or any risk signal; MEDIUM when >= 1
material event or >= 3 to watch; LOW otherwise. Only HIGH / MEDIUM companies are surfaced, top ``TOP_N``.

Suggested action (first rule that applies; never "contact founder"):
RISK → REVIEW; a DIRECT competitor's launch or financing → UPDATE_COMPETITIVE_MAP; a performance / benchmark claim or
an event that bears on a diligence claim → RUN_DILIGENCE; a hiring cluster → PREPARE_FOUNDER_QUESTIONS; only WATCH
events → MONITOR; otherwise REVIEW. "What this could mean" and questions are templates, labelled INFERENCE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

TOP_N = 5
WATCH_CAP = 5
HIRING_SUBTYPES = frozenset({"specialized_hiring_cluster", "new_role_class", "hiring_acceleration"})
COMPETITIVE_SUBTYPES = frozenset({"product_launch", "major_release", "new_round", "acquisition",
                                  "strategic_investment"})
PERF_SUBTYPES = frozenset({"benchmark_claim"})
PRIORITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


@dataclass
class AttentionItem:
    event_id: str
    company_id: str  # the affected portfolio company
    company_name: str
    company_slug: str
    subject_name: str
    relation: str  # SELF | COMPETITOR | ADJACENT | ...
    materiality: str  # for the affected company
    event_type: str
    subtype: str
    title: str
    event_at: datetime
    families: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    performance_claim: bool = False
    diligence_claim: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


def event_action(*, materiality: str, event_type: str, subtype: str, relation: str = "SELF",
                 performance_claim: bool = False, diligence_claim: bool = False) -> str:
    """Suggested review for one event from one company's point of view."""
    if materiality == "NOISE":
        return "NO_ACTION"
    if event_type == "RISK":
        return "REVIEW"
    if relation == "COMPETITOR" and subtype in COMPETITIVE_SUBTYPES:
        return "UPDATE_COMPETITIVE_MAP"
    if relation == "SELF" and (subtype in PERF_SUBTYPES or performance_claim or diligence_claim):
        return "RUN_DILIGENCE"
    if relation == "SELF" and subtype in HIRING_SUBTYPES:
        return "PREPARE_FOUNDER_QUESTIONS"
    if materiality == "WATCH" or relation != "SELF":
        return "MONITOR"
    return "REVIEW"


def card_action(items: list[AttentionItem]) -> tuple[str, str]:
    """(action, reason) for a company card."""
    live = [i for i in items if i.materiality in ("MATERIAL", "WATCH")]
    if any(i.event_type == "RISK" for i in live):
        return "REVIEW", "a risk signal was detected"
    comp = next((i for i in live if i.relation == "COMPETITOR" and i.subtype in COMPETITIVE_SUBTYPES), None)
    if comp:
        return "UPDATE_COMPETITIVE_MAP", f"a direct competitor ({comp.subject_name}) announced a " \
                                         f"{comp.subtype.replace('_', ' ')}"
    perf = next((i for i in live if i.relation == "SELF" and (i.subtype in PERF_SUBTYPES or i.performance_claim
                                                              or i.diligence_claim)), None)
    if perf:
        return "RUN_DILIGENCE", "a performance or technical claim can be checked against public evidence"
    if any(i.relation == "SELF" and i.subtype in HIRING_SUBTYPES for i in live):
        return "PREPARE_FOUNDER_QUESTIONS", "a specialised hiring change raises questions only the team can answer"
    if live and all(i.materiality == "WATCH" for i in live):
        return "MONITOR", "only events to watch; nothing material yet"
    return "REVIEW", "material events since the last look"


MEANING: dict[str, str] = {
    "FINANCING": "New capital usually changes hiring capacity and the pace of the roadmap.",
    "COMMERCIAL": "A commercial or government award can mean a move from pilots toward programs of record.",
    "PRODUCT": "A launch shifts the question from technical feasibility to adoption.",
    "TALENT": "Specialised hiring often precedes a new product line or program.",
    "TECHNICAL": "Technical output can confirm or challenge what the company says it can do.",
    "RISK": "A risk signal needs a factual account before any conclusion.",
    "REGULATORY": "A regulatory step can unlock or delay commercial timelines.",
    "STRATEGIC": "A change in positioning can mean a change in target market.",
    "COMPETITOR": "A competitor's move can change the comparison buyers and investors make.",
}
PAIR_MEANING: dict[frozenset[str], str] = {
    frozenset({"FINANCING", "TALENT"}): "Financing followed by specialised hiring can mean the technical roadmap is "
                                        "being expanded.",
    frozenset({"PRODUCT", "TALENT"}): "A launch with hiring in the same area can mean the company is scaling what "
                                      "it just shipped.",
    frozenset({"COMMERCIAL", "TALENT"}): "An award alongside hiring can mean the company is staffing a program.",
    frozenset({"PRODUCT", "COMPETITOR"}): "Launches by the company and a competitor in the same window can mean the "
                                          "category is getting crowded.",
}


def what_this_could_mean(items: list[AttentionItem]) -> str:
    kinds = {("COMPETITOR" if i.relation != "SELF" else i.event_type) for i in items
             if i.materiality in ("MATERIAL", "WATCH")}
    for pair, text in PAIR_MEANING.items():
        if pair <= kinds:
            return text
    ordered = [k for k in ("RISK", "FINANCING", "COMMERCIAL", "PRODUCT", "TALENT", "TECHNICAL", "REGULATORY",
                           "STRATEGIC", "COMPETITOR") if k in kinds]
    return " ".join(MEANING[k] for k in ordered[:2])


QUESTIONS: dict[str, str] = {
    "new_round": "What does the new financing change in the hiring and product plan for the next 12 months?",
    "acquisition": "What does the acquisition add (team, technology, customers), and what changes in focus?",
    "strategic_investment": "What does the strategic investor bring beyond capital, and are there commercial terms?",
    "government_contract": "What is the scope of the government award, and is there a path to a larger program?",
    "partnership": "Is the partnership a paid deployment, a pilot or a research collaboration?",
    "customer_announcement": "Is the announced customer a paid deployment or a pilot, and how is success measured?",
    "deployment": "Is the deployment paid, and what does it need to expand?",
    "product_launch": "Who are the first users of the launch, and how will adoption be measured?",
    "major_release": "What changed in the major release, and does it break existing users?",
    "new_feature": "Which customer need drove the new capability?",
    "pricing_change": "What drove the pricing change, and how does it affect margins?",
    "specialized_hiring_cluster": "What is the {role} hiring for: a new product line, a program, or backfill?",
    "new_role_class": "Why is the company starting to hire {role} roles now?",
    "hiring_acceleration": "What is driving the hiring increase, and how is it funded?",
    "role_closures": "Were the closed roles filled, paused or cancelled?",
    "executive_join": "What will the new leader own, and what gap does the role fill?",
    "executive_departure": "Who takes over the departing leader's responsibilities?",
    "benchmark_claim": "Can the performance claim be reproduced independently, and against which baseline?",
    "research_paper": "Does the paper describe what is in the product today, or future work?",
    "open_source_release": "Why open-source this now, and how does it relate to the commercial product?",
    "repository_release": "What changed in the release that matters to users?",
    "repo_traction": "What is driving the repository's growth, and is it converting to users?",
    "security_incident": "What happened, what was affected, and what has changed since?",
    "outage": "What caused the outage, and what was the customer impact?",
    "lawsuit": "What is the claim, and what is the potential exposure?",
    "recall": "What was recalled, why, and what is the remediation plan?",
    "repository_archived": "Why was the repository archived, and is the project discontinued?",
    "regulatory_milestone": "What does the regulatory step unlock, and what remains before approval?",
    "repositioning": "Why did the company change how it describes itself?",
    "market_entry": "What is the plan for the new market, and who leads it?",
    "public_attention": "What drove the public attention, and is it about the product?",
}
COMPETITOR_QUESTION = "How does {subject}'s {what} change {company}'s competitive position?"


def question_for(item: AttentionItem) -> str | None:
    if item.relation != "SELF":
        if item.subtype in COMPETITIVE_SUBTYPES or item.materiality == "MATERIAL":
            return COMPETITOR_QUESTION.format(subject=item.subject_name, what=item.subtype.replace("_", " "),
                                              company=item.company_name)
        return None
    tpl = QUESTIONS.get(item.subtype)
    if tpl is None:
        return None
    role = str(item.meta.get("role_class") or "specialised").replace("_", " ")
    return tpl.format(role=role)


QUESTION_SOURCE = {"hiring": "job postings", "web": "company newsroom", "commercial": "USAspending.gov",
                   "github": "GitHub", "research": "arXiv", "community": "Hacker News", "social": "X"}


@dataclass
class Card:
    company_id: str
    company_name: str
    company_slug: str
    priority: str
    score: float
    why_surfaced: str
    what_changed: list[dict[str, Any]]
    material_events: list[str]
    what_this_could_mean: str
    questions: list[dict[str, Any]]
    suggested_action: str
    action_reason: str
    sources: list[dict[str, str]]
    counts: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "what_this_could_mean_epistemic": "INFERENCE"}


def _score(items: list[AttentionItem], day_end: datetime) -> tuple[float, dict[str, int], set[str]]:
    mat_self = sum(1 for i in items if i.materiality == "MATERIAL" and i.relation == "SELF")
    mat_comp = sum(1 for i in items if i.materiality == "MATERIAL" and i.relation != "SELF")
    watch = sum(1 for i in items if i.materiality == "WATCH")
    risk = any(i.event_type == "RISK" and i.relation == "SELF" for i in items)
    fams = {f for i in items for f in i.families}
    newest = max((i.event_at for i in items), default=None)
    recency = 0
    if newest is not None:
        age = (day_end - newest).days
        recency = 2 if age <= 7 else 1 if age <= 14 else 0
    score = 3 * mat_self + 2 * mat_comp + min(watch, WATCH_CAP) + (3 if risk else 0) + max(len(fams) - 1, 0) + recency
    counts = {"material_self": mat_self, "material_competitor": mat_comp, "watch": watch, "risk": int(risk),
              "families": len(fams)}
    return float(score), counts, fams


def priority_of(counts: dict[str, int]) -> str:
    material = counts["material_self"] + counts["material_competitor"]
    if counts["risk"] or (material >= 2 and counts["material_self"] >= 1):
        return "HIGH"
    if material >= 1 or counts["watch"] >= 3:
        return "MEDIUM"
    return "LOW"


def _why(items: list[AttentionItem], counts: dict[str, int]) -> str:
    material = counts["material_self"] + counts["material_competitor"]
    times = [i.event_at for i in items]
    span = max((max(times) - min(times)).days + 1, 1) if times else 0
    parts = []
    if material:
        via = []
        if counts["material_self"]:
            via.append(f"{counts['material_self']} own")
        if counts["material_competitor"]:
            via.append(f"{counts['material_competitor']} via competitors")
        parts.append(f"{material} material event{'s' if material != 1 else ''} ({', '.join(via)})")
    if counts["watch"]:
        parts.append(f"{counts['watch']} to watch")
    fam = counts["families"]
    return (" and ".join(parts) + f" in {span} day{'s' if span != 1 else ''}"
            + (f", from {fam} independent source famil{'ies' if fam != 1 else 'y'}" if fam else ""))


def build_card(items: list[AttentionItem], day_end: datetime) -> Card:
    items = sorted(items, key=lambda i: ({"MATERIAL": 0, "WATCH": 1}.get(i.materiality, 2),
                                         -i.event_at.timestamp(), i.event_id))
    score, counts, _ = _score(items, day_end)
    first = items[0]
    action, reason = card_action(items)
    questions, seen_q = [], set()
    for i in items:
        q = question_for(i)
        if q and q not in seen_q:
            seen_q.add(q)
            fam = i.families[0] if i.families else ""
            questions.append({"text": q, "event_id": i.event_id, "source_url": i.source_urls[0] if i.source_urls
                              else None, "source": QUESTION_SOURCE.get(fam, fam or None),
                              "reason": f"raised by {i.subtype.replace('_', ' ')}: {i.title[:160]}",
                              "epistemic": "INFERENCE"})
        if len(questions) >= 4:
            break
    sources, seen_s = [], set()
    for i in items:
        for u in i.source_urls[:2]:
            if u not in seen_s:
                seen_s.add(u)
                sources.append({"url": u, "title": i.title[:200]})
    return Card(
        company_id=first.company_id, company_name=first.company_name, company_slug=first.company_slug,
        priority=priority_of(counts), score=score, why_surfaced=_why(items, counts),
        what_changed=[{"event_id": i.event_id, "title": i.title, "materiality": i.materiality,
                       "relation": i.relation, "subject": i.subject_name, "event_type": i.event_type,
                       "subtype": i.subtype, "event_at": i.event_at.isoformat()} for i in items[:5]],
        material_events=[i.event_id for i in items if i.materiality == "MATERIAL"],
        what_this_could_mean=what_this_could_mean(items), questions=questions, suggested_action=action,
        action_reason=reason, sources=sources[:8], counts=counts,
    )


def rank(items: list[AttentionItem], day_end: datetime, *, window_days: int = 30, top: int = TOP_N,
         min_priority: str = "MEDIUM") -> list[Card]:
    """Cards for companies at ``min_priority`` or above, best first (priority, score, name)."""
    since = day_end - timedelta(days=window_days)
    by: dict[str, list[AttentionItem]] = {}
    for i in items:
        if i.materiality in ("MATERIAL", "WATCH") and since <= i.event_at <= day_end:
            by.setdefault(i.company_id, []).append(i)
    cards = [build_card(v, day_end) for v in by.values()]
    cards = [c for c in cards if PRIORITY_ORDER[c.priority] <= PRIORITY_ORDER[min_priority]]
    cards.sort(key=lambda c: (PRIORITY_ORDER[c.priority], -c.score, c.company_name.lower(), c.company_id))
    return cards[:top]
