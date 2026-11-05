/**
 * TECHNICAL DILIGENCE — pure display logic (no React, relative imports only; tested by
 * tests/diligence.test.mts under Node type stripping).
 *
 * Honesty rules encoded here:
 * - statuses are evidence assessments, never recommendations; CONTRADICTED reads "EVIDENCE CONFLICT";
 * - an underwriting rating always names its scale (Evidence: MEDIUM is not Risk: MEDIUM);
 * - a missing human review reads "Awaiting human review" — never a fabricated decision;
 * - evidence is grouped by who published it (the company / its public artifacts / third parties);
 * - an empty evidence group is stated as a gap, not hidden.
 */
import type {
  ClaimStatus,
  Conflict,
  DiligenceClaim,
  DiligenceDefinitions,
  DiligenceIndex,
  DiligenceState,
  DiligenceStatus,
  EvidenceRef,
  Review,
  TestPlan,
  UnderwritingCategory,
  WorkspaceSummary,
} from "../../lib/types-diligence.ts";

// ── Claim statuses ───────────────────────────────────────────────────────────

/** Display order: strongest evidence first, conflicts kept visible next to support. */
export const STATUS_ORDER: ClaimStatus[] = ["VERIFIED", "SUPPORTED", "PARTIALLY_SUPPORTED", "CONTRADICTED", "UNVERIFIED", "UNTESTABLE"];

const STATUS_LABELS: Record<ClaimStatus, string> = {
  VERIFIED: "VERIFIED",
  SUPPORTED: "SUPPORTED",
  PARTIALLY_SUPPORTED: "PARTIALLY SUPPORTED",
  CONTRADICTED: "EVIDENCE CONFLICT",
  UNVERIFIED: "UNVERIFIED",
  UNTESTABLE: "UNTESTABLE",
};

/** Status → display label. CONTRADICTED is always "EVIDENCE CONFLICT" (never "contradicted" / "deception"). */
export function statusLabel(status: string | null | undefined, labels?: Record<string, string>): string {
  const key = (status ?? "").toUpperCase();
  if (key === "CONTRADICTED") return STATUS_LABELS.CONTRADICTED;
  const fromApi = labels?.[key];
  if (fromApi && !/decept|lie|fraud/i.test(fromApi)) return fromApi;
  return STATUS_LABELS[key as ClaimStatus] ?? key.replace(/_/g, " ");
}

export type StatusTone = "verified" | "supported" | "partial" | "conflict" | "unverified" | "untestable";

export function statusTone(status: string | null | undefined): StatusTone {
  switch ((status ?? "").toUpperCase()) {
    case "VERIFIED":
      return "verified";
    case "SUPPORTED":
      return "supported";
    case "PARTIALLY_SUPPORTED":
      return "partial";
    case "CONTRADICTED":
      return "conflict";
    case "UNTESTABLE":
      return "untestable";
    default:
      return "unverified";
  }
}

/** Counts in display order, zero rows kept (an explicit 0 conflicts is information). */
export function statusCounts(counts: Partial<Record<string, number>> | null | undefined): { status: ClaimStatus; label: string; count: number }[] {
  return STATUS_ORDER.map((s) => ({ status: s, label: statusLabel(s), count: counts?.[s] ?? 0 }));
}

/** "Claims 12 · Supported 5 · Verified 2 · Unverified 5 · Conflicts 0" (company-page strip). */
export function countsLine(counts: Partial<Record<string, number>> | null | undefined): string {
  const c = (k: ClaimStatus) => counts?.[k] ?? 0;
  const total = STATUS_ORDER.reduce((a, s) => a + c(s), 0);
  const parts = [`Claims ${total}`, `Verified ${c("VERIFIED")}`, `Supported ${c("SUPPORTED")}`];
  if (c("PARTIALLY_SUPPORTED")) parts.push(`Partially supported ${c("PARTIALLY_SUPPORTED")}`);
  parts.push(`Unverified ${c("UNVERIFIED")}`);
  if (c("UNTESTABLE")) parts.push(`Untestable ${c("UNTESTABLE")}`);
  parts.push(`Conflicts ${c("CONTRADICTED")}`);
  return parts.join(" · ");
}

// ── Workspace state ──────────────────────────────────────────────────────────

const STATE_LABELS: Record<DiligenceState, string> = {
  NOT_RUN: "NOT RUN",
  IN_PROGRESS: "IN PROGRESS",
  COMPLETE: "COMPLETE",
  NEEDS_REVIEW: "NEEDS REVIEW",
};

export function stateLabel(state: string | null | undefined): string {
  return STATE_LABELS[(state ?? "NOT_RUN") as DiligenceState] ?? (state ?? "").replace(/_/g, " ");
}

/** Why the workspace is in its state (NEEDS REVIEW: what is pending; errors verbatim). */
export function stateReason(s: Pick<DiligenceStatus, "status" | "pending_review" | "last_error"> | null | undefined): string | null {
  if (!s) return null;
  if (s.status === "NEEDS_REVIEW") {
    const labels = (s.pending_review ?? []).map((p) => p.label).filter(Boolean);
    if (labels.length === 0) return "Items are flagged for human review.";
    return `Pending human review: ${labels.join("; ")}`;
  }
  if (s.status === "IN_PROGRESS") return "Collection and analysis are running.";
  if (s.last_error) return `Last run failed: ${s.last_error}`;
  if (s.status === "NOT_RUN") return "No diligence run for this company yet.";
  return null;
}

/** 0.583 → "58%"; null → "—". */
export function pct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

/** "live" → "Live collection", "replay" → "Replay of a captured collection". */
export function modeLabel(mode: string | null | undefined): string {
  if (mode === "live") return "Live collection";
  if (mode === "replay") return "Replay of a captured collection";
  return mode ? mode : "—";
}

// ── Claims ───────────────────────────────────────────────────────────────────

/** 1 → "CLAIM 01". */
export function claimLabel(ordinal: number): string {
  return `CLAIM ${String(ordinal).padStart(2, "0")}`;
}

export function claimHref(slug: string, ordinal: number): string {
  return `/diligence/${encodeURIComponent(slug)}/claims/${ordinal}`;
}

export function diligenceHref(slug: string): string {
  return `/diligence/${encodeURIComponent(slug)}`;
}

/** Valid claim ordinal from a route param ("01", "7"), else null. */
export function parseOrdinal(v: string | null | undefined): number | null {
  if (!v || !/^\d{1,4}$/.test(v)) return null;
  const n = Number(v);
  return n >= 1 && n <= 1000 ? n : null;
}

/** NOT_TESTABLE → "NOT TESTABLE". */
export function testabilityLabel(t: string | null | undefined): string {
  return (t ?? "UNKNOWN").replace(/_/g, " ");
}

/** Status rules R1–R6 from the payload (`definitions.rules`); empty when an older backend omits them. */
export function rulesFor(defs: DiligenceDefinitions | null | undefined): Record<string, string> {
  return defs?.rules ?? {};
}

/** "R6: only the company's own statement" → { id: "R6", text: "only the company's own statement" }. */
export function splitReason(reason: string | null | undefined, rule?: string | null): { id: string | null; text: string } {
  const r = (reason ?? "").trim();
  const m = /^(R\d+)\s*[:—-]\s*([^]*)$/.exec(r);
  if (m) return { id: m[1], text: m[2] };
  return { id: rule && /^R\d+$/.test(rule) ? rule : null, text: r };
}

export interface EvidenceGroups {
  /** The company's own statements (website, README prose, launch posts). */
  company: EvidenceRef[];
  /** The publisher's public code, releases and registry metadata: verifiable, not independent opinion. */
  artifact: EvidenceRef[];
  /** Independent authors (HN, papers). */
  thirdParty: EvidenceRef[];
  /** Evidence that conflicts with the claim. */
  conflicting: EvidenceRef[];
  /** Explicit "no evidence for this aspect" entries plus derived empty-group gaps. */
  gaps: string[];
}

const isArtifact = (e: EvidenceRef) => e.publisher === "code" || e.publisher === "registry";

/** Group a claim's evidence by publisher and state what is missing. */
export function groupEvidence(claim: Pick<DiligenceClaim, "company_evidence" | "independent_evidence" | "contradictory_evidence" | "claim_type">): EvidenceGroups {
  const all = [...(claim.company_evidence ?? []), ...(claim.independent_evidence ?? [])];
  const real = all.filter((e) => !e.gap);
  const company = real.filter((e) => e.publisher === "company");
  const artifact = real.filter(isArtifact);
  const thirdParty = real.filter((e) => !isArtifact(e) && e.publisher !== "company");
  const conflicting = (claim.contradictory_evidence ?? []).filter((e) => !e.gap);
  const gaps = [...all, ...(claim.contradictory_evidence ?? [])].filter((e) => e.gap).map((e) => e.note || e.title).filter(Boolean);
  const kind = (claim.claim_type ?? "").replace(/_/g, " ").toLowerCase();
  if (artifact.length === 0) gaps.push(`No public code or registry evidence for this ${kind ? `${kind} ` : ""}claim.`);
  if (thirdParty.length === 0) gaps.push("No independent third-party source found.");
  if (artifact.length > 0 && !artifact.some((e) => e.relation === "supports" || e.verifies))
    gaps.push("Public artifacts give context only; none directly supports the figure or property claimed.");
  return { company, artifact, thirdParty, conflicting, gaps };
}

/** "supports · direct" chip text for an evidence item. */
export function evidenceRelation(e: Pick<EvidenceRef, "relation" | "strength" | "verifies">): string {
  const rel = e.verifies ? "verifies" : e.relation || "context";
  return [rel, e.strength].filter(Boolean).join(" · ").toUpperCase();
}

/** Conflict labels: only the two neutral forms are ever shown. */
export function conflictLabel(c: Pick<Conflict, "label" | "kind"> | null | undefined): string {
  const l = `${c?.label ?? ""} ${c?.kind ?? ""}`.toUpperCase();
  return /CLARIF/.test(l) ? "CLAIM REQUIRES CLARIFICATION" : "EVIDENCE CONFLICT";
}

/** A test plan is rendered for HIGH/MEDIUM testability claims that have one. */
export function showsTestPlan(claim: Pick<DiligenceClaim, "testability" | "test_plan">): boolean {
  const t = (claim.testability ?? "").toUpperCase();
  return Boolean(claim.test_plan) && (t === "HIGH" || t === "MEDIUM");
}

/** True when the plan carries content (an empty or partial record renders a link to the claim page instead). */
export function isFullPlan(plan: TestPlan | null | undefined): plan is TestPlan {
  if (!plan) return false;
  return Boolean(plan.procedure?.length || plan.metrics?.length || plan.pass_criteria?.length || plan.hypothesis);
}

export const NOT_EXECUTED = "Not executed — plans are generated, untrusted code is never run on the radar host.";

export function executionText(plan: TestPlan | null | undefined): string {
  const st = plan?.execution?.status;
  if (!st || st === "not_executed") return NOT_EXECUTED;
  return `${st.replace(/_/g, " ")}${plan?.execution?.reason ? ` — ${plan.execution.reason}` : ""}`;
}

// ── Underwriting ─────────────────────────────────────────────────────────────

/** "evidence" → "Evidence"; "risk" → "Risk". The scale is always named next to the rating. */
export function scaleName(scale: string | null | undefined): string {
  return scale === "risk" ? "Risk" : scale === "evidence" ? "Evidence" : scale ? scale[0].toUpperCase() + scale.slice(1) : "Rating";
}

export function scaleLegend(scale: string | null | undefined): string {
  return scale === "risk" ? "Risk scale: LOW · MODERATE · HIGH · UNKNOWN" : "Evidence-strength scale: STRONG · MEDIUM · WEAK · UNKNOWN";
}

/** "Risk: HIGH" / "Evidence: MEDIUM". */
export function ratingText(u: Pick<UnderwritingCategory, "scale" | "rating">): string {
  return `${scaleName(u.scale)}: ${(u.rating ?? "UNKNOWN").toUpperCase()}`;
}

export type RatingTone = "good" | "mid" | "bad" | "unknown";

/** Direction-aware tone: HIGH risk and WEAK evidence are both "bad". */
export function ratingTone(u: Pick<UnderwritingCategory, "scale" | "rating">): RatingTone {
  const r = (u.rating ?? "").toUpperCase();
  if (r === "UNKNOWN" || !r) return "unknown";
  if (u.scale === "risk") return r === "LOW" ? "good" : r === "MODERATE" ? "mid" : "bad";
  return r === "STRONG" ? "good" : r === "MEDIUM" ? "mid" : "bad";
}

// ── Human review ─────────────────────────────────────────────────────────────

export const DECISIONS = ["ACCEPT", "QUESTION", "REJECT", "NEEDS_VERIFICATION"] as const;

export function decisionLabel(d: string | null | undefined): string {
  return (d ?? "").replace(/_/g, " ").toUpperCase();
}

/** Newest review by created_at (stable on ties: later in the list wins). */
export function latestReview<T extends { created_at: string | null }>(reviews: T[] | null | undefined): T | null {
  let best: T | null = null;
  for (const r of reviews ?? []) {
    if (!best || (r.created_at ?? "") >= (best.created_at ?? "")) best = r;
  }
  return best;
}

export interface ReviewSummary {
  reviewed: boolean;
  label: string;
  note: string | null;
  reviewer: string | null;
  at: string | null;
  count: number;
}

/** Human side of AGENT ASSESSMENT vs HUMAN REVIEW. No review → "Awaiting human review" (never invented). */
export function reviewSummary(reviews: Pick<Review, "decision" | "note" | "reviewer" | "created_at">[] | null | undefined): ReviewSummary {
  const r = latestReview(reviews);
  if (!r) return { reviewed: false, label: "Awaiting human review", note: null, reviewer: null, at: null, count: 0 };
  return { reviewed: true, label: decisionLabel(r.decision), note: r.note?.trim() || null, reviewer: r.reviewer || null, at: r.created_at, count: reviews?.length ?? 0 };
}

/** Writes are possible only on a reachable live backend that accepts them (not read-only, not a snapshot). */
export function canWrite(
  session: { reachable?: boolean; read_only: boolean; demo_snapshot?: boolean; static_snapshot?: boolean } | null | undefined,
  staticSnapshot = false,
): boolean {
  if (!session || staticSnapshot) return false;
  return Boolean(session.reachable) && !session.read_only && !session.demo_snapshot && !session.static_snapshot;
}

// ── Index ────────────────────────────────────────────────────────────────────

/** slug → workspace, for "Open diligence" links (one index fetch per page, not one per card). */
export function workspaceMap(index: DiligenceIndex | null | undefined): Map<string, WorkspaceSummary> {
  const m = new Map<string, WorkspaceSummary>();
  for (const w of index?.workspaces ?? []) if (w.run) m.set(w.company.slug, w);
  return m;
}

// ── Company-page tabs ────────────────────────────────────────────────────────

export type CompanyTab = "overview" | "why-now" | "diligence" | "relationships" | "reports";

export const COMPANY_TABS: { key: CompanyTab; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "why-now", label: "Why now" },
  { key: "diligence", label: "Diligence" },
  { key: "relationships", label: "Relationships" },
  { key: "reports", label: "Reports" },
];

export function parseTab(v: string | string[] | undefined | null): CompanyTab {
  const s = (Array.isArray(v) ? v[0] : v)?.toLowerCase().trim().replace(/_/g, "-");
  return COMPANY_TABS.some((t) => t.key === s) ? (s as CompanyTab) : "overview";
}

export function tabHref(slug: string, tab: CompanyTab): string {
  const base = `/companies/${encodeURIComponent(slug)}`;
  return tab === "overview" ? base : `${base}?tab=${tab}`;
}

/** Section ids each company tab shows (OVERVIEW shows everything). */
export const TAB_SECTIONS: Record<Exclude<CompanyTab, "overview" | "diligence">, string[]> = {
  "why-now": ["why-now", "what-changed", "signals", "timeline"],
  relationships: ["team", "team-graph", "repositories", "research"],
  reports: ["reports"],
};

// ── Open-source health ───────────────────────────────────────────────────────

const OSS_ORDER = [
  "stars", "forks", "contributors", "bus_factor", "top_contributor_share", "active_contributors_90d", "commits_12w",
  "prs_merged_30d", "releases_90d", "latest_release", "release_interval_median_days", "issue_time_to_close_median_days",
  "license", "semver_1_0", "archived", "repo_age_days",
];

const OSS_LABELS: Record<string, string> = {
  semver_1_0: "stable release ≥ 1.0",
  commits_12w: "commits 12 weeks",
  repo_age_days: "repo age (days)",
  release_interval_median_days: "median days between releases",
  issue_time_to_close_median_days: "median days to close an issue",
  active_contributors_90d: "active contributors 90d",
};

/** OSS metrics in a reading order; unknown values stay visible as "unknown". */
export function ossRows(metrics: Record<string, { value: unknown; note?: string | null; source_url?: string | null }> | null | undefined) {
  if (!metrics) return [];
  const keys = [...OSS_ORDER.filter((k) => k in metrics), ...Object.keys(metrics).filter((k) => !OSS_ORDER.includes(k))];
  return keys.map((k) => {
    const m = metrics[k];
    return { key: k, label: OSS_LABELS[k] ?? k.replace(/_/g, " "), value: ossValue(k, m?.value), note: m?.note ?? null, url: m?.source_url ?? null, known: m?.value !== null && m?.value !== undefined };
  });
}

export function ossValue(key: string, v: unknown): string {
  if (v === null || v === undefined) return "unknown";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "number") {
    if (/share|growth/.test(key) && v <= 1) return pct(v);
    return Number.isInteger(v) ? v.toLocaleString("en-US") : v.toFixed(1);
  }
  return String(v);
}

export const SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "MODERATE", "LOW", "INFO", "UNKNOWN"];

export function bySeverity<T extends { severity: string }>(items: T[] | null | undefined): T[] {
  const rank = (s: string) => {
    const i = SEVERITY_ORDER.indexOf((s ?? "").toUpperCase());
    return i === -1 ? SEVERITY_ORDER.length : i;
  };
  return [...(items ?? [])].sort((a, b) => rank(a.severity) - rank(b.severity));
}
