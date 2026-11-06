/**
 * PORTFOLIO PULSE — pure display logic (no React, relative imports only; tested by tests/portfolio.test.mts under
 * Node type stripping).
 *
 * Honesty rules encoded here:
 * - materiality is a documented rule outcome (M1–M11), shown with its rule id; "what this could mean" is INFERENCE;
 * - the suggested action is one of six review actions — the system never suggests contacting a founder;
 * - proxies are public signals, labelled by what they are ("HIRING SIGNAL"), never as headcount, revenue or customers;
 * - a window pair that was not observed on both sides says "not compared" and why;
 * - human steps of the decision trace are "awaiting review" until a person acts — never fabricated;
 * - private investment fields never render, even if a payload carried one (`stripPrivate`).
 */
import type {
  CompsMap,
  Comp,
  Funnel,
  MetricWindow,
  PCompany,
  PDecision,
  PEvent,
  PortfolioAction,
  PortfolioIndexItem,
  Position,
  ProxyValue,
  ReviewInfo,
  SinceReviewRow,
} from "../../lib/types-portfolio.ts";

// ── Hrefs ────────────────────────────────────────────────────────────────────

export const METHODOLOGY_URL = "https://github.com/armaansingla14/Anti-Radar/blob/main/docs/PORTFOLIO_METHODOLOGY.md";

export const portfolioHref = (slug: string) => `/portfolio/${encodeURIComponent(slug)}`;
export const eventHref = (id: string) => `/portfolio/events/${encodeURIComponent(id)}`;
export const compsHref = (slug: string) => `/portfolio/comps#comps-${slug}`;

/** Methodology anchor for a materiality rule id ("M4") or a section. */
export function methodologyHref(section: "materiality" | "funnel" | "comps" | "proxies" | "attention" | "review" = "materiality"): string {
  const anchor: Record<typeof section, string> = {
    materiality: "materiality-rules",
    funnel: "noise-and-the-funnel",
    comps: "competitor-impact",
    proxies: "comparable-windows-and-proxies",
    attention: "needs-attention",
    review: "review-workflow-and-decision-log",
  };
  return `${METHODOLOGY_URL}#${anchor[section]}`;
}

// ── Materiality, priority, action ────────────────────────────────────────────

export const MATERIALITIES = ["MATERIAL", "WATCH", "NOISE"] as const;

export function materialityLabel(m: string | null | undefined): string {
  const v = (m ?? "").toUpperCase();
  return (MATERIALITIES as readonly string[]).includes(v) ? v : "UNCLASSIFIED";
}

export const MATERIALITY_DEFINITION: Record<string, string> = {
  MATERIAL: "Material: a documented rule (M1–M8) says this changes what we should believe about the company",
  WATCH: "Watch: worth knowing, not yet material — capped or classified by a documented rule",
  NOISE: "Noise: counted and filtered by rule (M10) — copy edits, routine releases, low-engagement discussion",
};

export function priorityLabel(p: string | null | undefined): string {
  const v = (p ?? "").toUpperCase();
  return v === "HIGH" || v === "MEDIUM" || v === "LOW" ? `${v} PRIORITY` : "NOT RANKED";
}

const ACTION_LABELS: Record<PortfolioAction, string> = {
  REVIEW: "REVIEW",
  MONITOR: "MONITOR",
  UPDATE_COMPETITIVE_MAP: "UPDATE COMPETITIVE MAP",
  RUN_DILIGENCE: "RUN DILIGENCE",
  PREPARE_FOUNDER_QUESTIONS: "PREPARE FOUNDER QUESTIONS",
  NO_ACTION: "NO ACTION",
};

export const ACTION_DEFINITION: Record<PortfolioAction, string> = {
  REVIEW: "Read the events and their sources; decide whether anything changes",
  MONITOR: "Only events to watch — nothing material yet",
  UPDATE_COMPETITIVE_MAP: "A direct competitor moved — revisit the comparison",
  RUN_DILIGENCE: "A performance or technical claim can be checked against public evidence (V3 diligence)",
  PREPARE_FOUNDER_QUESTIONS: "A change only the team can explain — write the questions down for the next scheduled conversation",
  NO_ACTION: "Nothing to do",
};

/** One of the six review actions. Anything else (including any "contact"/"reach out" wording) reads REVIEW. */
export function actionLabel(a: string | null | undefined): string {
  const v = (a ?? "").toUpperCase() as PortfolioAction;
  return ACTION_LABELS[v] ?? "REVIEW";
}

export function actionKey(a: string | null | undefined): PortfolioAction {
  const v = (a ?? "").toUpperCase() as PortfolioAction;
  return v in ACTION_LABELS ? v : "REVIEW";
}

export const DECISIONS = ["MARK_REVIEWED", "NEEDS_FOLLOW_UP", "MONITOR", "NO_ACTION"] as const;

export function decisionLabel(d: string | null | undefined): string {
  return (d ?? "").replace(/_/g, " ").toUpperCase();
}

export function questionStatusLabel(s: string | null | undefined): string {
  const v = (s ?? "OPEN").toUpperCase();
  return v === "ANSWERED" || v === "DISMISSED" ? v : "OPEN";
}

// ── Event types ──────────────────────────────────────────────────────────────

export const EVENT_TYPES = ["PRODUCT", "TECHNICAL", "TALENT", "COMMERCIAL", "FINANCING", "REGULATORY", "RISK", "STRATEGIC"] as const;
export const RELATIONS = ["SELF", "COMPETITOR", "ADJACENT", "INFRASTRUCTURE", "INCUMBENT", "OPEN_SOURCE"] as const;

/** "PRODUCT", "product_launch" → "PRODUCT · product launch". */
export function typeLabel(type: string | null | undefined, subtype?: string | null): string {
  const t = (type ?? "EVENT").toUpperCase();
  const s = (subtype ?? "").replace(/_/g, " ").trim().toLowerCase();
  return s ? `${t} · ${s}` : t;
}

export function compTypeLabel(t: string | null | undefined): string {
  return (t ?? "COMP").replace(/_/g, " ").toUpperCase();
}

export function relationLabel(r: string | null | undefined): string {
  return (r ?? "").replace(/_/g, " ").toUpperCase();
}

// ── Counts row ───────────────────────────────────────────────────────────────

export interface CountItem {
  key: string;
  label: string;
  value: number;
  /** What exactly is counted. */
  title: string;
  href: string;
}

const TYPE_COUNT_ORDER = ["technical", "competitive", "financing", "product", "talent", "commercial", "risk", "regulatory", "strategic"];
const ALWAYS_SHOWN = new Set(["technical", "competitive", "financing"]);

/**
 * NEED ATTENTION · MATERIAL · WATCH · TECHNICAL · COMPETITIVE · FINANCING (+ other types only when nonzero).
 * MATERIAL and WATCH are distinct events that reach a portfolio company (its own, or through a comp after rule
 * M8) in the window; the type counts split those same events (a comp's event counts as COMPETITIVE).
 */
export function countsRow(counts: Record<string, number> | null | undefined): CountItem[] {
  const c = counts ?? {};
  const relevant = (c.material ?? 0) + (c.watch ?? 0);
  const out: CountItem[] = [
    {
      key: "need_attention",
      label: "Need attention",
      value: c.need_attention ?? 0,
      title: "Portfolio companies ranked HIGH or MEDIUM priority (at most 5 shown)",
      href: "#needs-attention",
    },
    {
      key: "material",
      label: "Material events",
      value: c.material ?? 0,
      title: "Distinct events material for a portfolio company — its own, or a direct comp's (rule M8) — in the 30-day window",
      href: "/portfolio/events?materiality=MATERIAL",
    },
    {
      key: "watch",
      label: "To watch",
      value: c.watch ?? 0,
      title: "Distinct events to watch that reach a portfolio company (own or via a comp), excluding material ones",
      href: "/portfolio/events?materiality=WATCH",
    },
  ];
  for (const k of TYPE_COUNT_ORDER) {
    const v = c[k] ?? 0;
    if (!ALWAYS_SHOWN.has(k) && v === 0) continue;
    out.push({
      key: k,
      label: k === "competitive" ? "Competitive" : k[0].toUpperCase() + k.slice(1),
      value: v,
      title:
        k === "competitive"
          ? `Of the ${relevant} material + watch events, those that are a comp's event (reaching the portfolio company as a competitor)`
          : `Of the ${relevant} material + watch events, the company's own ${k.toUpperCase()} events`,
      href: k === "competitive" ? "/portfolio/events?relation=COMPETITOR" : `/portfolio/events?type=${k.toUpperCase()}`,
    });
  }
  return out;
}

// ── Funnel ───────────────────────────────────────────────────────────────────

const n = (v: number | null | undefined) => (v ?? 0).toLocaleString("en-US");

export interface FunnelStep {
  key: string;
  value: string;
  label: string;
}

/** The stored run's funnel as steps; empty when the run is not available (say why instead). */
export function funnelSteps(f: Funnel | null | undefined): FunnelStep[] {
  if (!f || !f.available) return [];
  return [
    { key: "raw", value: n(f.raw_changes), label: "raw changes observed" },
    { key: "candidates", value: n(f.candidates), label: "candidate events" },
    { key: "dedup", value: n(f.after_dedup), label: "after dedup" },
    { key: "classified", value: `${n(f.material)} material · ${n(f.watch)} watch · ${n(f.noise)} noise`, label: "by rule" },
    { key: "surfaced", value: n(f.companies_surfaced), label: f.companies_surfaced === 1 ? "company surfaced" : "companies surfaced" },
  ];
}

/** "1,215 raw changes observed → 432 candidate events → 284 after dedup → 5 material · 35 watch · 244 noise → 5 companies surfaced". */
export function funnelLine(f: Funnel | null | undefined): string {
  if (!f || !f.available) return `Funnel unavailable${f?.reason ? `: ${f.reason}` : " — no portfolio run stored"}`;
  return funnelSteps(f)
    .map((s) => (s.key === "classified" ? s.value : `${s.value} ${s.label}`))
    .join(" → ");
}

// ── Proxies and comparable windows ───────────────────────────────────────────

const METRIC_NAMES: Record<string, string> = {
  new_technical_roles: "new technical roles",
  releases: "GitHub releases",
  hn_points: "Hacker News points",
  open_technical_roles: "open technical roles",
  contributors: "contributors on public repositories",
  customer_stories_30d: "customer stories published, 30 days",
  hn_points_30d: "Hacker News points, 30 days",
  hn_points_prior_30d: "Hacker News points, the 30 days before",
  new_technical_roles_14d: "technical roles posted, 14 days",
  new_technical_roles_prior_14d: "technical roles posted, the 14 days before",
  papers_90d: "linked arXiv papers, 90 days",
  releases_30d: "GitHub releases, 30 days",
  releases_prior_30d: "GitHub releases, the 30 days before",
  federal_awards: "federal award obligations",
};

/** "proxy_open_technical_roles" → "open technical roles". Never "headcount". */
export function metricName(metric: string | null | undefined): string {
  const k = (metric ?? "").replace(/^proxy_/, "");
  const name = METRIC_NAMES[k] ?? k.replace(/_/g, " ");
  return name.replace(/head\s*count/gi, "roles");
}

/** What kind of public signal a proxy is: "HIRING SIGNAL", "CODE SIGNAL", … (from its label, else its metric name). */
export function proxyKind(p: { metric?: string | null; proxy_label?: string | null }): string {
  const label = (p.proxy_label ?? "").toLowerCase();
  const m = /public\s+(\w+)\s+signal/.exec(label);
  if (m) return `${m[1].toUpperCase()} SIGNAL`;
  const k = (p.metric ?? "").toLowerCase();
  if (/role|job|hiring/.test(k)) return "HIRING SIGNAL";
  if (/release|contributor|repo|commit/.test(k)) return "CODE SIGNAL";
  if (/hn|point|attention/.test(k)) return "ATTENTION SIGNAL";
  if (/paper|research/.test(k)) return "RESEARCH SIGNAL";
  if (/customer|stor/.test(k)) return "CUSTOMER STORIES";
  if (/award|obligation|contract/.test(k)) return "AWARD SIGNAL";
  return "PUBLIC PROXY";
}

/** "(not headcount)" / "(not revenue, not a customer count)" → "not headcount" — what the proxy is not. */
export function proxyIsNot(label: string | null | undefined): string | null {
  const m = /\((not [^)]+)\)/i.exec(label ?? "");
  return m ? m[1] : null;
}

/** The proxy's own description without the "Public … signal:" prefix and the "(not …)" note. */
export function proxyDescription(label: string | null | undefined): string {
  return (label ?? "")
    .replace(/^public\s+\w+\s+signal:\s*/i, "")
    .replace(/\s*\(not [^)]+\)/i, "")
    .trim();
}

const fmtVal = (v: number | null | undefined) =>
  v === null || v === undefined || !Number.isFinite(v) ? "—" : Number.isInteger(v) ? v.toLocaleString("en-US") : v.toFixed(1);

/** "HIRING SIGNAL · 13 open technical roles" — always the signal kind, never headcount. */
export function proxyHeadline(p: ProxyValue): string {
  return `${proxyKind(p)} · ${fmtVal(p.value)} ${metricName(p.metric)}`;
}

/** "last 14 days vs the prior 14" from a window like "14 days". */
export function windowPhrase(window: string | null | undefined): string {
  const m = /(\d+)\s*day/.exec(window ?? "");
  return m ? `last ${m[1]} days vs the prior ${m[1]}` : (window ?? "window unknown");
}

/** "24 vs 32 · last 30 days vs the prior 30" | "not compared: one of the two windows was not observed". */
export function windowText(m: Pick<MetricWindow, "current" | "prior" | "window" | "comparable" | "reason">): string {
  if (!m.comparable) return `not compared: ${m.reason?.trim() || "both windows were not observed"}`;
  return `${fmtVal(m.current)} vs ${fmtVal(m.prior)} · ${windowPhrase(m.window)}`;
}

/** "+8", "−586", "±0", "" when not comparable. */
export function deltaText(m: Pick<MetricWindow, "delta" | "comparable" | "current" | "prior">): string {
  if (!m.comparable) return "";
  const d = m.delta ?? (m.current !== null && m.prior !== null ? (m.current ?? 0) - (m.prior ?? 0) : null);
  if (d === null || !Number.isFinite(d)) return "";
  if (d === 0) return "±0";
  return `${d > 0 ? "+" : "−"}${fmtVal(Math.abs(d))}`;
}

/** Window metrics keep their order; proxies that are only one side of a window pair are dropped (shown as pairs). */
export function standaloneProxies(proxies: ProxyValue[] | null | undefined): ProxyValue[] {
  return (proxies ?? []).filter((p) => !/_(prior_)?(14|30)d$/.test(p.metric) || /customer_stories/.test(p.metric));
}

// ── Private-field boundary ───────────────────────────────────────────────────

/** Keys that are never rendered in public mode (the backend never serialises them; this is defence in depth). */
export const PRIVATE_KEYS = [
  "private_fields", "ownership", "ownership_pct", "cost_basis", "mark", "marks", "valuation_mark", "reserves",
  "round_participation", "board", "board_seat", "board_status", "internal_owner", "internal_notes", "revenue",
  "private_revenue", "customer_count", "customers_count", "internal_metrics",
];
const PRIVATE_RE = new RegExp(`^(${PRIVATE_KEYS.join("|")})$`, "i");

/** Drop any private investment key, at any depth. Arrays and primitives are kept; the input is not mutated. */
export function stripPrivate<T>(value: T): T {
  if (Array.isArray(value)) return value.map((v) => stripPrivate(v)) as T;
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      if (PRIVATE_RE.test(k)) continue;
      out[k] = stripPrivate(v);
    }
    return out as T;
  }
  return value;
}

/** Stage / year exactly as the public source states them, else UNKNOWN. Round is UNKNOWN unless public. */
export function positionFacts(p: Position | null | undefined): { stage: string; year: string; round: string } {
  const safe = stripPrivate(p);
  return {
    stage: safe?.investment_stage?.trim() || "UNKNOWN",
    year: safe?.investment_year ? String(safe.investment_year) : "UNKNOWN",
    round: "UNKNOWN",
  };
}

// ── Event filters ────────────────────────────────────────────────────────────

export interface EventFilters {
  type: string | null;
  /** MATERIAL | WATCH | NOISE | ALL; null = the default (MATERIAL + WATCH). */
  materiality: string | null;
  company: string | null;
  relation: string | null;
}

const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) ?? null;

export function parseEventFilters(sp: Record<string, string | string[] | undefined>): EventFilters {
  let type = one(sp.type)?.toUpperCase() ?? null;
  let relation = one(sp.relation)?.toUpperCase() ?? null;
  // COMPETITIVE is a relation (a comp's event seen from the portfolio company), not a stored event type.
  if (type === "COMPETITIVE") {
    type = null;
    relation = relation ?? "COMPETITOR";
  }
  const mat = one(sp.materiality)?.toUpperCase() ?? null;
  const company = one(sp.company);
  return {
    type: type && (EVENT_TYPES as readonly string[]).includes(type) ? type : null,
    materiality: mat && [...MATERIALITIES, "ALL"].includes(mat) ? mat : null,
    company: company && /^[a-z0-9][a-z0-9._-]{0,120}$/i.test(company) ? company.toLowerCase() : null,
    relation: relation && (RELATIONS as readonly string[]).includes(relation) ? relation : null,
  };
}

/** Link that sets (or clears with null) one filter and keeps the others. */
export function eventFilterHref(current: EventFilters, key: keyof EventFilters, value: string | null): string {
  const next = { ...current, [key]: value };
  const sp = new URLSearchParams();
  for (const k of ["materiality", "type", "relation", "company"] as const) if (next[k]) sp.set(k, next[k]!);
  const qs = sp.toString();
  return qs ? `/portfolio/events?${qs}` : "/portfolio/events";
}

/** The API queries behind a filter: the default (no materiality) is MATERIAL + WATCH; ALL includes NOISE. */
export function eventQueries(f: EventFilters): { type?: string; materiality?: string; company?: string; relation?: string }[] {
  const base = { type: f.type ?? undefined, company: f.company ?? undefined, relation: f.relation ?? undefined };
  if (f.materiality === "ALL") return [base];
  if (f.materiality) return [{ ...base, materiality: f.materiality }];
  return [{ ...base, materiality: "MATERIAL" }, { ...base, materiality: "WATCH" }];
}

/** Merge lists, dedupe by id, newest first. */
export function mergeEvents(lists: PEvent[][]): PEvent[] {
  const seen = new Map<string, PEvent>();
  for (const l of lists) for (const e of l) if (!seen.has(e.id)) seen.set(e.id, e);
  return [...seen.values()].sort((a, b) => b.event_at.localeCompare(a.event_at) || a.id.localeCompare(b.id));
}

/** Every filter link the events page renders (also crawled by the snapshot export). */
export function eventFilterLinks(opts: { companies: string[] }): string[] {
  const none: EventFilters = { type: null, materiality: null, company: null, relation: null };
  return [
    "/portfolio/events",
    ...[...MATERIALITIES, "ALL"].map((m) => eventFilterHref(none, "materiality", m)),
    ...EVENT_TYPES.map((t) => eventFilterHref(none, "type", t)),
    ...["SELF", "COMPETITOR"].map((r) => eventFilterHref(none, "relation", r)),
    ...opts.companies.map((c) => eventFilterHref(none, "company", c)),
  ];
}

// ── Relations through comps ──────────────────────────────────────────────────

export interface CompLink {
  portfolio: PCompany;
  comp_type: string;
  why_comparable: string;
  confidence: string;
}

/** comp slug → the portfolio companies that list it as a comp (one comp can serve several). */
export function compIndex(maps: CompsMap[] | null | undefined): Map<string, CompLink[]> {
  const m = new Map<string, CompLink[]>();
  for (const map of maps ?? []) {
    for (const c of [...map.direct, ...map.emerging, ...map.adjacent]) {
      const list = m.get(c.company.slug) ?? [];
      list.push({ portfolio: map.company, comp_type: c.comp_type, why_comparable: c.why_comparable, confidence: c.confidence });
      m.set(c.company.slug, list);
    }
  }
  return m;
}

export interface RelationView {
  kind: "SELF" | "COMPETITOR" | "COMP" | "UNKNOWN";
  /** Portfolio companies this event is about (SELF) or reaches through a comp. */
  via: CompLink[];
}

/**
 * How an event relates to the portfolio: SELF when its subject is a portfolio company, else the portfolio
 * companies that track the subject as a comp. `relation` from a company-filtered list wins.
 */
export function relationOf(e: Pick<PEvent, "company" | "relation">, portfolio: ReadonlySet<string>, comps: Map<string, CompLink[]>): RelationView {
  if (portfolio.has(e.company.slug) || e.relation === "SELF") return { kind: "SELF", via: [] };
  const via = comps.get(e.company.slug) ?? [];
  if (e.relation && e.relation !== "SELF") return { kind: "COMPETITOR", via };
  if (via.length) return { kind: "COMP", via };
  return { kind: "UNKNOWN", via: [] };
}

// ── Decision trace ───────────────────────────────────────────────────────────

export type TraceState = "system" | "human" | "awaiting";

export interface TraceStep {
  key: "EVENT" | "REVIEW" | "QUESTION" | "ACTION";
  state: TraceState;
  lines: PDecision[];
  /** Shown when there are no lines. */
  placeholder: string | null;
}

const SYSTEM_EVENT = new Set(["detected", "classified", "comps_updated"]);
const HUMAN_REVIEW = new Set(["reviewed", "monitoring_changed"]);
const QUESTION_KINDS = new Set(["question_raised", "question_answered", "question_dismissed", "question_status", "question_reopened"]);
const ACTION_KINDS = new Set(["diligence_requested", "action_taken"]);

/** A decision line written by a person (anything not by `system`). */
export const isHuman = (d: Pick<PDecision, "actor">) => (d.actor ?? "").toLowerCase() !== "system";

/**
 * EVENT → REVIEW → QUESTION → ACTION. System steps show what the system logged; human steps read "awaiting review"
 * until a person acts (only real human lines turn them human). The suggested ACTION is the system's suggestion.
 */
export function traceSteps(decisions: PDecision[] | null | undefined, suggested?: PortfolioAction | null): TraceStep[] {
  const ds = [...(decisions ?? [])].sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? ""));
  const pick = (f: (d: PDecision) => boolean) => ds.filter(f);
  const event = pick((d) => SYSTEM_EVENT.has(d.kind));
  const review = pick((d) => HUMAN_REVIEW.has(d.kind) && isHuman(d));
  const question = pick((d) => QUESTION_KINDS.has(d.kind) || d.kind.startsWith("question"));
  const action = pick((d) => ACTION_KINDS.has(d.kind) && isHuman(d));
  return [
    { key: "EVENT", state: "system", lines: event, placeholder: event.length ? null : "No system log line for this step" },
    { key: "REVIEW", state: review.length ? "human" : "awaiting", lines: review, placeholder: review.length ? null : "Awaiting review — not reviewed" },
    {
      key: "QUESTION",
      state: question.some(isHuman) ? "human" : "system",
      lines: question,
      placeholder: question.length ? null : "No question raised",
    },
    {
      key: "ACTION",
      state: action.length ? "human" : "awaiting",
      lines: action,
      placeholder: action.length ? null : `Awaiting review — no action taken. The system suggests ${actionLabel(suggested ?? "REVIEW")}:`,
    },
  ];
}

export function traceStateLabel(s: TraceState): string {
  return s === "system" ? "SYSTEM" : s === "human" ? "HUMAN" : "AWAITING REVIEW";
}

/** "Not reviewed" until a person reviews; never a fabricated review. */
export function reviewText(r: ReviewInfo | null | undefined): string {
  if (!r) return "Not reviewed";
  return `${decisionLabel(r.decision)} by ${r.reviewer}`;
}

// ── Index helpers ────────────────────────────────────────────────────────────

export function portfolioSlugs(index: PortfolioIndexItem[] | null | undefined): Set<string> {
  return new Set((index ?? []).map((i) => i.company.slug));
}

/** Companies ranked HIGH or MEDIUM (the Needs Attention set). */
export function needsAttentionCount(index: PortfolioIndexItem[] | null | undefined): number {
  return (index ?? []).filter((i) => i.priority === "HIGH" || i.priority === "MEDIUM").length;
}

export interface SinceRow {
  company: PCompany;
  stage: string;
  reviewed: boolean;
  since: string | null;
  material: number;
  watch: number;
  noise: number;
  openQuestions: number;
}

/** Every portfolio company, with its since-last-review counts (or the window, when never reviewed). Busiest first. */
export function sinceRows(index: PortfolioIndexItem[] | null | undefined, since: SinceReviewRow[] | null | undefined): SinceRow[] {
  const by = new Map((since ?? []).map((s) => [s.company.slug, s]));
  const rows: SinceRow[] = (index ?? []).map((i) => {
    const s = by.get(i.company.slug);
    return {
      company: i.company,
      stage: positionFacts(i.position).stage,
      reviewed: Boolean(s?.reviewed),
      since: s?.since ?? null,
      material: s?.material ?? i.material,
      watch: s?.watch ?? i.watch,
      noise: i.noise,
      openQuestions: i.open_questions,
    };
  });
  return rows.sort((a, b) => b.material - a.material || b.watch - a.watch || a.company.name.localeCompare(b.company.name));
}

// ── Comps ────────────────────────────────────────────────────────────────────

export const COMP_GROUPS: { key: "direct" | "emerging" | "adjacent"; label: string; note: string }[] = [
  { key: "direct", label: "Direct competitors", note: "Same buyer, same job" },
  { key: "emerging", label: "Emerging", note: "Open-source projects or companies founded in the last two years" },
  { key: "adjacent", label: "Adjacent · infrastructure · incumbent", note: "Overlapping product, supplier or established player" },
];

export function compCount(m: CompsMap): number {
  return m.direct.length + m.emerging.length + m.adjacent.length;
}

export function allComps(m: CompsMap | null | undefined): Comp[] {
  return m ? [...m.direct, ...m.emerging, ...m.adjacent] : [];
}

// ── Brief ────────────────────────────────────────────────────────────────────

const WORDS = ["Zero", "One", "Two", "Three", "Four", "Five"];

export function thingsWorthKnowing(count: number): string {
  return `${WORDS[count] ?? count} ${count === 1 ? "thing" : "things"} worth knowing`;
}

/** Strip inline "[S3]" markers (rendered separately as links). */
export function stripCites(text: string): string {
  return text.replace(/\s*\[S\d+\]/g, "").trim();
}
