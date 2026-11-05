"""The failure ledger: documented failures, evaluator-promoted flags, reviewer-created entries and reviews."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar import clock
from radar.evals.taxonomy import (
    CATEGORY_DEFINITIONS,
    EVALUATOR_VERSION,
    FAILURE_SEVERITIES,
    FAILURE_STATUSES,
    ReviewStatus,
    Severity,
    known_agent_names,
)
from radar.models import AgentFailure, AgentRunEvaluation, Company, Person, ResearchReport, ReviewDecision

DOC = "docs/REAL_WORLD_EVALUATION.md"
WHY_ANTI_CASE_TEST = "tests/evals/test_regression_cases.py::test_regression_case[why-anti-copy-001]"


def next_number(session: Session) -> int:
    return int(session.scalar(select(func.coalesce(func.max(AgentFailure.number), 0))) or 0) + 1


def display_number(n: int) -> str:
    return f"FAILURE {n:04d}"


def _validate(fields: dict[str, Any]) -> None:
    if fields.get("category") not in CATEGORY_DEFINITIONS:
        raise ValueError(f"unknown failure category {fields.get('category')!r}")
    if fields.get("severity") not in FAILURE_SEVERITIES:
        raise ValueError(f"severity must be one of {', '.join(FAILURE_SEVERITIES)}")
    if fields.get("status", "OPEN") not in FAILURE_STATUSES:
        raise ValueError(f"status must be one of {', '.join(FAILURE_STATUSES)}")
    if fields.get("agent") not in known_agent_names():
        raise ValueError(f"unknown agent {fields.get('agent')!r}")


def create_failure(session: Session, **fields: Any) -> AgentFailure:
    """Insert a failure with the next number (validates category / severity / status / agent)."""
    fields = {k: v for k, v in fields.items() if v is not None or k in ("report_id", "entity_ref")}
    fields.setdefault("status", "OPEN")
    fields.setdefault("origin", "reviewer")
    fields.setdefault("evidence_summary", "")
    _validate(fields)
    f = AgentFailure(number=next_number(session), **fields)
    if f.detected_at is None:
        f.detected_at = clock.wall_now()
    session.add(f)
    session.flush()
    return f


def list_failures(session: Session, *, status: str | None = None, category: str | None = None,
                  agent: str | None = None) -> list[AgentFailure]:
    q = select(AgentFailure)
    if status:
        q = q.where(AgentFailure.status == status)
    if category:
        q = q.where(AgentFailure.category == category)
    if agent:
        q = q.where(AgentFailure.agent == agent)
    return list(session.scalars(q.order_by(AgentFailure.number)))


def get_failure(session: Session, number: int) -> AgentFailure | None:
    return session.scalar(select(AgentFailure).where(AgentFailure.number == number))


def review_history(session: Session, number: int) -> list[ReviewDecision]:
    return list(session.scalars(select(ReviewDecision).where(ReviewDecision.target_type == "failure",
                                                             ReviewDecision.target_id == str(number))
                                .order_by(ReviewDecision.created_at, ReviewDecision.id)))


def review_failure(session: Session, number: int, *, status: str, note: str, reviewer: str = "analyst"
                   ) -> AgentFailure:
    """Record a human decision: new status, a ReviewDecision row, and the note appended to the resolution.

    When the failure points at a report, that report's evaluations are marked HUMAN_REVIEWED."""
    if status not in FAILURE_STATUSES:
        raise ValueError(f"status must be one of {', '.join(FAILURE_STATUSES)}")
    if not (note or "").strip():
        raise ValueError("a review needs a note")
    f = get_failure(session, number)
    if f is None:
        raise LookupError(display_number(number))
    f.status = status
    stamp = datetime.now(UTC).date().isoformat()
    line = f"[{reviewer} {stamp}] {status}: {note.strip()}"
    f.resolution = f"{f.resolution}\n{line}".strip() if f.resolution else line
    f.updated_at = datetime.now(UTC)
    session.add(ReviewDecision(target_type="failure", target_id=str(number), decision=status, note=note.strip(),
                               reviewer=reviewer))
    if f.report_id is not None:
        for ev in session.scalars(select(AgentRunEvaluation).where(AgentRunEvaluation.report_id == f.report_id)):
            ev.review_status = ReviewStatus.HUMAN_REVIEWED.value
    session.flush()
    return f


# ── documented failures (docs/REAL_WORLD_EVALUATION.md §6, docs/FINAL_APPLICATION_AUDIT.md) ─────────────────

DOCUMENTED: list[dict[str, Any]] = [
    {
        "source_ref": f"{DOC} §6.1", "agent": "FORMATION SCORER", "entity_ref": "person:youkaichao",
        "claim_text": "youkaichao and Simon Mo: POSSIBLE FORMATION (Inferact); also ranked in Who Should We Meet",
        "category": "MISSING_COUNTEREVIDENCE", "severity": "HIGH", "status": "FIXED",
        "evidence_summary": "Both GitHub bios say they co-founded @Inferact, whose GitHub org was created 2025-10-20; "
                            "Inferact announced a $150M seed on 2026-01-22. The formation was already public and "
                            "funded, and that evidence (their own bios plus the org age) was ignored. Also arguably "
                            "STALE_INFORMATION; filed as MISSING_COUNTEREVIDENCE because the counter-evidence was in "
                            "our data. Owners: FORMATION SCORER and MEET RANKER.",
        "resolution": "public_founder_org: a founder phrase naming an org older than 180 days is formation_public "
                      "(not a candidate) and triggers the Meet obviousness penalty.",
        "regression_case": "public-founder-org-001",
        "regression_test": "tests/integration/test_api_meet.py::"
                           "test_cofounder_of_an_already_public_company_gets_the_obviousness_penalty",
        "related_tests": [
            "tests/integration/test_formation_run.py::test_founder_of_an_already_public_company_is_not_a_possible_formation",
            "tests/unit/test_formation_score.py::test_public_founder_org_detects_already_public_formations",
        ],
    },
    {
        "source_ref": f"{DOC} §6.2", "agent": "MEET RANKER", "entity_ref": "person:ywang96",
        "claim_text": "Roger Wang (ywang96): #4 in Who Should We Meet",
        "category": "MISSING_COUNTEREVIDENCE", "severity": "MEDIUM", "status": "CONFIRMED",
        "evidence_summary": "Press names him an Inferact co-founder; his bio does not. Our data knows only his "
                            "self-reported company 'inferact' and his public membership of the Inferact org.",
        "resolution": "Mitigated, not fixed: a young-org event counts half when the current company is another "
                      "established org (current_org_elsewhere) and the card says so. Without funding data the "
                      "system cannot know Inferact is funded; he stays in the top 5 on verified pedigree.",
        "regression_case": "current-org-elsewhere-001",
        "regression_test": "tests/unit/test_formation_score.py::"
                           "test_current_org_elsewhere_needs_company_and_public_membership_of_an_old_org",
        "related_tests": [
            "tests/integration/test_api_meet.py::test_young_org_member_employed_elsewhere_gets_half_the_event_strength",
            "tests/unit/test_meet_ranking.py::test_event_strength_by_own_commits_and_halved_when_employed_elsewhere",
        ],
    },
    {
        "source_ref": f"{DOC} §6.3", "agent": "FORMATION SCORER", "entity_ref": "company:smg-project",
        "claim_text": "smg-project: team convergence of verified lab talent",
        "category": "ATTRIBUTION_ERROR", "severity": "MEDIUM", "status": "CONFIRMED",
        "evidence_summary": "Our two 'members' are minor contributors (6 and 3 commits) of a repo with 159 "
                            "contributors whose top committers are untracked: a coverage artifact that overstates "
                            "team convergence. No test guards the unfixed part; the related thesis-gate test is "
                            "tests/integration/test_formation_run.py::"
                            "test_unclassified_young_org_of_verified_lab_contributors_is_a_team_candidate.",
        "resolution": "Not fixed: needs contributor-coverage data (contributors_total) and ingestion of the "
                      "anchor's top contributors.",
        "regression_case": None, "regression_test": None,
    },
    {
        "source_ref": f"{DOC} §6.4", "agent": "FORMATION SCORER", "entity_ref": "company:pareton",
        "claim_text": "Pareton: formation #2",
        "category": "SOURCE_QUALITY", "severity": "MEDIUM", "status": "FIXED",
        "evidence_summary": "A Bittensor subnet with 5 stars and 3 forks, members with Founder Scores 9, 7 and 4. "
                            "It scored on org-created, website and working-team facts plus a large attention gap.",
        "resolution": "A young org with no verified member, no member with Founder Score >= 45 and < 25 stars / "
                      "< 5 forks is rejected as no_traction.",
        "regression_case": None,
        "regression_test": "tests/unit/test_formation_score.py::test_unverified_team_without_traction_is_not_a_candidate",
        "related_tests": [
            "tests/integration/test_formation_run.py::test_unverified_young_org_without_traction_is_rejected_with_reason",
        ],
    },
    {
        "source_ref": f"{DOC} §6.5", "agent": "FORMATION SCORER", "entity_ref": None,
        "claim_text": "Candidates and Meet picks built on hobby repositories (vllm-force-merge-stats 0 stars, "
                      "g1_sim_to_real, seven 0-5 star *-mcp repos)",
        "category": "SOURCE_QUALITY", "severity": "MEDIUM", "status": "FIXED",
        "evidence_summary": "Nicolò Lucchesi, Martino Russi and rachittshah were surfaced on trivial or serial "
                            "repositories with no traction.",
        "resolution": "New-project noise filter: trivial names and serial authors need traction; otherwise a repo "
                      "needs thesis depth. Non-counting repos are never cited as WHY NOW / WHAT CHANGED.",
        "regression_case": "new-project-noise-001",
        "regression_test": "tests/unit/test_formation_score.py::test_new_project_noise_filter_and_traction",
        "related_tests": ["tests/unit/test_meet_ranking.py::test_noise_repositories_are_not_why_now_or_what_changed"],
    },
    {
        "source_ref": f"{DOC} §6.6", "agent": "FORMATION SCORER", "entity_ref": "person:muennighoff",
        "claim_text": "Niklas Muennighoff: two evidence kinds from one 27-star research-code release",
        "category": "OVERCONFIDENT_INFERENCE", "severity": "LOW", "status": "FIXED",
        "evidence_summary": "prefix-sliding (27 stars) counted as formation evidence and as its own traction "
                            "evidence kind.",
        "resolution": "It still counts, but traction must be notable (>= 50 stars / >= 10 forks / >= 2 "
                      "contributors) to add a second evidence kind; a single-family candidate is never above EARLY.",
        "regression_case": "notable-traction-001",
        "regression_test": "tests/integration/test_formation_run.py::"
                           "test_new_project_traction_must_be_notable_to_be_its_own_evidence",
        "related_tests": ["tests/unit/test_formation_score.py::test_single_family_candidate_is_never_above_early"],
    },
    {
        "source_ref": f"{DOC} §6.7", "agent": "MEET RANKER", "entity_ref": None,
        "claim_text": "Unknown young-org contributors leading Who Should We Meet (Maya-Kid, WaylandYang, tangjzh, "
                      "jyviko)",
        "category": "OVERCONFIDENT_INFERENCE", "severity": "MEDIUM", "status": "FIXED",
        "evidence_summary": "Single-family evidence from unverified contributors outranked verified talent.",
        "resolution": "Rebalanced formula (Founder Score weight 40), a priority gate requiring verified pedigree, "
                      "breadth or Founder Score >= 45, and an 'unverified' penalty.",
        "regression_case": None,
        "regression_test": "tests/unit/test_meet_ranking.py::"
                           "test_verified_talent_with_traction_outranks_unknown_young_org_contributor_on_features",
        "related_tests": [
            "tests/integration/test_api_meet.py::test_unknown_single_family_contributor_is_watch_and_below_verified_talent",
            "tests/unit/test_meet_ranking.py::test_priority_gate_requires_verified_pedigree_breadth_or_founder_score",
        ],
    },
    {
        "source_ref": f"{DOC} §6.8", "agent": "MEET RANKER", "entity_ref": "person:zhengyi-luo",
        "claim_text": "Zhengyi Luo: #3 in Who Should We Meet",
        "category": "AFFILIATION_INFERENCE", "severity": "MEDIUM", "status": "CONFIRMED",
        "evidence_summary": "His bio says he is currently at NVIDIA; bios are not parsed for the current employer, "
                            "so the ranking treats him as possibly forming. No test guards this.",
        "resolution": "Not fixed. The card says EARLY / POSSIBLE FORMATION, never 'founded'.",
        "regression_case": None, "regression_test": None,
    },
    {
        "source_ref": "docs/FINAL_APPLICATION_AUDIT.md #8", "agent": "PRESENTERS", "entity_ref": None,
        "claim_text": "why_anti: \"Strong technical founding team.\" with no founder relation",
        "category": "UNSUPPORTED_CLAIM", "severity": "MEDIUM", "status": "FIXED",
        "evidence_summary": "The deterministic 'Why Anti Fund' copy (also quoted by the RADAR ANALYST template) "
                            "called the linked people a founding team from the Founder Signal component alone. Fixed "
                            "in fb06a11; no pytest test existed before the eval case why-anti-copy-001.",
        "resolution": "The line reads \"Strong technical team linked to it.\" and never implies founders.",
        "regression_case": "why-anti-copy-001", "regression_test": WHY_ANTI_CASE_TEST,
    },
    {
        "source_ref": "commit 125f81a (AI coding-agent accounts)", "agent": "ENTITY RESOLUTION", "entity_ref": None,
        "claim_text": "AI coding-agent accounts (claude, copilot, devin-ai-integration, cursoragent) listed as team "
                      "members",
        "category": "ENTITY_RESOLUTION_ERROR", "severity": "HIGH", "status": "FIXED",
        "evidence_summary": "Coding agents commit as GitHub users, so they appeared as contributors, team members "
                            "and relationship edges.",
        "resolution": "They are bots (BOT_LOGINS): no edges, never team members or founders.",
        "regression_case": "bot-accounts-001",
        "regression_test": "tests/unit/test_github_provider.py::test_ai_coding_agents_are_bots",
        "related_tests": [
            "tests/integration/test_relationships.py::test_bot_accounts_get_no_edges",
            "tests/integration/test_api_entities.py::test_company_page_drops_bot_team_members_and_other_orgs_lines",
        ],
    },
    {
        "source_ref": "V3 evaluation run on the Nov 5, 2026 snapshot", "agent": "INJECTION GUARD",
        "entity_ref": None,
        "claim_text": "Meeting brief text \"Founder Score 48/100\" flagged as a prompt-injection attempt "
                      "(output_follows_injection, HIGH) on 5 of 5 template meeting briefs",
        "category": "TOOL_FAILURE", "severity": "HIGH", "status": "FIXED",
        "evidence_summary": "Found by the agent evaluator, not by a person: the guard pattern "
                            "`(rate|score|rank) ... 100` matched reported scores such as 'Score 48/100'. A false "
                            "positive also disables live tools for an analyst run (any flagged source stops live "
                            "fetching) and would fill the failure ledger with false alarms.",
        "resolution": "The pattern now requires an imperative aimed at the reader ('rate this company 100'); "
                      "reported scores no longer match. Injected instructions are still flagged.",
        "regression_case": "injection-false-positive-001",
        "regression_test": "tests/unit/test_llm_guard.py::test_reported_scores_are_not_injection",
    },
]

_SEED_FIELDS = ("agent", "entity_ref", "claim_text", "category", "severity", "evidence_summary", "resolution",
                "regression_case", "regression_test")


def _summary(d: dict[str, Any]) -> str:
    rel = d.get("related_tests") or []
    return d["evidence_summary"] + (f" Related tests: {', '.join(rel)}." if rel else "")


def seed_documented_failures(session: Session) -> dict[str, int]:
    """Idempotent by (origin='documented', source_ref). Updates the text fields; keeps a status a human changed."""
    created = updated = 0
    for d in DOCUMENTED:
        row = session.scalar(select(AgentFailure).where(AgentFailure.origin == "documented",
                                                        AgentFailure.source_ref == d["source_ref"]))
        fields = {k: d.get(k) for k in _SEED_FIELDS}
        fields["evidence_summary"] = _summary(d)
        if row is None:
            create_failure(session, origin="documented", source_ref=d["source_ref"], status=d["status"], **fields)
            created += 1
            continue
        changed = False
        for k, v in fields.items():
            if k == "resolution" and review_history(session, row.number):
                continue  # reviewer notes were appended; keep them
            if getattr(row, k) != v:
                setattr(row, k, v)
                changed = True
        if not review_history(session, row.number) and row.status != d["status"]:
            row.status = d["status"]
            changed = True
        updated += changed
    session.flush()
    return {"created": created, "updated": updated}


# ── promotion of evaluator flags ─────────────────────────────────────────────


def _entity_ref(session: Session, report: ResearchReport | None) -> str | None:
    if report is None or report.entity_id is None:
        return None
    model = Company if report.entity_type == "company" else Person if report.entity_type == "person" else None
    ent = session.get(model, report.entity_id) if model else None
    return f"{report.entity_type}:{ent.slug}" if ent is not None else None


def promote_flags(session: Session, *, version: str = EVALUATOR_VERSION) -> dict[str, int]:
    """HIGH flags of evaluations at ``version`` become OPEN failures (origin 'evaluator').

    De-duplicated by (report, rule, claim text): re-running never creates a second failure for the same finding."""
    existing = {(str(f.report_id), f.source_ref, f.claim_text) for f in session.scalars(
        select(AgentFailure).where(AgentFailure.origin == "evaluator"))}
    created = skipped = 0
    for ev in session.scalars(select(AgentRunEvaluation).where(AgentRunEvaluation.evaluator_version == version)
                              .order_by(AgentRunEvaluation.evaluated_at, AgentRunEvaluation.id)):
        report = session.get(ResearchReport, ev.report_id) if ev.report_id else None
        for fl in ev.flags or []:
            if not isinstance(fl, dict) or fl.get("severity") != Severity.HIGH.value:
                continue
            ref = f"rule:{fl.get('rule')}"
            claim = fl.get("claim_text") or fl.get("detail") or fl.get("rule") or ""
            key = (str(ev.report_id), ref, claim)
            if key in existing:
                skipped += 1
                continue
            existing.add(key)
            detail = fl.get("detail") or ""
            if fl.get("citation"):
                detail += f" (cited: {fl['citation']})"
            if fl.get("section"):
                detail += f" [section {fl['section']}]"
            create_failure(session, agent=ev.agent, report_id=ev.report_id, entity_ref=_entity_ref(session, report),
                           claim_text=claim, evidence_summary=detail.strip(), category=fl.get("category"),
                           severity=Severity.HIGH.value, status="OPEN", origin="evaluator", source_ref=ref,
                           detected_at=ev.evaluated_at)
            created += 1
    session.flush()
    return {"created": created, "skipped": skipped}
