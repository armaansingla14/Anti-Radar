/**
 * PORTFOLIO PULSE — response types for /api/portfolio*.
 * Mirrors `backend/radar/schemas/portfolio.py`. Public mode only: no type here carries a private investment
 * field (ownership, cost basis, marks …); `PositionOut` is the backend's allow-list and unknown public facts
 * stay null (rendered as UNKNOWN).
 */
import type { ISODateTime } from "./types.ts";

export type Materiality = "MATERIAL" | "WATCH" | "NOISE";
export type PortfolioAction = "REVIEW" | "MONITOR" | "UPDATE_COMPETITIVE_MAP" | "RUN_DILIGENCE" | "PREPARE_FOUNDER_QUESTIONS" | "NO_ACTION";
export type PortfolioDecision = "MARK_REVIEWED" | "NEEDS_FOLLOW_UP" | "MONITOR" | "NO_ACTION";
export type QuestionStatus = "OPEN" | "ANSWERED" | "DISMISSED";
export type Priority = "HIGH" | "MEDIUM" | "LOW";
export type EventType = "PRODUCT" | "TECHNICAL" | "TALENT" | "COMMERCIAL" | "FINANCING" | "COMPETITIVE" | "REGULATORY" | "RISK" | "STRATEGIC";
export type Relation = "SELF" | "COMPETITOR" | "ADJACENT" | "INFRASTRUCTURE" | "INCUMBENT" | "OPEN_SOURCE";
export type CompType = "DIRECT" | "ADJACENT" | "INFRASTRUCTURE" | "INCUMBENT" | "OPEN_SOURCE";

export interface PCompany {
  id: string;
  slug: string;
  name: string;
  website: string | null;
  domain: string | null;
  github_org: string | null;
  is_demo: boolean;
}

/** Public investment context only. */
export interface Position {
  portfolio_name: string;
  status: string;
  publicly_disclosed: boolean;
  source_url: string | null;
  source_checked_at: string | null;
  investment_stage: string | null;
  investment_year: number | null;
  sector: string | null;
  thesis_tags: string[];
}

export interface EventSource {
  url: string | null;
  title: string | null;
  source_type: string | null;
  family: string | null;
  publisher: string | null;
  observed_at: ISODateTime | null;
}

export interface PEvent {
  id: string;
  company: PCompany;
  event_type: string;
  event_subtype: string;
  title: string;
  description: string | null;
  event_at: ISODateTime;
  detected_at: ISODateTime | null;
  source_urls: string[];
  sources: EventSource[];
  confidence: number;
  epistemic: string;
  materiality: Materiality;
  materiality_rule: string;
  materiality_reason: string;
  novelty: string | null;
  impact_areas: string[];
  why_it_matters: string;
  why_it_matters_epistemic: "INFERENCE";
  suggested_review: PortfolioAction;
  status: string;
  reviewed_at: ISODateTime | null;
  reviewed_by: string | null;
  raw_count: number;
  families: string[];
  /** From the requesting company's point of view (only on company-filtered lists). */
  relation: string | null;
  performance_claim: boolean;
  is_demo: boolean;
}

export interface Impact {
  company: PCompany;
  relation: string;
  impact_reason: string;
  confidence: number;
  materiality_for_company: Materiality;
}

export interface PQuestion {
  id: string;
  company: PCompany | null;
  question: string;
  reason: string;
  source: string | null;
  status: QuestionStatus;
  created_from_event_id: string | null;
  created_at: ISODateTime | null;
}

export interface PDecision {
  id: string;
  kind: string;
  actor: string;
  note: string;
  event_id: string | null;
  question_id: string | null;
  created_at: ISODateTime | null;
}

export interface WhatChangedItem {
  event_id: string;
  title: string;
  materiality: Materiality;
  relation: string;
  subject: string;
  event_type: string;
  subtype: string;
  event_at: ISODateTime;
}

export interface CardQuestion {
  text: string;
  event_id: string | null;
  source_url: string | null;
  source: string | null;
  reason: string | null;
  epistemic?: string;
}

export interface AttentionCard {
  company: PCompany;
  priority: Priority;
  score: number;
  why_surfaced: string;
  what_changed: WhatChangedItem[];
  material_events: string[];
  what_this_could_mean: string;
  what_this_could_mean_epistemic: "INFERENCE";
  questions: CardQuestion[];
  suggested_action: PortfolioAction;
  action_reason: string;
  sources: { url: string | null; title: string | null }[];
  counts: Record<string, number>;
}

export interface Funnel {
  raw_changes: number;
  candidates: number;
  after_dedup: number;
  material: number;
  watch: number;
  noise: number;
  companies_surfaced: number;
  day: string | null;
  window: { start: string; end: string } | null;
  available: boolean;
  reason: string | null;
}

export interface MetricWindow {
  metric: string;
  current: number | null;
  prior: number | null;
  window: string;
  comparable: boolean;
  reason: string | null;
  delta: number | null;
  proxy_label: string | null;
  source_url: string | null;
  confidence: number | null;
}

export interface Mover {
  company: PCompany;
  metric: MetricWindow;
}

export interface ProxyValue {
  metric: string;
  value: number | null;
  day: string | null;
  source_url: string | null;
  confidence: number | null;
  proxy_label: string | null;
}

export interface ProviderHealth {
  key: string;
  label: string;
  state: string;
  reason: string | null;
  last_success_at: ISODateTime | null;
}

export interface Monitoring {
  tracked: number;
  portfolio_companies: number;
  comps: number;
  fresh: number;
  stale: number;
  never_observed: number;
  stale_after_days: number;
  providers: ProviderHealth[];
  events_updated_at: ISODateTime | null;
  data_as_of: string | null;
}

export interface SinceReviewRow {
  company: PCompany;
  since: ISODateTime | null;
  reviewed: boolean;
  material: number;
  watch: number;
}

export interface Pulse {
  day: string | null;
  counts: Record<string, number>;
  funnel: Funnel;
  needs_attention: AttentionCard[];
  material_events: PEvent[];
  movers: Mover[];
  since_last_review: SinceReviewRow[];
  monitoring: Monitoring;
  brief_id: string | null;
}

export interface Comp {
  company: PCompany;
  comp_type: string;
  why_comparable: string;
  source_url: string;
  confidence: string;
  epistemic: string;
  recent_events: PEvent[];
  technical_momentum: number | null;
  anti_score: number | null;
}

export interface ReviewInfo {
  id: string;
  decision: string;
  note: string;
  reviewer: string;
  created_at: ISODateTime | null;
}

export interface PortfolioDiligence {
  status: string;
  run_id: string | null;
  finished_at?: ISODateTime | null;
  counts?: Record<string, number>;
  pending_review?: { target_type: string; target_id: string; label: string }[];
  /** Event detail only: the handoff target. */
  company_slug?: string;
  workspace?: string | null;
  run_endpoint?: string | null;
  claim?: unknown;
}

export interface PortfolioDataAge {
  updated_at: ISODateTime | null;
  last_observed_at: ISODateTime | null;
  age_days: number | null;
  families: Record<string, ISODateTime | null>;
}

export interface PortfolioCompany {
  company: PCompany;
  position: Position;
  events: PEvent[];
  competitor_events: PEvent[];
  noise_count: number;
  metrics: MetricWindow[];
  proxies: ProxyValue[];
  comps: Comp[];
  questions: PQuestion[];
  last_review: ReviewInfo | null;
  next_review: string | null;
  decision_trace: PDecision[];
  data_age: PortfolioDataAge | null;
  report_id: string | null;
  diligence: PortfolioDiligence;
  needs_attention: AttentionCard | null;
}

export interface PortfolioIndexItem {
  company: PCompany;
  position: Position;
  material: number;
  watch: number;
  noise: number;
  open_questions: number;
  priority: Priority | null;
}

export interface EventList {
  total: number;
  offset: number;
  limit: number;
  items: PEvent[];
}

export interface EventDetail {
  event: PEvent;
  impacts: Impact[];
  questions: PQuestion[];
  decision_trace: PDecision[];
  suggested_action: PortfolioAction;
  diligence: PortfolioDiligence | null;
  related_comps: Record<string, unknown>[];
}

export interface CompsMap {
  company: PCompany;
  direct: Comp[];
  emerging: Comp[];
  adjacent: Comp[];
}

/** A claim line in a PORTFOLIO ANALYST report body. */
export interface ReportClaim {
  text: string;
  epistemic: string;
  source_ids: string[];
  event_id?: string | null;
  materiality?: Materiality | null;
  relation?: string | null;
  company?: string | null;
  company_slug?: string | null;
}

export interface ReportQuestion {
  question: string;
  event_id?: string | null;
  source_ids?: string[];
  source?: string | null;
  reason?: string | null;
  question_id?: string | null;
}

export interface BriefAttention {
  company: { id?: string; name: string; slug: string };
  priority: Priority;
  score: number;
  why_surfaced: ReportClaim;
  what_changed: ReportClaim[];
  what_this_could_mean: ReportClaim;
  suggested_action: PortfolioAction;
  action_label: string;
  action_reason: string;
  questions: ReportQuestion[];
}

export interface PortfolioBriefBody {
  agent: "PORTFOLIO ANALYST";
  kind: "portfolio_brief";
  date: string;
  period: { start: string; end: string; days: number };
  five_things: ReportClaim[];
  needs_attention: BriefAttention[];
  technical_changes: ReportClaim[];
  competitive_moves: ReportClaim[];
  talent_changes: ReportClaim[];
  noise_filtered: Partial<Funnel> & { text?: string };
  limitations: string;
}

export interface PortfolioReviewBody {
  agent: "PORTFOLIO ANALYST";
  kind: "portfolio_review";
  date: string;
  company: { id?: string; name: string; slug: string; website?: string | null };
  period: { start: string; end: string; days: number };
  summary: ReportClaim;
  what_changed: ReportClaim[];
  material_events: Record<string, ReportClaim[]>;
  open_questions: ReportQuestion[];
  suggested_review: { action: PortfolioAction; label: string; reason: ReportClaim };
  noise_filtered: Record<string, number>;
  limitations: string;
}

export interface BriefSource {
  id: string;
  url: string | null;
  title: string | null;
  snippet?: string | null;
  fetched_at?: ISODateTime | null;
  source_type?: string | null;
}

export interface PortfolioBrief {
  id: string;
  date: string;
  title: string;
  body: PortfolioBriefBody;
  markdown: string;
  sources: BriefSource[];
  generator: string;
  created_at: ISODateTime | null;
}

export interface BriefArchiveItem {
  id: string;
  date: string;
  title: string;
}

export interface PortfolioReviewRequest {
  decision: PortfolioDecision;
  note?: string;
  reviewer?: string;
  event_id?: string | null;
}

export interface QuestionStatusRequest {
  status: QuestionStatus;
  note?: string;
  reviewer?: string;
}
