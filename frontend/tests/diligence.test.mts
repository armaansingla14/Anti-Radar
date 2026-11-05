/** Technical diligence display logic: neutral status labels, named rating scales, evidence by publisher, honest reviews, tabs. */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  canWrite,
  claimHref,
  claimLabel,
  conflictLabel,
  countsLine,
  executionText,
  groupEvidence,
  isFullPlan,
  latestReview,
  ossValue,
  parseOrdinal,
  parseTab,
  pct,
  ratingText,
  ratingTone,
  reviewSummary,
  rulesFor,
  showsTestPlan,
  splitReason,
  stateLabel,
  stateReason,
  statusCounts,
  statusLabel,
  statusTone,
  tabHref,
  workspaceMap,
} from "../components/diligence/logic.ts";
import type { EvidenceRef } from "../lib/types-diligence.ts";

const ev = (publisher: string, extra: Partial<EvidenceRef> = {}): EvidenceRef => ({
  artifact_key: `${publisher}:x`, source_id: "S1", url: "https://example.com", title: publisher, publisher, publisher_label: publisher.toUpperCase(),
  kind: "tree", relation: "supports", strength: "direct", verifies: false, quote: "q", observed_at: null, matcher: "m", note: "n", ...extra,
});

describe("claim status labels", () => {
  it("shows CONTRADICTED as EVIDENCE CONFLICT, even if the API label says otherwise", () => {
    assert.equal(statusLabel("CONTRADICTED"), "EVIDENCE CONFLICT");
    assert.equal(statusLabel("CONTRADICTED", { CONTRADICTED: "CONTRADICTED" }), "EVIDENCE CONFLICT");
    assert.equal(statusLabel("PARTIALLY_SUPPORTED"), "PARTIALLY SUPPORTED");
    assert.equal(statusLabel("verified"), "VERIFIED");
    assert.equal(statusTone("CONTRADICTED"), "conflict");
    assert.equal(statusTone("SOMETHING_NEW"), "unverified");
  });

  it("never lets an API label call a claim deception", () => {
    assert.equal(statusLabel("UNVERIFIED", { UNVERIFIED: "DECEPTIVE" }), "UNVERIFIED");
  });

  it("keeps zero counts and the conflict count visible", () => {
    const rows = statusCounts({ VERIFIED: 2, SUPPORTED: 5, UNVERIFIED: 5 });
    assert.deepEqual(rows.map((r) => r.count), [2, 5, 0, 0, 5, 0]);
    assert.equal(rows[3].label, "EVIDENCE CONFLICT");
    assert.equal(countsLine({ VERIFIED: 2, SUPPORTED: 5, UNVERIFIED: 5, CONTRADICTED: 0 }), "Claims 12 · Verified 2 · Supported 5 · Unverified 5 · Conflicts 0");
  });

  it("labels workspace states and gives the pending-review reason", () => {
    assert.equal(stateLabel("NEEDS_REVIEW"), "NEEDS REVIEW");
    assert.equal(stateLabel("NOT_RUN"), "NOT RUN");
    assert.equal(
      stateReason({ status: "NEEDS_REVIEW", pending_review: [{ target_type: "underwriting", target_id: "x", label: "Open-source health: bus factor 1" }], last_error: null }),
      "Pending human review: Open-source health: bus factor 1",
    );
    assert.equal(stateReason({ status: "COMPLETE", pending_review: [], last_error: null }), null);
  });

  it("formats claim labels, coverage and ordinals", () => {
    assert.equal(claimLabel(1), "CLAIM 01");
    assert.equal(claimLabel(12), "CLAIM 12");
    assert.equal(claimHref("unsloth-ai", 3), "/diligence/unsloth-ai/claims/3");
    assert.equal(pct(0.583), "58%");
    assert.equal(pct(null), "—");
    assert.equal(parseOrdinal("07"), 7);
    assert.equal(parseOrdinal("0"), null);
    assert.equal(parseOrdinal("x"), null);
  });
});

describe("status rules", () => {
  it("splits the rule id from the reason and takes rule text from the payload", () => {
    assert.deepEqual(splitReason("R6: only the company's own statement"), { id: "R6", text: "only the company's own statement" });
    assert.deepEqual(splitReason("no prefix", "R3"), { id: "R3", text: "no prefix" });
    assert.deepEqual(rulesFor({}), {}); // no copied rule text: the payload is the source
    assert.equal(rulesFor({ rules: { R1: "api text" } }).R1, "api text");
  });
});

describe("evidence grouping", () => {
  it("splits independent evidence into public artifacts (code, registry) and third parties", () => {
    const g = groupEvidence({
      claim_type: "PERFORMANCE",
      company_evidence: [ev("company")],
      independent_evidence: [ev("code"), ev("registry"), ev("third_party")],
      contradictory_evidence: [],
    });
    assert.equal(g.company.length, 1);
    assert.deepEqual(g.artifact.map((e) => e.publisher), ["code", "registry"]);
    assert.deepEqual(g.thirdParty.map((e) => e.publisher), ["third_party"]);
    assert.deepEqual(g.gaps, []);
  });

  it("states gaps instead of hiding empty groups", () => {
    const g = groupEvidence({ claim_type: "PERFORMANCE", company_evidence: [ev("company")], independent_evidence: [ev("code", { relation: "context" })], contradictory_evidence: [] });
    assert.ok(g.gaps.includes("No independent third-party source found."));
    assert.ok(g.gaps.some((s) => /context only/.test(s)));
    const bare = groupEvidence({ claim_type: "SECURITY", company_evidence: [ev("company")], independent_evidence: [ev("code", { gap: true, note: "No public code evidence for sandboxing." })], contradictory_evidence: [] });
    assert.ok(bare.gaps.includes("No public code evidence for sandboxing."));
    assert.ok(bare.gaps.includes("No public code or registry evidence for this security claim."));
    assert.equal(bare.artifact.length, 0);
  });

  it("only uses the two neutral conflict labels", () => {
    assert.equal(conflictLabel({ label: "CLAIM REQUIRES CLARIFICATION", kind: "x" }), "CLAIM REQUIRES CLARIFICATION");
    assert.equal(conflictLabel({ label: "DECEPTION", kind: "contradiction" }), "EVIDENCE CONFLICT");
  });
});

describe("test plans", () => {
  it("renders plans for HIGH/MEDIUM testable claims only, never claims they were run", () => {
    assert.equal(showsTestPlan({ testability: "HIGH", test_plan: { procedure: ["x"] } }), true);
    assert.equal(showsTestPlan({ testability: "LOW", test_plan: { procedure: ["x"] } }), false);
    assert.equal(showsTestPlan({ testability: "MEDIUM", test_plan: null }), false);
    assert.equal(isFullPlan({}), false);
    assert.equal(isFullPlan({ procedure: ["x"] }), true);
    assert.match(executionText({ execution: { status: "not_executed" } }), /^Not executed — /);
    assert.match(executionText(null), /never run on the radar host/);
  });
});

describe("underwriting scales", () => {
  it("names the scale with the rating", () => {
    assert.equal(ratingText({ scale: "risk", rating: "HIGH" }), "Risk: HIGH");
    assert.equal(ratingText({ scale: "evidence", rating: "MEDIUM" }), "Evidence: MEDIUM");
  });
  it("is direction-aware: high risk and weak evidence are both bad", () => {
    assert.equal(ratingTone({ scale: "risk", rating: "HIGH" }), "bad");
    assert.equal(ratingTone({ scale: "risk", rating: "LOW" }), "good");
    assert.equal(ratingTone({ scale: "evidence", rating: "WEAK" }), "bad");
    assert.equal(ratingTone({ scale: "evidence", rating: "STRONG" }), "good");
    assert.equal(ratingTone({ scale: "evidence", rating: "UNKNOWN" }), "unknown");
  });
});

describe("human review", () => {
  it("says 'Awaiting human review' when there is none", () => {
    const s = reviewSummary([]);
    assert.equal(s.reviewed, false);
    assert.equal(s.label, "Awaiting human review");
    assert.equal(reviewSummary(undefined).label, "Awaiting human review");
  });
  it("uses the latest decision", () => {
    const reviews = [
      { decision: "QUESTION", note: "first", reviewer: "a", created_at: "2026-11-01T00:00:00Z" },
      { decision: "NEEDS_VERIFICATION", note: " later ", reviewer: "b", created_at: "2026-11-04T00:00:00Z" },
      { decision: "ACCEPT", note: "", reviewer: "c", created_at: "2026-11-02T00:00:00Z" },
    ];
    assert.equal(latestReview(reviews)?.reviewer, "b");
    const s = reviewSummary(reviews);
    assert.deepEqual([s.label, s.note, s.reviewer, s.count], ["NEEDS VERIFICATION", "later", "b", 3]);
  });
  it("only allows writes on a reachable, writable, non-snapshot backend", () => {
    assert.equal(canWrite({ reachable: true, read_only: false }), true);
    assert.equal(canWrite({ reachable: true, read_only: false, demo_snapshot: true }), false);
    assert.equal(canWrite({ reachable: true, read_only: true }), false);
    assert.equal(canWrite({ reachable: true, read_only: false }, true), false);
    assert.equal(canWrite({ reachable: false, read_only: false }), false);
    assert.equal(canWrite(null), false);
  });
});

describe("index and tabs", () => {
  it("maps workspaces with a run by slug", () => {
    const company = { id: "1", slug: "unsloth-ai", name: "Unsloth AI", website: null, domain: null, github_org: null, technical_domain: null, thesis_tags: [], is_demo: false };
    const m = workspaceMap({ workspaces: [{ company, status: "COMPLETE", run: {} as never, finished_at: null }, { company: { ...company, slug: "x" }, status: "NOT_RUN", run: null, finished_at: null }], candidates: [] });
    assert.deepEqual([...m.keys()], ["unsloth-ai"]);
    assert.equal(workspaceMap(null).size, 0);
  });
  it("parses ?tab= and builds plain links", () => {
    assert.equal(parseTab(undefined), "overview");
    assert.equal(parseTab("diligence"), "diligence");
    assert.equal(parseTab("WHY_NOW"), "why-now");
    assert.equal(parseTab(["reports", "x"]), "reports");
    assert.equal(parseTab("nonsense"), "overview");
    assert.equal(tabHref("unsloth-ai", "overview"), "/companies/unsloth-ai");
    assert.equal(tabHref("unsloth-ai", "diligence"), "/companies/unsloth-ai?tab=diligence");
  });
  it("keeps unknown OSS values visible", () => {
    assert.equal(ossValue("commits_12w", null), "unknown");
    assert.equal(ossValue("top_contributor_share", 0.61), "61%");
    assert.equal(ossValue("stars", 79074), "79,074");
    assert.equal(ossValue("archived", false), "no");
  });
});
