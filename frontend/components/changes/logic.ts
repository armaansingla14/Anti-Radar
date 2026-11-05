/**
 * SINCE LAST SNAPSHOT — pure display logic (no React, relative imports only; tested by
 * tests/changes.test.mts under Node type stripping).
 *
 * Honesty rules encoded here:
 * - a score delta is shown only when the backend says it is comparable; otherwise the reason is shown;
 * - WEAKENED and DROPPED are rendered as prominently as STRENGTHENED;
 * - "new" means new to Radar, so first-seen and created dates are kept apart;
 * - numbers are exactly as returned (no re-derivation, no rounding beyond display).
 */
import { externalHref, hrefFor } from "../../lib/format.ts";
import type { EntityRef, FormationChangeBadge, SinceSnapshot } from "../../lib/types.ts";
import type { ChangesReport, FormationChange, FormationChangeStatus, ReportChangeItem, ScoreChange } from "../../lib/types-changes.ts";

// ── Dates ────────────────────────────────────────────────────────────────────

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function parse(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T00:00:00Z` : iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "2026-10-20" → "Oct 20" (UTC); with `year`: "Oct 20, 2026". null when unparseable. */
export function dayLabel(iso: string | null | undefined, opts: { year?: boolean } = {}): string | null {
  const d = parse(iso);
  if (!d) return null;
  const base = `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
  return opts.year ? `${base}, ${d.getUTCFullYear()}` : base;
}

/** "Mar 2022" — for creation dates, where the month is the useful precision. */
export function monthLabel(iso: string | null | undefined): string | null {
  const d = parse(iso);
  return d ? `${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}` : null;
}

/** "since Oct 20" (lower case; callers upper-case via the label style when needed). */
export function sinceLabel(baseDay: string | null | undefined): string | null {
  const d = dayLabel(baseDay);
  return d ? `since ${d}` : null;
}

/** Backend reasons carry ISO days ("Not re-observed since 2026-10-20: …"); show them as "Oct 20". */
export function humanizeDays(text: string): string {
  return text.replace(/\b(\d{4}-\d{2}-\d{2})\b/g, (m) => dayLabel(m) ?? m);
}

/** Signed with a true minus sign: +14, −16, 0 (one decimal only when present). */
export function signed(n: number, digits = 1): string {
  const r = Number(n.toFixed(digits));
  if (r === 0) return "0";
  const mag = Math.abs(r).toLocaleString("en-US", { maximumFractionDigits: digits });
  return `${r > 0 ? "+" : "−"}${mag}`;
}

/** 40.4 → "40.4", 45 → "45". */
export function num(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Number(n.toFixed(digits)).toLocaleString("en-US", { maximumFractionDigits: digits });
}

// ── Entity header: "+11 since Oct 20" ────────────────────────────────────────

export type SinceView =
  | { kind: "none" }
  | { kind: "delta"; delta: number; text: string; tone: "up" | "down" | "flat"; title: string }
  | { kind: "not-compared"; text: string; reason: string };

/**
 * What the score header shows about the previous snapshot. A delta only when `comparable` and a delta is
 * present; otherwise a quiet "not compared" note carrying the backend's reason. Nothing when absent.
 */
export function sinceView(since: SinceSnapshot | null | undefined): SinceView {
  if (!since) return { kind: "none" };
  const label = sinceLabel(since.base_day) ?? "since the previous snapshot";
  if (since.comparable && typeof since.delta === "number" && !Number.isNaN(since.delta)) {
    const d = since.delta;
    const tone = d > 0 ? "up" : d < 0 ? "down" : "flat";
    const text = d === 0 ? `unchanged ${label}` : `${signed(d)} ${label}`;
    const from = dayLabel(since.base_day) ?? since.base_day;
    const to = dayLabel(since.current_day) ?? since.current_day;
    const title = `${from}: ${num(since.base_score)} → ${to}: ${num(since.current_score)} (cohort-relative percentile score)`;
    return { kind: "delta", delta: d, text, tone, title };
  }
  const reason = since.reason ? humanizeDays(since.reason) : "No comparable score on both days.";
  return { kind: "not-compared", text: `not compared ${label}`, reason };
}

// ── Change badges ────────────────────────────────────────────────────────────

export type BadgeStatus = FormationChangeStatus;
export type BadgeTone = "new" | "up" | "down" | "dropped" | "flat";

export interface BadgeMeta {
  tone: BadgeTone;
  glyph: string;
  text: string;
  /** "strong" badges are filled; WEAKENED and DROPPED are exactly as strong as STRENGTHENED. */
  weight: "strong" | "outline" | "quiet";
}

export const BADGE: Record<BadgeStatus, BadgeMeta> = {
  NEW: { tone: "new", glyph: "+", text: "New", weight: "outline" },
  STRENGTHENED: { tone: "up", glyph: "↑", text: "Strengthened", weight: "strong" },
  WEAKENED: { tone: "down", glyph: "↓", text: "Weakened", weight: "strong" },
  DROPPED: { tone: "dropped", glyph: "×", text: "Dropped", weight: "strong" },
  UNCHANGED: { tone: "flat", glyph: "→", text: "Unchanged", weight: "quiet" },
};

/** Badge meta for any status string; unknown statuses fall back to UNCHANGED's quiet look but keep their name. */
export function badgeMeta(status: string): BadgeMeta {
  const key = status.toUpperCase() as BadgeStatus;
  return BADGE[key] ?? { tone: "flat", glyph: "·", text: status, weight: "quiet" };
}

/** Tooltip for a formation card badge: "since Oct 20: 40.4 → 45". */
export function formationBadgeTitle(change: FormationChangeBadge, currentScore: number | null | undefined, currentTier?: string | null): string {
  const since = sinceLabel(change.base_day) ?? "since the previous snapshot";
  if (change.status === "NEW") return `New ${since}: not a formation candidate on ${dayLabel(change.base_day) ?? change.base_day}`;
  const scores = `${num(change.base_score)} → ${num(change.current_score ?? currentScore ?? null)}`;
  const tiers = change.base_tier && currentTier && change.base_tier !== currentTier ? ` · tier ${change.base_tier} → ${currentTier}` : "";
  const why = change.status === "UNCHANGED" ? ` · same tier, move under the ${change.threshold ?? 5}-point threshold` : "";
  return `${since}: ${scores}${tiers}${why}`;
}

// ── Home block ───────────────────────────────────────────────────────────────

export interface HomeCount {
  key: string;
  label: string;
  count: number;
  /** /changes#section */
  href: string;
  /** Rendered with the same weight as gains (never softened). */
  tone: "plain" | "up" | "down";
}

export const SECTION_IDS = {
  companies: "new-companies",
  people: "new-people",
  repositories: "new-repositories",
  papers: "new-papers",
  research: "new-research",
  hiring: "new-hiring",
  signals: "signals",
  scores: "scores",
  momentum: "momentum",
  collisions: "collisions",
  formation: "formation",
  meet: "meet",
  caveats: "caveats",
} as const;

export const changesHref = (section: keyof typeof SECTION_IDS): string => `/changes#${SECTION_IDS[section]}`;

/** True when there is a report worth showing (two snapshot days compared). */
export function hasReport(report: ChangesReport | null | undefined): report is ChangesReport {
  return !!report && report.available === true && !!report.base_day;
}

/**
 * The home "SINCE OCT 20" counts. Score moves only when the scores were comparable; formation only when
 * formation history exists on both days; new hiring only when there is some. Empty when unavailable.
 */
export function homeCounts(report: ChangesReport | null | undefined): HomeCount[] {
  if (!hasReport(report)) return [];
  const out: HomeCount[] = [
    { key: "companies", label: "New companies", count: report.new_companies.count, href: changesHref("companies"), tone: "plain" },
    { key: "people", label: "New people", count: report.new_people.count, href: changesHref("people"), tone: "plain" },
  ];
  if (report.formation.available)
    out.push({ key: "formations", label: "New formations", count: report.formation.counts.NEW ?? 0, href: changesHref("formation"), tone: "plain" });
  if (report.score_changes.comparable) {
    out.push({ key: "accelerated", label: "Accelerated", count: report.score_changes.accelerated.count, href: changesHref("scores"), tone: "up" });
    out.push({ key: "weakened", label: "Weakened", count: report.score_changes.weakened.count, href: changesHref("scores"), tone: "down" });
  }
  out.push({ key: "collisions", label: "New collisions", count: report.new_collisions.count, href: changesHref("collisions"), tone: "plain" });
  if (report.new_jobs.count > 0)
    out.push({ key: "hiring", label: "New hiring", count: report.new_jobs.count, href: changesHref("hiring"), tone: "plain" });
  return out;
}

/** One-line caveat under the home block. */
export function homeCaveat(report: ChangesReport): string {
  const s = report.score_changes;
  if (!s.comparable) return `Scores are not compared: ${s.reason ? humanizeDays(s.reason) : "no comparable live scores on both days."}`;
  const n = s.not_reobserved;
  const tail = n > 0 ? `; ${n.toLocaleString("en-US")} ${n === 1 ? "company was" : "companies were"} not re-observed and ${n === 1 ? "is" : "are"} not compared` : "";
  return `Scores are cohort-relative${tail}. "New" means new to Radar, not newly created.`;
}

// ── /changes page ────────────────────────────────────────────────────────────

/** "What changed since Oct 20". */
export function changesTitle(report: Pick<ChangesReport, "base_day"> | null | undefined): string {
  const s = sinceLabel(report?.base_day);
  return s ? `What changed ${s}` : "What changed";
}

/** "Oct 20, 2026 → Nov 5, 2026 · verified snapshots". */
export function rangeLine(report: Pick<ChangesReport, "base_day" | "current_day" | "is_snapshot">): string {
  const a = dayLabel(report.base_day, { year: true }) ?? "—";
  const b = dayLabel(report.current_day, { year: true }) ?? "—";
  return `${a} → ${b} · ${report.is_snapshot ? "verified snapshots" : "snapshot → live database"}`;
}

/** Only strict ISO days pass through as ?base= / ?current=. */
export function parseRange(sp: Record<string, string | string[] | undefined>): { base?: string; current?: string } {
  const pick = (v: string | string[] | undefined) => {
    const s = (Array.isArray(v) ? v[0] : v)?.trim();
    return s && /^\d{4}-\d{2}-\d{2}$/.test(s) ? s : undefined;
  };
  const out: { base?: string; current?: string } = {};
  const base = pick(sp.base);
  const current = pick(sp.current);
  if (base) out.base = base;
  if (current) out.current = current;
  return out;
}

/** "first seen Nov 5 · created Mar 2022" — new to Radar ≠ newly created. */
export function seenVsCreated(item: Pick<ReportChangeItem, "first_seen_at" | "created_at" | "entity_type">): { seen: string | null; created: string | null } {
  const seen = dayLabel(item.first_seen_at);
  const verb = item.entity_type === "paper" ? "published" : item.entity_type === "job" ? "posted" : "created";
  const createdDay = item.entity_type === "company" || item.entity_type === "repository" ? monthLabel(item.created_at) : dayLabel(item.created_at, { year: true });
  return { seen: seen ? `first seen ${seen}` : null, created: createdDay ? `${verb} ${createdDay}` : null };
}

const COMPONENT_LABEL: Record<string, string> = {
  technical_momentum: "Momentum",
  acceleration: "Acceleration",
  attention_gap: "Attention gap",
  founder_signal: "Founder",
  thesis_fit: "Thesis fit",
  novelty: "Novelty",
};

export function componentLabel(key: string): string {
  if (COMPONENT_LABEL[key]) return COMPONENT_LABEL[key];
  const s = key.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Largest component moves (|Δ| ≥ 0.5 after rounding to one decimal), biggest first: [{label, text, value}]. */
export function topComponents(change: Pick<ScoreChange, "components_delta">, limit = 3): { key: string; label: string; value: number; text: string }[] {
  return Object.entries(change.components_delta ?? {})
    .map(([key, v]) => ({ key, value: Number(v.toFixed(1)) }))
    .filter((c) => Math.abs(c.value) >= 0.5)
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value) || a.key.localeCompare(b.key))
    .slice(0, limit)
    .map((c) => ({ ...c, label: componentLabel(c.key), text: signed(c.value) }));
}

/** "Rank 3 → 1", "New at rank 1", "Was rank 4". */
export function meetRankText(m: { base_rank: number | null; current_rank: number | null }): string {
  if (m.base_rank === null && m.current_rank !== null) return `entered at #${m.current_rank}`;
  if (m.current_rank === null && m.base_rank !== null) return `was #${m.base_rank}`;
  return `#${m.base_rank ?? "—"} → #${m.current_rank ?? "—"}`;
}

/** "Formation dropped" quiet line for /formation: null when nothing dropped or unavailable. */
export function droppedLine(report: ChangesReport | null | undefined): string | null {
  if (!hasReport(report) || !report.formation.available) return null;
  const n = report.formation.counts.DROPPED ?? 0;
  if (n <= 0) return null;
  const since = sinceLabel(report.formation.base_day ?? report.base_day) ?? "since the previous snapshot";
  return `${n} ${n === 1 ? "candidate" : "candidates"} dropped ${since}`;
}

/** Score-change rows always keep the backend order; this just tells whether a section has anything to show. */
export function sectionState(count: number, items: unknown[]): "empty" | "listed" | "truncated" {
  if (count <= 0) return "empty";
  return items.length < count ? "truncated" : "listed";
}

// ── Links ────────────────────────────────────────────────────────────────────

const PAGE_TYPES = new Set(["company", "person"]);

/** In-app page for a company / person reference; null for other kinds (repositories, papers…). */
export function refHref(ref: Pick<EntityRef, "type" | "slug" | "id"> | null | undefined): string | null {
  if (!ref || !PAGE_TYPES.has(ref.type)) return null;
  return hrefFor(ref.type, ref.slug || ref.id);
}

/**
 * Where an item's name links: the entity page for companies / people, otherwise the source URL
 * (GitHub, arXiv, the job posting), only when it is a safe http(s) link.
 */
export function itemHref(item: Pick<ReportChangeItem, "entity_type" | "slug" | "id" | "url">): { href: string; external: boolean } | null {
  if (PAGE_TYPES.has(item.entity_type)) return { href: hrefFor(item.entity_type, item.slug || item.id), external: false };
  const ext = externalHref(item.url, false);
  return ext ? { href: ext, external: true } : null;
}

/**
 * A formation change's link: the candidate page while it still exists; for DROPPED (no card any more) the
 * anchor's own page (candidate slugs are "<kind>-<anchor slug>").
 */
export function formationChangeHref(fc: Pick<FormationChange, "status" | "slug" | "kind" | "anchor_type" | "anchor_id" | "anchor_slug">): string | null {
  if (fc.status !== "DROPPED") return `/formation/${encodeURIComponent(fc.slug)}`;
  if (!PAGE_TYPES.has(fc.anchor_type)) return null;
  if (fc.anchor_slug) return hrefFor(fc.anchor_type, fc.anchor_slug);
  const prefix = `${fc.kind}-`;
  const anchorSlug = fc.slug.startsWith(prefix) ? fc.slug.slice(prefix.length) : "";
  return hrefFor(fc.anchor_type, anchorSlug || fc.anchor_id);
}
