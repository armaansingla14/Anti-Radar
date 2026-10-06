/**
 * TypeScript mirror of backend/radar/schemas/api.py — the API contract.
 *
 * Field names match the Pydantic models exactly. Python `datetime`/`date`
 * arrive as ISO strings. Keep in sync with the backend file; do not add
 * fields here that the backend does not emit.
 */

export type ISODateTime = string;
export type ISODate = string;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type JsonValue = any;
export type JsonObject = Record<string, JsonValue>;

export type Epistemic = "FACT" | "INFERENCE" | "MODEL_OPINION";
export type Verification = "VERIFIED" | "INFERRED" | "UNKNOWN";
export type EntityKind = "company" | "person" | "repository" | "paper";
export type Generator = "llm" | "template" | "cached";
export type Trend = "up2" | "up" | "flat" | "down";

// ── Score ────────────────────────────────────────────────────────────────────

export interface EvidenceItem {
  signal_id: string | null;
  title: string;
  detail: string | null;
  source_type: string | null;
  source_url: string | null;
  event_at: ISODateTime | null;
  weight: number | null;
}

export type ScoreComponentKey =
  | "technical_momentum"
  | "founder_signal"
  | "acceleration"
  | "thesis_fit"
  | "novelty"
  | "attention_gap";

export interface ScoreComponent {
  key: ScoreComponentKey | string;
  label: string;
  value: number;
  max: number;
  explanation: string;
  raw: JsonObject;
  evidence: EvidenceItem[];
}

export interface AttentionGapSource {
  key: string;
  label: string;
  available: boolean;
  value?: number | null;
  percentile?: number | null;
  note?: string | null;
}

export interface AttentionGap {
  technical: number;
  attention: number;
  gap: number;
  flagged: boolean;
  coverage: "good" | "partial" | "low";
  sources: AttentionGapSource[];
}

export interface Collision {
  score: number;
  families: string[];
  window_days: number;
  span_days: number | null;
  signal_count: number;
  evidence: EvidenceItem[];
  summary: string;
}

export interface AntiScore {
  total: number;
  delta_7d: number | null;
  delta_30d: number | null;
  components: ScoreComponent[];
  attention_gap: AttentionGap | null;
  collision: Collision | null;
  version: string;
  computed_on: ISODate;
  feedback_adjustment: number;
  cohort_size: number;
  low_coverage: boolean;
}

export interface ScorePoint {
  day: ISODate;
  score: number;
}

// ── Signals ──────────────────────────────────────────────────────────────────

export interface SignalOut {
  id: string;
  entity_id: string;
  entity_type: string;
  entity_name: string | null;
  entity_slug: string | null;
  signal_type: string;
  source_type: string;
  source_url: string | null;
  title: string;
  description: string | null;
  raw_value: number | null;
  normalized_value: number | null;
  event_at: ISODateTime;
  detected_at: ISODateTime;
  confidence: number;
  epistemic: Epistemic;
  is_demo: boolean;
  metadata: JsonObject;
}

export interface SeriesPoint {
  day: ISODate;
  value: number;
}

export interface TimeSeries {
  metric: string;
  label: string;
  points: SeriesPoint[];
  source: "observed" | "backfill" | "demo" | "mixed";
  note: string | null;
}

// ── Entities ─────────────────────────────────────────────────────────────────

export interface FactField {
  label: string;
  value: JsonValue;
  status: Verification;
  source_url: string | null;
  source_type: string | null;
  note: string | null;
}

export interface TopicTag {
  key: string;
  theme: string;
  label: string;
  score: number;
  method: string;
}

export interface PersonCard {
  id: string;
  slug: string;
  name: string;
  headline: string | null;
  current_company: string | null;
  current_role: string | null;
  prior_affiliations: string[];
  domains: string[];
  github_handle: string | null;
  x_handle: string | null;
  personal_site: string | null;
  avatar_url: string | null;
  founder_score: number | null;
  delta_7d: number | null;
  why_now: string[];
  is_founder: boolean;
  role: string | null;
  is_demo: boolean;
  first_seen_at: ISODateTime | null;
}

export interface RepoCard {
  id: string;
  full_name: string;
  description: string | null;
  language: string | null;
  stars: number;
  forks: number;
  stars_30d: number | null;
  commits_4w: number | null;
  contributors: number | null;
  gh_created_at: ISODateTime | null;
  url: string;
  is_demo: boolean;
}

export interface EntityLink {
  type: string;
  slug?: string | null;
  name: string;
  id?: string;
  score?: number | null;
  /** Emitted on thesis-map top_entities. */
  is_demo?: boolean;
}

export interface PaperCard {
  id: string;
  arxiv_id: string;
  title: string;
  abstract: string | null;
  authors: string[];
  categories: string[];
  published_at: ISODateTime | null;
  url: string | null;
  code_urls: string[];
  linked_entities: EntityLink[];
  why_it_matters: string | null;
  why_it_matters_epistemic?: Epistemic | null;
  why_it_matters_generator?: "llm" | "template" | "cached" | null;
  score: number | null;
  is_demo: boolean;
}

export interface IntelCard {
  rank: number;
  entity_type: EntityKind;
  id: string;
  slug: string;
  name: string;
  kind_label: string;
  one_liner: string | null;
  score: number;
  delta_7d: number | null;
  delta_30d: number | null;
  thesis_tags: TopicTag[];
  why_surfaced: EvidenceItem[];
  why_anti: string[];
  why_anti_epistemic: Epistemic;
  attention_gap: AttentionGap | null;
  collision: Collision | null;
  sparkline: number[];
  sparkline_label: string | null;
  first_seen_at: ISODateTime | null;
  is_new: boolean;
  watched: boolean;
  is_demo: boolean;
  data_note: string | null;
}

export interface CompanySummary {
  id: string;
  slug: string;
  name: string;
  one_liner: string | null;
  kind: string;
  stage: string;
  score: number | null;
  delta_7d: number | null;
  thesis_tags: TopicTag[];
  attention_gap: number | null;
  collision: number | null;
  stars_30d: number | null;
  first_seen_at: ISODateTime | null;
  website: string | null;
  github_org: string | null;
  is_demo: boolean;
  watched: boolean;
}

export interface TimelineEvent {
  day: ISODate;
  title: string;
  detail: string | null;
  source_type: string;
  source_url: string | null;
  signal_type: string;
  epistemic: Epistemic;
  is_demo: boolean;
}

export interface SourceRef {
  id: string;
  url: string | null;
  title: string | null;
  source_type: string | null;
  fetched_at: ISODateTime | null;
}

export interface Narrative {
  text: string;
  epistemic: Epistemic;
  generator: Generator;
  model: string | null;
  citations: string[];
}

export interface ReportRef {
  id: string;
  kind: string;
  title: string;
  created_at: ISODateTime;
}

export interface CompanyDetail {
  summary: CompanySummary;
  description: string | null;
  facts: FactField[];
  anti_score: AntiScore | null;
  score_history: ScorePoint[];
  why_now: Narrative | null;
  why_anti: Narrative | null;
  risks: Narrative[];
  timeline: TimelineEvent[];
  founders: PersonCard[];
  repositories: RepoCard[];
  papers: PaperCard[];
  jobs: JsonObject[];
  hiring: JsonObject;
  series: TimeSeries[];
  sources: SourceRef[];
  reports: ReportRef[];
  match_candidates: JsonObject[];
  watched: boolean;
  data_note: string | null;
}

export interface PersonDetail {
  card: PersonCard;
  bio: string | null;
  facts: FactField[];
  founder_score: AntiScore | null;
  score_history: ScorePoint[];
  why_now: Narrative | null;
  timeline: TimelineEvent[];
  companies: CompanySummary[];
  repositories: RepoCard[];
  papers: PaperCard[];
  collaborators: PersonCard[];
  sources: SourceRef[];
  watched: boolean;
  data_note: string | null;
}

// ── Feeds ────────────────────────────────────────────────────────────────────

export interface StrangeSignal {
  id: string;
  pattern: string;
  headline: string;
  detail: string;
  entities: EntityLink[];
  evidence: EvidenceItem[];
  detected_at: ISODateTime;
  strength: number;
  is_demo: boolean;
}

export interface ThesisShift {
  topic_key: string;
  label: string;
  theme: string;
  direction: "heating" | "cooling";
  detail: string;
  momentum: number;
}

export interface RadarCounts {
  emerging_companies: number;
  founders: number;
  strange_signals: number;
  thesis_shifts: number;
  new_entities_7d: number;
  signals_24h: number;
}

export interface RadarFeed {
  date: ISODate;
  generated_at: ISODateTime;
  counts: RadarCounts;
  cards: IntelCard[];
  founders: IntelCard[];
  strange_signals: StrangeSignal[];
  thesis_shifts: ThesisShift[];
  data_freshness: Record<string, string | null>;
  demo_mode: boolean;
  snapshot_note: string | null;
}

export interface WatchChange {
  entity_type: string;
  entity_id: string;
  slug: string;
  name: string;
  changes: string[];
  score: number | null;
  delta: number | null;
  is_demo: boolean;
}

export interface DailyRadar {
  date: ISODate;
  since: ISODateTime;
  founders: IntelCard[];
  companies: IntelCard[];
  papers: PaperCard[];
  strange_signals: StrangeSignal[];
  watchlist_changes: WatchChange[];
  new_entities: (CompanySummary | PersonCard)[];
  summary: Narrative | null;
}

export interface BriefItem {
  n: number;
  text: string;
  entity_refs: EntityLink[];
  evidence: string[];
}

export interface MorningBrief {
  date: ISODate;
  what_changed: BriefItem[];
  founders_to_know: IntelCard[];
  companies_to_watch: IntelCard[];
  research: PaperCard[];
  strange_signal: StrangeSignal | null;
  narrative: Narrative | null;
  markdown: string;
  generator: Generator;
  generated_at?: ISODateTime | null;
}

export interface ThesisCell {
  key: string;
  label: string;
  theme: string;
  momentum: number;
  momentum_trend: Trend;
  attention: number | null;
  attention_trend: Trend;
  attention_gap: boolean;
  companies: number;
  emerging_companies: number;
  founders: number;
  papers_30d: number;
  hiring_roles_30d: number;
  top_entities: EntityLink[];
}

export interface ThesisTheme {
  key: string;
  label: string;
  weight: number;
  momentum: number;
  cells: ThesisCell[];
}

export interface ThesisMap {
  themes: ThesisTheme[];
  generated_at: ISODateTime;
}

// ── Search ───────────────────────────────────────────────────────────────────

export type SearchSort = "score" | "momentum" | "attention_gap" | "recent" | "delta";

export interface SearchFilters {
  entity_types: EntityKind[];
  themes: string[];
  topics: string[];
  keywords: string[];
  prior_affiliations: string[];
  hiring_role_classes: string[];
  min_score: number | null;
  min_attention_gap: number | null;
  max_attention: number | null;
  accelerating: boolean;
  first_seen_within_days: number | null;
  event_within_days: number | null;
  signal_types: string[];
  stage: string[];
  sort: SearchSort;
}

export interface SearchResult {
  entity_type: EntityKind;
  id: string;
  slug: string;
  name: string;
  subtitle: string | null;
  score: number | null;
  match_reasons: string[];
  is_demo: boolean;
}

export interface SearchResponse {
  query: string;
  filters: SearchFilters;
  interpretation: string[];
  parser: "deterministic" | "llm" | "hybrid";
  results: SearchResult[];
  total: number;
}

// ── Workflow ─────────────────────────────────────────────────────────────────

export interface WatchRequest {
  entity_type: "company" | "person" | "repository" | "topic";
  entity_id?: string | null;
  topic_key?: string | null;
  note?: string | null;
}

export type FeedbackLabel =
  | "useful"
  | "not_interesting"
  | "too_obvious"
  | "too_early"
  | "wrong_thesis"
  | "bad_data"
  | "already_known"
  | "dismiss";

export interface FeedbackRequest {
  entity_type: EntityKind;
  entity_id: string;
  label: FeedbackLabel;
  note?: string | null;
}

export interface InvestigateRequest {
  entity_type: "company" | "person";
  entity_id: string;
  live_fetch?: boolean;
}

export interface MemoRequest {
  entity_type: "company" | "person";
  entity_id: string;
  report_id?: string | null;
}

export interface IngestGithubRequest {
  query?: string | null;
  repo?: string | null;
  org?: string | null;
  limit?: number;
}

export interface IngestUrlRequest {
  url: string;
  company_name?: string | null;
}

export interface IngestXRequest {
  url: string;
  pasted_text?: string | null;
  entity_type?: "company" | "person" | null;
  entity_id?: string | null;
}

export interface ReportOut {
  id: string;
  kind: string;
  title: string;
  entity_type: string | null;
  entity_id: string | null;
  entity_slug: string | null;
  body: JsonObject;
  markdown: string;
  sources: JsonObject[];
  model: string | null;
  generator: string;
  status: string;
  cost_usd: number;
  created_at: ISODateTime;
  is_demo: boolean;
}

export type ProviderState = "healthy" | "degraded" | "error" | "disabled" | "never_run";

export interface ProviderStatus {
  provider: string;
  label: string;
  state: ProviderState;
  reason: string | null;
  last_success_at: ISODateTime | null;
  last_run_at: ISODateTime | null;
  records_collected: number;
  records_rejected: number;
  signals_created: number;
  errors: string[];
  rate_limit_remaining: number | null;
  latency_ms_p50: number | null;
  runs_24h: number;
}

export interface SystemStatus {
  providers: ProviderStatus[];
  llm: JsonObject;
  database: JsonObject;
  demo_mode: boolean;
  score_version: string;
  last_scored_at: ISODateTime | null;
}

export interface RetroEntry {
  name: string;
  slug: string | null;
  why_included: string;
  observable_signals: EvidenceItem[];
  signal_families: string[];
  history_available: boolean;
  history_note: string;
  current_score: number | null;
  framework_reading: string;
  series: TimeSeries[];
}

export interface Retrospective {
  disclaimer: string;
  method: string;
  entries: RetroEntry[];
  takeaways: string[];
}

export interface GraphNode {
  id: string;
  type: string;
  label: string;
  slug: string | null;
  score: number | null;
  is_demo: boolean;
}

export interface GraphEdge {
  source: string;
  target: string;
  kind: string;
}

export interface GraphOut {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

// ── Not modelled in api.py (backend returns ad-hoc JSON) ─────────────────────

/**
 * One server-sent event from POST /api/investigate. The backend emits
 * `data: {json}` frames; `type` discriminates. Unknown keys are preserved.
 */
export interface InvestigateEvent {
  type: string;
  step?: string;
  message?: string;
  status?: "running" | "ok" | "error" | "skipped" | string;
  report_id?: string;
  url?: string;
  [key: string]: JsonValue;
}

export interface WatchItemOut {
  id: string;
  entity_type: "company" | "person" | "repository" | "topic" | string;
  entity_id: string;
  topic_key: string | null;
  name: string;
  slug: string | null;
  note: string | null;
  created_at: ISODateTime;
  last_seen_at: ISODateTime;
  score: number | null;
  delta_7d: number | null;
  is_demo: boolean;
  changes: string[];
}

export interface WatchlistOut {
  name: string;
  items: WatchItemOut[];
  changes_since_last_visit: number;
}

/** Back-compat aliases used by earlier components. */
export type WatchlistEntry = WatchItemOut;
export type WatchlistResponse = WatchlistOut;

export interface OkOut {
  ok: boolean;
  detail: string | null;
}

export interface FeedbackOut {
  ok: boolean;
  weights: Record<string, number>;
  changed: Record<string, number>;
  dismissed: boolean;
}

export interface IngestRunOut {
  provider: string;
  status: string;
  records_fetched: number;
  records_accepted: number;
  records_rejected: number;
  signals_created: number;
  errors: string[];
}

export interface IngestOut {
  run: IngestRunOut;
  entity: { type: string; id: string; slug: string; name: string } | null;
  info: JsonObject;
  recomputed: boolean;
}
