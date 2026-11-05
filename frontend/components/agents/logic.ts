/**
 * AGENT EVALUATION control plane — pure display logic (no React, relative imports only; tested by
 * tests/agents.test.mts under Node type stripping).
 *
 * Honesty rules encoded here:
 * - every number comes from a `{value, n, reason}` metric; null renders "—" with its reason as a footnote;
 * - nothing is estimated, rounded up or defaulted to zero;
 * - pytest node ids link to the test file on GitHub (the part before `::`).
 */
import type { AgentSummary, CaseOut, FailureOut, MetricValue } from "../../lib/types-agents.ts";

export const REPO_URL = "https://github.com/armaansingla14/Anti-Radar";

export type MetricKind = "rate" | "number" | "ms" | "usd" | "count";

export interface MetricColumn {
  key: string;
  label: string;
  /** Short header for the table. */
  short: string;
  kind: MetricKind;
}

export const METRIC_COLUMNS: MetricColumn[] = [
  { key: "success_rate", label: "Success rate", short: "Success", kind: "rate" },
  { key: "structured_validity_rate", label: "Structured-output validity", short: "Schema", kind: "rate" },
  { key: "citation_validity", label: "Citation validity", short: "Citations", kind: "rate" },
  { key: "unsupported_claim_rate", label: "Unsupported claim rate", short: "Unsupported", kind: "rate" },
  { key: "source_coverage", label: "Source coverage", short: "Coverage", kind: "rate" },
  { key: "avg_sources_used", label: "Avg sources used", short: "Sources", kind: "number" },
  { key: "latency_p50_ms", label: "Latency p50", short: "p50", kind: "ms" },
  { key: "latency_p95_ms", label: "Latency p95", short: "p95", kind: "ms" },
  { key: "avg_cost_usd", label: "Avg cost", short: "Cost", kind: "usd" },
  { key: "fallback_rate", label: "Fallback rate", short: "Fallback", kind: "rate" },
  { key: "error_rate", label: "Error rate", short: "Errors", kind: "rate" },
];

export interface FormattedMetric {
  text: string;
  missing: boolean;
  reason: string | null;
  /** Tooltip: the value's sample size, or why it is missing. */
  title: string;
}

function fmtValue(v: number, kind: MetricKind): string {
  switch (kind) {
    case "rate": {
      const p = v * 100;
      return `${p === Math.round(p) ? p.toFixed(0) : p.toFixed(1)}%`;
    }
    case "ms":
      return v >= 1000 ? `${(v / 1000).toFixed(v >= 10_000 ? 0 : 1)} s` : `${Math.round(v)} ms`;
    case "usd":
      return v === 0 ? "$0" : v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`;
    case "count":
      return String(Math.round(v));
    default:
      return Number.isInteger(v) ? String(v) : v.toFixed(1);
  }
}

/** `{value, n, reason}` → display. Null (or an absent metric) is "—" with the reason; never 0. */
export function formatMetric(m: MetricValue | null | undefined, kind: MetricKind): FormattedMetric {
  if (!m || m.value === null || m.value === undefined || !Number.isFinite(m.value)) {
    const reason = m?.reason?.trim() || "not reported by the evaluator";
    return { text: "—", missing: true, reason, title: `Not computed: ${reason}` };
  }
  return { text: fmtValue(m.value, kind), missing: false, reason: null, title: `n = ${m.n}` };
}

/** Distinct reasons for null metrics across agents, numbered in first-seen order (footnotes). */
export function metricFootnotes(agents: Pick<AgentSummary, "metrics">[], columns: MetricColumn[] = METRIC_COLUMNS): Map<string, number> {
  const notes = new Map<string, number>();
  for (const a of agents) {
    for (const c of columns) {
      const f = formatMetric(a.metrics?.[c.key], c.kind);
      if (f.missing && f.reason && !notes.has(f.reason)) notes.set(f.reason, notes.size + 1);
    }
  }
  return notes;
}

// ── Failures ─────────────────────────────────────────────────────────────────

/** 12 → "FAILURE 0012" (the API's `display` wins when present). */
export function failureDisplay(f: Pick<FailureOut, "number"> & { display?: string | null }): string {
  return f.display?.trim() || `FAILURE ${String(f.number).padStart(4, "0")}`;
}

/** Route param "0012" / "12" → 12; anything else → null. */
export function parseFailureNumber(v: string | null | undefined): number | null {
  if (!v || !/^\d{1,8}$/.test(v)) return null;
  const n = Number(v);
  return n >= 1 ? n : null;
}

export function failureHref(n: number): string {
  return `/agents/failures/${String(n).padStart(4, "0")}`;
}

/** pytest node id → the test file on GitHub (backend/<path before ::>); null when it is not a test path. */
export function testFileUrl(nodeId: string | null | undefined): string | null {
  const file = (nodeId ?? "").split("::")[0].trim();
  if (!file || !/^[\w./-]+\.py$/.test(file) || file.includes("..")) return null;
  return `${REPO_URL}/blob/main/backend/${file.replace(/^backend\//, "")}`;
}

/** A source_ref that names a repo document ("docs/X.md §6.1") or a commit → its GitHub URL. */
export function sourceRefUrl(ref: string | null | undefined): string | null {
  const s = (ref ?? "").trim();
  const doc = /^(docs\/[\w./-]+\.md)/.exec(s);
  if (doc && !doc[1].includes("..")) return `${REPO_URL}/blob/main/${doc[1]}`;
  const commit = /^commit\s+([0-9a-f]{7,40})\b/i.exec(s);
  if (commit) return `${REPO_URL}/commit/${commit[1]}`;
  return null;
}

/** Related tests from the API (`related_tests`), without the regression test itself. */
export function relatedTests(f: Pick<FailureOut, "regression_test"> & { related_tests?: string[] | null }): string[] {
  return [...new Set((f.related_tests ?? []).filter((t) => t && t !== f.regression_test))];
}

/**
 * Evidence summary for display. The backend still appends "Related tests: …" to the summary; when the
 * same tests are listed from `related_tests`, that trailing sentence is dropped so they are not shown twice.
 */
export function evidenceText(summary: string | null | undefined, related?: string[] | null): string {
  const s = (summary ?? "").trim();
  if (!related?.length) return s;
  return s.replace(/\s*Related tests?:[^]*$/, "").trim();
}

export interface FailureFilters {
  status: string | null;
  category: string | null;
  agent: string | null;
}

const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v)?.trim() || null;

export function parseFailureFilters(sp: Record<string, string | string[] | undefined>): FailureFilters {
  const clean = (v: string | null) => (v && /^[A-Za-z_ -]{1,60}$/.test(v) ? v : null);
  return { status: clean(one(sp.status))?.toUpperCase() ?? null, category: clean(one(sp.category))?.toUpperCase() ?? null, agent: clean(one(sp.agent)) };
}

/** Link for a filter strip: set (or clear with null) one filter, keep the others. */
export function filterHref(current: FailureFilters, key: keyof FailureFilters, value: string | null): string {
  const next = { ...current, [key]: value };
  const sp = new URLSearchParams();
  for (const k of ["status", "category", "agent"] as const) if (next[k]) sp.set(k, next[k]!);
  const qs = sp.toString();
  return qs ? `/agents/failures?${qs}` : "/agents/failures";
}

export function originLabel(origin: string | null | undefined): string {
  switch (origin) {
    case "documented":
      return "Documented (hand audit)";
    case "evaluator":
      return "Automatic evaluator";
    case "reviewer":
      return "Human reviewer";
    default:
      return origin ?? "—";
  }
}

// ── Regression cases ─────────────────────────────────────────────────────────

export type CaseResult = "PASS" | "FAIL" | "NOT RUN";

export function caseResult(c: Pick<CaseOut, "latest">): CaseResult {
  if (!c.latest) return "NOT RUN";
  return c.latest.passed && !c.latest.error ? "PASS" : "FAIL";
}

/** "public-founder-org-001" → the case row anchor on /agents/cases. */
export function caseHref(id: string): string {
  return `/agents/cases#case-${id}`;
}
