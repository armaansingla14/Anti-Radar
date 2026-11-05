/**
 * AGENT EVALUATION control plane — response types for /api/agents*.
 * Mirrors `backend/radar/schemas/evals.py`. A metric is `{value, n, reason}`: `value` is null (never
 * estimated) whenever it cannot be computed, with the reason; `n` is its denominator / sample size.
 */
import type { ISODateTime } from "./types.ts";

export type FailureStatus = "OPEN" | "CONFIRMED" | "FIXED" | "DISMISSED";

export interface MetricValue {
  value: number | null;
  n: number;
  reason: string | null;
}

export interface AgentSummary {
  key: string;
  name: string;
  kinds: string[];
  purposes: string[];
  description: string;
  runs: number;
  metrics: Record<string, MetricValue>;
  by_generator: Record<string, number>;
  flags_by_category: Record<string, number>;
  open_failures: number;
}

export interface LLMTelemetryRow {
  purpose: string;
  agent: string;
  calls: number;
  live_calls: number;
  ok_rate: MetricValue;
  latency_p50_ms: MetricValue;
  latency_p95_ms: MetricValue;
  cost_usd: number;
  cache_hit_rate: MetricValue;
  models: string[];
}

export interface RegressionSummary {
  cases_total: number;
  passing: number;
  failing: number;
  never_run: number;
  last_run_at: ISODateTime | null;
  error: string | null;
}

export interface ModelRef {
  provider: string;
  model: string;
}

export interface ConfiguredModel extends ModelRef {
  tiers: string[];
}

export interface Recommendation {
  task: string;
  recommendation: ModelRef | null;
  reason: string;
}

export interface TaxonomyEntry {
  category: string;
  definition: string;
}

export interface AgentsOverview {
  agents: AgentSummary[];
  llm_telemetry: LLMTelemetryRow[];
  methodology: Record<string, string>;
  taxonomy: TaxonomyEntry[];
  regression: RegressionSummary;
  model_comparison: { configured_models: ConfiguredModel[]; recommendations: Recommendation[] };
  evaluator_version: string;
}

export interface EvalFlag {
  rule: string;
  category: string;
  severity: string;
  section: string | null;
  claim_text: string | null;
  citation: string | null;
  detail: string;
  needs_human_review: boolean;
}

export interface EvaluationOut {
  id: string;
  report_id: string | null;
  report_title: string | null;
  report_kind: string | null;
  entity_type: string | null;
  entity_id: string | null;
  entity_slug: string | null;
  report_url: string | null;
  agent: string;
  model: string | null;
  generator: string | null;
  evaluator_version: string;
  evaluated_at: ISODateTime;
  task_success: boolean;
  structured_output_valid: boolean;
  claims_total: number;
  fact_claims: number;
  citations_total: number;
  invalid_citations: number;
  unsupported_claims: number;
  unsupported_claim_rate: number | null;
  citation_precision: number | null;
  source_coverage: number | null;
  sources_registered: number;
  sources_used: number;
  latency_ms: number | null;
  cost_usd: number;
  fallback: boolean;
  error: boolean;
  flags: EvalFlag[];
  review_status: string;
  notes: string;
}

export interface FailureReviewOut {
  decision: string;
  note: string;
  reviewer: string;
  created_at: ISODateTime | null;
}

export interface FailureOut {
  number: number;
  /** "FAILURE 0012" */
  display: string;
  agent: string;
  report_id: string | null;
  entity_ref: string | null;
  claim_text: string;
  evidence_summary: string;
  category: string;
  severity: string;
  resolution: string;
  regression_case: string | null;
  /** pytest node id, e.g. tests/unit/test_llm_guard.py::test_reported_scores_are_not_injection */
  regression_test: string | null;
  /** Other pytest node ids that cover this failure (besides regression_test). */
  related_tests?: string[];
  status: FailureStatus | string;
  /** documented | evaluator | reviewer */
  origin: string;
  source_ref: string | null;
  detected_at: ISODateTime | null;
  created_at: ISODateTime | null;
  updated_at: ISODateTime | null;
  reviews: FailureReviewOut[];
}

export interface FailureReviewRequest {
  status: FailureStatus;
  note: string;
}

export interface CaseResultOut {
  passed: boolean;
  provider: string;
  model: string;
  run_at: ISODateTime | null;
  failures: string[];
  error: string | null;
  metrics: Record<string, unknown>;
}

export interface CaseOut {
  id: string;
  title: string;
  agent: string;
  task: string;
  failure_category: string;
  regression_for: string | null;
  min_citations: number | null;
  expected_epistemic: Record<string, string>;
  expected: Record<string, unknown>;
  latest: CaseResultOut | null;
}
