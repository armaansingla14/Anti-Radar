/**
 * TECHNICAL DILIGENCE — response types for /api/diligence*, /api/reviews.
 * Mirrors `backend/radar/schemas/diligence.py` and `backend/radar/diligence/report.py` (DiligenceReport).
 * Nested report sections are loosely typed (the backend keeps them as dicts); every field the UI reads is
 * optional so an older run renders what it has and shows the rest as unknown.
 *
 * Statuses are evidence assessments, never investment recommendations. CONTRADICTED is displayed as
 * "EVIDENCE CONFLICT" — a conflict to resolve with the company, not a finding of deception.
 */
import type { ISODateTime } from "./types.ts";

export type DiligenceState = "NOT_RUN" | "IN_PROGRESS" | "COMPLETE" | "NEEDS_REVIEW";
export type ClaimStatus = "VERIFIED" | "SUPPORTED" | "PARTIALLY_SUPPORTED" | "CONTRADICTED" | "UNVERIFIED" | "UNTESTABLE";
export type Testability = "HIGH" | "MEDIUM" | "LOW" | "NOT_TESTABLE";
export type ReviewTargetType = "claim" | "underwriting" | "report" | "failure";
export type ReviewDecision = "ACCEPT" | "QUESTION" | "REJECT" | "NEEDS_VERIFICATION";
/** company: the company's own words; code: its public code/releases; registry: PyPI/npm/OSV; third_party: independent. */
export type Publisher = "company" | "code" | "registry" | "third_party";

export interface DiligenceCompany {
  id: string;
  slug: string;
  name: string;
  website: string | null;
  domain: string | null;
  github_org: string | null;
  technical_domain: string | null;
  thesis_tags: string[];
  is_demo: boolean;
}

export interface PendingReview {
  target_type: string;
  target_id: string;
  label: string;
}

export interface DiligenceStatus {
  status: DiligenceState;
  run_id: string | null;
  finished_at: ISODateTime | null;
  counts: Partial<Record<ClaimStatus, number>>;
  pending_review: PendingReview[];
  started_at: ISODateTime | null;
  last_error: string | null;
}

export interface TraceSummary {
  requests: number;
  errors: number;
  refused: number;
  partial: boolean;
  timing_ms: number | null;
}

export interface DiligenceRunSummary {
  id: string;
  status: string;
  /** live | replay */
  mode: string;
  method_version: string;
  started_at: ISODateTime | null;
  finished_at: ISODateTime | null;
  collected_at: ISODateTime | null;
  sources_analyzed: number;
  /** 0..1 share of material claims with non-company evidence; null when not computed. */
  evidence_coverage: number | null;
  summary: {
    claims?: number;
    by_type?: Record<string, number>;
    by_status?: Partial<Record<ClaimStatus, number>>;
    by_testability?: Partial<Record<Testability, number>>;
    conflicts?: number;
    questions?: number;
    underwriting?: Record<string, string>;
    partial?: boolean;
  };
  report_id: string | null;
  error: string | null;
  trace_summary: TraceSummary | null;
}

export interface WorkspaceSummary {
  company: DiligenceCompany;
  status: DiligenceState;
  run: DiligenceRunSummary | null;
  finished_at: ISODateTime | null;
  pending_review?: PendingReview[];
  last_error?: string | null;
}

export interface DiligenceCandidate {
  company: DiligenceCompany;
  score: number | null;
}

export interface DiligenceIndex {
  workspaces: WorkspaceSummary[];
  candidates: DiligenceCandidate[];
}

export interface Review {
  id: string;
  target_type: string;
  target_id: string;
  decision: ReviewDecision | string;
  note: string;
  reviewer: string;
  created_at: ISODateTime | null;
}

export interface ReviewRequest {
  target_type: ReviewTargetType;
  target_id: string;
  decision: ReviewDecision;
  note?: string;
  reviewer?: string;
}

/** One piece of evidence for or against a claim (backend EvidenceRef). */
export interface EvidenceRef {
  artifact_key: string;
  source_id: string | null;
  url: string;
  title: string;
  publisher: Publisher | string;
  publisher_label: string;
  kind: string;
  /** supports | contradicts | context | … */
  relation: string;
  /** direct | indirect */
  strength: string;
  verifies: boolean;
  quote: string;
  observed_at: ISODateTime | null;
  matcher: string;
  note: string;
  /** True for an explicit "no evidence for this aspect" entry. */
  gap?: boolean;
}

export interface TestPlan {
  claim_id?: number;
  hypothesis?: string;
  facets?: string[];
  models?: string[];
  metrics?: string[];
  workloads?: string[];
  hardware?: string[];
  targets?: string[];
  baseline?: { name?: string | null; version_claimed?: string | null; version_guidance?: string | null } | null;
  candidate?: { description?: string | null } | null;
  procedure?: string[];
  pass_criteria?: string[];
  sandbox?: string[];
  execution?: { status?: string; reason?: string } | null;
}

export interface DiligenceClaim {
  id?: string;
  ordinal: number;
  text: string;
  claim_type: string;
  source_id: string | null;
  source_url: string | null;
  source_quote: string;
  observed_at: ISODateTime | null;
  epistemic?: string;
  testability: Testability | string;
  status: ClaimStatus | string;
  status_label: string;
  status_reason: string;
  rule: string;
  confidence: string;
  extracted_by: string;
  company_evidence: EvidenceRef[];
  independent_evidence: EvidenceRef[];
  contradictory_evidence: EvidenceRef[];
  test_plan: TestPlan | null;
  reviews?: Review[];
  run_id?: string | null;
  sources?: DiligenceSource[] | null;
}

export interface UnderwritingEvidence {
  url?: string;
  title?: string;
  quote?: string;
  publisher?: string;
  source_id?: string | null;
  artifact_key?: string;
}

export interface UnderwritingCategory {
  key: string;
  title: string;
  /** evidence: STRONG / MEDIUM / WEAK / UNKNOWN. risk: LOW / MODERATE / HIGH / UNKNOWN. */
  scale: "evidence" | "risk" | string;
  rating: string;
  headline: string;
  known: string[];
  inferred: string[];
  unknown: string[];
  evidence: UnderwritingEvidence[];
  rule: string;
  target_id?: string;
  reviews?: Review[];
}

export interface Conflict {
  id: string;
  kind: string;
  /** EVIDENCE CONFLICT | CLAIM REQUIRES CLARIFICATION */
  label: string;
  claim_ordinals: number[];
  summary: string;
  evidence: EvidenceRef[];
  details?: Record<string, unknown>;
}

export interface DiligenceRisk {
  title: string;
  severity: string;
  category: string;
  detail: string;
  url: string | null;
}

export interface FounderQuestion {
  question: string;
  reason: string;
  claim_ordinals: number[];
  evidence: UnderwritingEvidence[];
  priority?: number;
}

export interface DiligenceSource {
  id: string;
  url: string;
  title: string;
  source_type: string;
  publisher?: string;
  fetched_at: ISODateTime | null;
  artifact_key?: string;
}

export interface ExecutiveSummary {
  text: string;
  counts: Partial<Record<ClaimStatus, number>>;
  material_claims: number;
  evidence_reviewed: number;
  strongest_evidence: string;
  largest_uncertainty: string;
  critical_question: string | null;
}

export interface OssMetric {
  value: string | number | boolean | null;
  note?: string | null;
  source_url?: string | null;
}

export interface OssRisk {
  id: string;
  title: string;
  detail?: string;
  severity: string;
  url?: string | null;
}

export interface OpenSourceHealth {
  available?: boolean;
  repo?: string;
  repo_url?: string;
  maturity?: string;
  metrics?: Record<string, OssMetric>;
  risks?: OssRisk[];
  unknown?: string[];
}

export interface SecurityFinding {
  id: string;
  kind: string;
  title: string;
  severity: string;
  why_it_matters: string;
  material_question?: boolean;
  evidence?: { url?: string; path?: string; line?: number | null; quote?: string; source_id?: string | null };
}

export interface SecuritySection {
  counts?: Record<string, number>;
  findings?: SecurityFinding[];
  reviewed?: boolean;
  material_questions?: number;
  posture?: { scope?: string; security_md?: boolean; files_scanned?: string[]; published_advisories?: number };
}

export interface DependencyRisk {
  id: string;
  severity: string;
  dependency: string;
  ecosystem: string;
  title: string;
  detail: string;
  url?: string;
}

export interface DependencySection {
  parsed?: boolean;
  counts?: { total?: number; runtime?: number; dev?: number; pinned?: number; by_ecosystem?: Record<string, number> };
  risks?: DependencyRisk[];
  vulnerabilities?: { id: string; name?: string; version?: string; summary?: string; severity_level?: string; url?: string }[];
  osv_checked?: number;
  manifests?: { url?: string; kind?: string; path?: string }[];
  not_checked_unpinned?: string[];
  external_services?: { vendor: string; category?: string; runtime?: boolean }[];
  llm_providers?: unknown[];
}

export interface InfraSection {
  available?: boolean;
  known?: string[];
  inferred?: string[];
  unknown?: string[];
  clouds?: string[];
  databases?: string[];
  observability?: string[];
  ci_workflows?: string[];
}

export interface AiSystemItem {
  state: "known" | "inferred" | "unknown" | string;
  aspect: string;
  detail: string;
  evidence?: { url?: string; path?: string; item?: string; vendor?: string }[];
}

export interface AiSystemsSection {
  items?: AiSystemItem[];
  runtimes?: string[];
  llm_providers?: string[];
  unknown?: string[];
}

export interface BenchmarksSection {
  rating?: string;
  notes?: string[];
  baselines?: string[];
  performance_claims?: { ordinal: number; text: string; status: string; status_label: string; has_test_plan: boolean }[];
}

export interface WorkspaceSections {
  technical_differentiation?: UnderwritingCategory | null;
  architecture?: (UnderwritingCategory & { languages?: Record<string, Record<string, number>> }) | null;
  open_source_health?: OpenSourceHealth | null;
  security?: SecuritySection | null;
  dependencies?: DependencySection | null;
  infrastructure?: InfraSection | null;
  ai_systems?: AiSystemsSection | null;
  benchmarks?: BenchmarksSection | null;
}

export interface DiligenceDefinitions {
  claim_types?: Record<string, string>;
  statuses?: Record<string, string>;
  status_labels?: Record<string, string>;
  testability?: Record<string, string>;
  publishers?: Record<string, string>;
  publisher_labels?: Record<string, string>;
  /** Status rules R1–R6. */
  rules?: Record<string, string>;
  [key: string]: Record<string, string> | undefined;
}

export interface DiligenceWorkspace {
  company: DiligenceCompany;
  status: DiligenceStatus;
  header: {
    date: string | null;
    status: DiligenceState;
    evidence_coverage: number | null;
    sources_analyzed: number;
    last_updated: ISODateTime | null;
    technical_domain: string | null;
  };
  executive_summary: ExecutiveSummary | null;
  claims: DiligenceClaim[];
  underwriting: UnderwritingCategory[];
  contradictions: Conflict[];
  risks: DiligenceRisk[];
  unknowns: string[];
  questions: FounderQuestion[];
  sources: DiligenceSource[];
  sections: WorkspaceSections;
  report_id: string | null;
  report_reviews: Review[];
  run: DiligenceRunSummary | null;
  definitions: DiligenceDefinitions;
  method_version: string;
}

/** Stored report body of kind `diligence` (backend DiligenceReport). */
export interface DiligenceReportBody {
  company: { id: string | null; slug: string; name: string; website: string | null; github: string | null };
  date: string;
  as_of: string;
  method_version: string;
  status: string;
  disclaimer: string;
  executive_summary: ExecutiveSummary;
  claims: DiligenceClaim[];
  underwriting: UnderwritingCategory[];
  contradictory_evidence: Conflict[];
  key_risks: DiligenceRisk[];
  unknowns: string[];
  questions_for_founders: FounderQuestion[];
  sources: DiligenceSource[];
}
