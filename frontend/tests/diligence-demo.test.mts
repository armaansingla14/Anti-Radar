/**
 * /demo/diligence plan: picks follow their rules over whatever data is loaded (no hard-coded names or
 * numbers), nine scenes in order, captions built from the payload, and every scene degrades when its
 * entity is missing.
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  buildDiligencePlan,
  DILIGENCE_SCENE_COUNT,
  pickClaim,
  pickEvidenceCard,
  pickFailure,
  pickQuestion,
  pickRadarSpot,
  pickRiskCard,
  pickWorkspace,
  type DiligenceInputs,
} from "../components/demo/diligencePlan.ts";
import type { DiligenceClaim, DiligenceIndex, DiligenceWorkspace, UnderwritingCategory, WorkspaceSummary } from "../lib/types-diligence.ts";
import type { AgentsOverview, CaseOut, FailureOut } from "../lib/types-agents.ts";
import type { ChangesReport } from "../lib/types-changes.ts";

const company = (slug: string, is_demo = false) => ({ id: `id-${slug}`, slug, name: slug.toUpperCase(), website: null, domain: null, github_org: slug, technical_domain: "Training", thesis_tags: [], is_demo });

const summary = (slug: string, finished: string | null, extra: Partial<WorkspaceSummary> = {}): WorkspaceSummary => ({
  company: company(slug),
  status: "COMPLETE",
  run: finished ? ({ id: `run-${slug}`, finished_at: finished } as WorkspaceSummary["run"]) : null,
  finished_at: finished,
  ...extra,
});

const claim = (ordinal: number, claim_type: string, status: string, testability: string, extra: Partial<DiligenceClaim> = {}): DiligenceClaim => ({
  ordinal,
  text: `claim ${ordinal}`,
  claim_type,
  source_id: "S1",
  source_url: "https://example.com",
  source_quote: `claim ${ordinal}`,
  observed_at: null,
  testability,
  status,
  status_label: status,
  status_reason: "R6: only the company's own statement",
  rule: "R6",
  confidence: "LOW",
  extracted_by: "rules",
  company_evidence: [],
  independent_evidence: [],
  contradictory_evidence: [],
  test_plan: { baseline: { name: "Baseline" }, metrics: ["m1", "m2"], procedure: ["a", "b", "c"], execution: { status: "not_executed" } },
  ...extra,
});

const uw = (key: string, scale: string, rating: string): UnderwritingCategory => ({ key, title: key.toLowerCase(), scale, rating, headline: `${key} headline`, known: [], inferred: [], unknown: [], evidence: [], rule: "" });

const failure = (number: number, agent: string, origin: string, extra: Partial<FailureOut> = {}): FailureOut => ({
  number,
  display: `FAILURE ${String(number).padStart(4, "0")}`,
  agent,
  report_id: null,
  entity_ref: null,
  claim_text: `failure ${number}`,
  evidence_summary: "",
  category: "TOOL_FAILURE",
  severity: "HIGH",
  resolution: "",
  regression_case: `case-${number}`,
  regression_test: null,
  status: "FIXED",
  origin,
  source_ref: null,
  detected_at: null,
  created_at: null,
  updated_at: null,
  reviews: [],
  ...extra,
});

function workspace(slug: string): DiligenceWorkspace {
  return {
    company: company(slug),
    status: { status: "NEEDS_REVIEW", run_id: "r", finished_at: "2026-11-05T00:00:00Z", counts: { VERIFIED: 1, UNVERIFIED: 2 }, pending_review: [{ target_type: "underwriting", target_id: "x", label: "Open-source health: one maintainer" }], started_at: null, last_error: null },
    header: { date: "2026-11-05", status: "NEEDS_REVIEW", evidence_coverage: 0.5, sources_analyzed: 40, last_updated: null, technical_domain: "Training" },
    executive_summary: { text: "", counts: { VERIFIED: 1, UNVERIFIED: 2 }, material_claims: 3, evidence_reviewed: 40, strongest_evidence: "", largest_uncertainty: "", critical_question: "Q2?" },
    claims: [claim(1, "TECHNICAL_CAPABILITY", "VERIFIED", "HIGH"), claim(2, "PERFORMANCE", "UNVERIFIED", "MEDIUM"), claim(3, "PERFORMANCE", "UNVERIFIED", "HIGH")],
    underwriting: [uw("TECHNICAL_DIFFERENTIATION", "evidence", "MEDIUM"), uw("SECURITY", "risk", "MODERATE"), uw("OPEN_SOURCE_HEALTH", "risk", "HIGH")],
    contradictions: [],
    risks: [],
    unknowns: [],
    questions: [
      { question: "Q1?", reason: "r1", claim_ordinals: [3], evidence: [], priority: 90 },
      { question: "Q2?", reason: "licence is not uniform", claim_ordinals: [1], evidence: [], priority: 70 },
    ],
    sources: [],
    sections: {},
    report_id: "rep",
    report_reviews: [],
    run: { id: "r" } as DiligenceWorkspace["run"],
    definitions: { status_labels: { CONTRADICTED: "EVIDENCE CONFLICT" } },
    method_version: "m",
  };
}

const changes = (slug: string, list: "weakened" | "accelerated", delta: number): ChangesReport =>
  ({
    base_day: "2026-10-20",
    is_snapshot: true,
    score_changes: {
      comparable: true,
      accelerated: { count: list === "accelerated" ? 1 : 0, items: list === "accelerated" ? [{ entity_type: "company", id: "c", slug, name: slug, base_score: 10, current_score: 10 + delta, delta, components_delta: {} }] : [] },
      weakened: { count: list === "weakened" ? 1 : 0, items: list === "weakened" ? [{ entity_type: "company", id: "c", slug, name: slug, base_score: 29, current_score: 29 + delta, delta, components_delta: {} }] : [] },
    },
  }) as unknown as ChangesReport;

const agents = { agents: [{ key: "diligence", name: "DILIGENCE ANALYST", runs: 7, metrics: { citation_validity: { value: 1, n: 7, reason: null }, unsupported_claim_rate: { value: 0, n: 7, reason: null } } }], regression: { passing: 16, cases_total: 16 } } as unknown as AgentsOverview;
const kase = (id: string, passed = true): CaseOut => ({ id, title: `title ${id}`, agent: "x", task: "t", failure_category: "TOOL_FAILURE", regression_for: null, min_citations: null, expected_epistemic: {}, expected: {}, latest: { passed, provider: "template", model: "template", run_at: null, failures: [], error: null, metrics: {} } });

function inputs(over: Partial<DiligenceInputs> = {}): DiligenceInputs {
  return {
    index: { workspaces: [summary("acme", "2026-11-05T10:00:00Z")], candidates: [] } as DiligenceIndex,
    workspace: workspace("acme"),
    company: { name: "Acme", score: 13, since_snapshot: { base_day: "2026-10-20", current_day: "2026-11-05", base_score: 29, current_score: 13, delta: -16, comparable: true, reason: null } },
    changes: changes("acme", "weakened", -16),
    agents,
    failures: [failure(3, "INJECTION GUARD", "evaluator"), failure(5, "DILIGENCE ANALYST", "documented")],
    cases: [kase("case-3")],
    ...over,
  };
}

describe("diligence tour picks", () => {
  it("picks the most recently finished real workspace", () => {
    const idx = { workspaces: [summary("old", "2026-10-01T00:00:00Z"), summary("new", "2026-11-05T00:00:00Z"), summary("none", null)], candidates: [] } as DiligenceIndex;
    assert.equal(pickWorkspace(idx)?.company.slug, "new");
    const demoNewer = { workspaces: [summary("real", "2026-10-01T00:00:00Z"), { ...summary("fake", "2026-11-05T00:00:00Z"), company: company("fake", true) }], candidates: [] } as DiligenceIndex;
    assert.equal(pickWorkspace(demoNewer)?.company.slug, "real");
    assert.equal(pickWorkspace({ workspaces: [summary("none", null)], candidates: [] }), null);
    assert.equal(pickWorkspace(null), null);
  });

  it("picks the most testable unverified performance claim, then the first HIGH-testability claim", () => {
    const ws = workspace("acme");
    assert.equal(pickClaim(ws.claims)?.ordinal, 3); // PERFORMANCE + UNVERIFIED + HIGH beats MEDIUM
    const noPerf = [claim(1, "SECURITY", "SUPPORTED", "LOW"), claim(2, "OPEN_SOURCE", "VERIFIED", "HIGH")];
    assert.equal(pickClaim(noPerf)?.ordinal, 2);
    assert.equal(pickClaim([claim(4, "SECURITY", "SUPPORTED", "LOW")])?.ordinal, 4);
    assert.equal(pickClaim([]), null);
  });

  it("picks a risk card (HIGH first) and an evidence card — two scales", () => {
    const ws = workspace("acme");
    assert.equal(pickRiskCard(ws.underwriting)?.key, "OPEN_SOURCE_HEALTH");
    assert.equal(pickEvidenceCard(ws.underwriting)?.key, "TECHNICAL_DIFFERENTIATION");
    assert.equal(pickRiskCard([uw("A", "evidence", "STRONG")]), null);
  });

  it("picks the critical question, else the highest priority", () => {
    const ws = workspace("acme");
    assert.equal(pickQuestion(ws.questions, "Q2?"), 1);
    assert.equal(pickQuestion(ws.questions, "not there"), 0);
    assert.equal(pickQuestion([], "Q"), null);
  });

  it("picks an evaluator-caught injection-guard failure, with fallbacks", () => {
    assert.equal(pickFailure([failure(1, "INJECTION GUARD", "documented"), failure(2, "INJECTION GUARD", "evaluator"), failure(4, "MEET RANKER", "evaluator")])?.number, 2);
    assert.equal(pickFailure([failure(4, "MEET RANKER", "evaluator"), failure(1, "INJECTION GUARD", "documented")])?.number, 4);
    assert.equal(pickFailure([failure(1, "INJECTION GUARD", "documented"), failure(7, "MEET RANKER", "documented")])?.number, 1);
    assert.equal(pickFailure([failure(7, "MEET RANKER", "documented"), failure(9, "X", "documented", { status: "OPEN" })])?.number, 7);
    assert.equal(pickFailure([failure(9, "X", "documented", { regression_case: null })]), null);
    assert.equal(pickFailure([]), null);
  });

  it("finds the company's row in Radar's since-snapshot lists", () => {
    assert.equal(pickRadarSpot("acme", changes("acme", "weakened", -16))?.list, "weakened");
    assert.equal(pickRadarSpot("acme", changes("acme", "accelerated", 9))?.list, "accelerated");
    assert.equal(pickRadarSpot("other", changes("acme", "weakened", -16)), null);
    assert.equal(pickRadarSpot("acme", null), null);
  });
});

describe("diligence tour plan", () => {
  it("has nine scenes in order after the title, ending on the close card", () => {
    const p = buildDiligencePlan(inputs());
    assert.equal(DILIGENCE_SCENE_COUNT, 9);
    assert.equal(p.steps[0].scene_key, "title");
    const scenes = p.steps.slice(1).map((s) => s.scene);
    assert.deepEqual([...new Set(scenes)], [1, 2, 3, 4, 5, 6, 7, 8, 9]);
    assert.deepEqual(scenes, [...scenes].sort((a, b) => a - b));
    assert.equal(p.steps.at(-1)!.scene_key, "final");
  });

  it("builds captions from the payload", () => {
    const p = buildDiligencePlan(inputs());
    const by = (k: string) => p.steps.filter((s) => s.scene_key === k);
    assert.match(by("radar")[0].caption, /Acme — Anti Score 13, −16 since Oct 20: weakened/);
    assert.equal(by("radar")[0].url, "/changes");
    assert.match(by("workspace")[0].caption, /NEEDS REVIEW — 1 pending review: Open-source health: one maintainer · coverage 50% · 40 sources analyzed/);
    assert.match(by("workspace")[0].click ?? "", /open-diligence/);
    assert.match(by("ledger")[0].caption, /^3 material claims: 1 verified · 0 supported · 0 partially supported · 0 evidence conflict · 2 unverified · 0 untestable$/);
    assert.equal(by("claim")[0].url, "/diligence/acme/claims/3");
    assert.match(by("claim")[0].caption, /R6: only the company's own statement → UNVERIFIED/);
    assert.match(by("test-plan")[0].caption, /^Generated, not run — baseline Baseline, 2 metrics, 3 steps/);
    assert.deepEqual(by("underwriting").map((s) => s.focus), ["#uw-OPEN_SOURCE_HEALTH", "#uw-TECHNICAL_DIFFERENTIATION"]);
    assert.match(by("underwriting")[0].caption, /Risk: HIGH/);
    assert.match(by("underwriting")[1].caption, /Evidence: MEDIUM/);
    assert.equal(by("questions")[0].focus, "#question-2");
    assert.match(by("questions")[0].caption, /why: licence is not uniform/);
    const ag = by("agents");
    assert.deepEqual(ag.map((s) => s.url), ["/agents", "/agents/failures/0003", "/agents/cases#case-case-3"]);
    assert.match(ag[0].caption, /DILIGENCE ANALYST: 7 runs, citation validity 100%, unsupported claims 0% · regression 16\/16 passing/);
    assert.match(ag[1].caption, /found by the automatic evaluator/);
    assert.match(ag[2].caption, /latest run: PASS/);
    assert.deepEqual(p.counts, { claims: 3, questions: 2, failures: 2, pinned: 2 });
  });

  it("falls back to the company page when the company is not in a since-snapshot list", () => {
    const p = buildDiligencePlan(inputs({ changes: null }));
    const radar = p.steps.find((s) => s.scene_key === "radar")!;
    assert.equal(radar.url, "/companies/acme");
    assert.match(radar.caption, /Anti Score 13, −16 since Oct 20/);
    assert.equal(p.steps.find((s) => s.scene_key === "workspace")!.click, null);
  });

  it("degrades every scene when there is no workspace, failure or case", () => {
    const p = buildDiligencePlan({ index: { workspaces: [], candidates: [] }, workspace: null, company: null, changes: null, agents: null, failures: [], cases: [] });
    assert.deepEqual([...new Set(p.steps.map((s) => s.scene))], [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
    for (const s of p.steps.slice(1, -1)) assert.ok(s.url, `scene ${s.scene} has a page`);
    assert.equal(p.picks.workspace, null);
    assert.equal(p.picks.failure, null);
  });

  it("drops the regression-case beat when the failure's case is unknown", () => {
    const p = buildDiligencePlan(inputs({ cases: [] }));
    assert.equal(p.steps.filter((s) => s.scene_key === "agents").length, 2);
  });
});
