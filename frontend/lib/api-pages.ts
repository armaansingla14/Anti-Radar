/**
 * Typed fetchers + response types for the M5 page routes owned by the pages
 * workstream: Daily Radar v2, stored morning-brief history and single stored
 * briefs. Types mirror the backend contract (`backend/radar/schemas/feeds_v2.py`)
 * and are defined here so `lib/types.ts` stays owned by the home workstream.
 *
 * Every fetcher goes through the same backend resolution as `lib/api.ts`
 * (server: BACKEND_URL / dataset cookie; browser: the same-origin proxy).
 * Pure helpers below the fetchers are unit tested in `tests/pages.test.mts`.
 */
import { ApiError, apiUrl } from "./api.ts";
import { serverFetch } from "./dataSource.ts";
import { countSources } from "./labels.ts";
import { requestCookies, resolveBackend, rethrowNextInternal } from "./session.ts";
import type { ConfidenceLevel } from "./labels.ts";
import type { ChangeItem, EvidenceItem, Generator, ISODate, ISODateTime, JsonObject, MorningBrief, Narrative } from "./types.ts";

// ── Contract types ───────────────────────────────────────────────────────────

/** One Daily Radar v2 entry (backend `DailyItem`): deterministic headline / why_now, never model text. */
export interface DailyItem {
  kind: "formation" | "person" | "company" | "collision" | "strange" | "research" | "watch" | string;
  headline: string;
  why_now: string;
  at: ISODateTime | null;
  /** The stored signals behind the item. */
  sources: EvidenceItem[];
  confidence: ConfidenceLevel | string;
  entity: ChangeItem["entity"] | null;
  /** Display tag, e.g. "POSSIBLE FORMATION". */
  label?: string | null;
  score?: number | null;
  delta?: number | null;
  is_demo?: boolean;
}

export type DailySectionKey =
  | "what_changed"
  | "people"
  | "formations"
  | "companies_accelerating"
  | "research"
  | "strange_signals"
  | "watchlist_changes";

export type DailyRadarV2 = {
  date: ISODate;
  /** Same semantics as /api/daily: the earlier of now−24h and the previous visit. */
  since: ISODateTime;
  data_as_of: ISODate;
  is_snapshot?: boolean;
  summary?: Narrative | null;
} & Record<DailySectionKey, DailyItem[]>;

/** A morning brief as stored for a given day (never generated retroactively). */
export type StoredBrief = MorningBrief & {
  id?: string | null;
  day?: ISODate | null;
  title?: string | null;
  created_at?: ISODateTime | null;
};

export interface BriefArchiveEntry {
  date: ISODate;
  /** ResearchReport id. */
  id: string;
  title: string;
  /** Number of "what changed" lines. */
  items: number;
  /** First lines of "what changed" (<= 3). */
  headlines?: string[];
  generator?: Generator | string;
  generated_at?: ISODateTime | null;
  is_demo?: boolean;
}

// ── Fetchers ─────────────────────────────────────────────────────────────────

const isServer = typeof window === "undefined";

async function getJson<T>(path: string, params?: Record<string, string>): Promise<T> {
  let backend: string | undefined;
  if (isServer) {
    const resolved = resolveBackend((await requestCookies()).dataset);
    if (!resolved.ok) throw new ApiError(503, path, resolved.detail);
    backend = resolved.base;
  }
  let res: Response;
  try {
    res = await (isServer ? serverFetch : fetch)(apiUrl(path, params, backend), { method: "GET", cache: "no-store", headers: { Accept: "application/json" } });
  } catch (err) {
    await rethrowNextInternal(err);
    if (isServer) throw new ApiError(503, path, "backend_waking");
    throw err;
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    let detail = text.slice(0, 500) || res.statusText;
    try {
      const body = JSON.parse(text) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, path, detail);
  }
  return (await res.json()) as T;
}

export const getDailyV2 = (params?: { since?: string }) => getJson<DailyRadarV2>("/daily/v2", params?.since ? { since: params.since } : undefined);
export const getBriefArchive = () => getJson<BriefArchiveEntry[]>("/brief/archive");
/** `day` is "YYYY-MM-DD" or "today". 404 `no_brief_for_date` when none was stored. */
export const getBriefDay = (day: string) => getJson<StoredBrief>(`/brief/day/${encodeURIComponent(day)}`);

/**
 * A stored brief, `null` when none was stored for that day (404), or `"unavailable"`
 * when the route itself is missing/failing — never a fabricated brief.
 */
export async function loadBriefDay(day: string): Promise<StoredBrief | null | "unavailable"> {
  try {
    return await getBriefDay(day);
  } catch (err) {
    await rethrowNextInternal(err);
    if (err instanceof ApiError && err.status === 404) return err.detail === "no_brief_for_date" ? null : "unavailable";
    return "unavailable";
  }
}

/** Archive list, or null when the archive route is unavailable. */
export async function loadBriefArchive(): Promise<BriefArchiveEntry[] | null> {
  try {
    const list = await getBriefArchive();
    return Array.isArray(list) ? list : null;
  } catch (err) {
    await rethrowNextInternal(err);
    return null;
  }
}

// ── Pure helpers (unit tested) ───────────────────────────────────────────────

const DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** "today" or a real calendar date "YYYY-MM-DD"; anything else is rejected. */
export function parseBriefDateParam(raw: string | null | undefined): string | null {
  const v = (raw ?? "").trim();
  if (v === "today") return "today";
  const m = DATE_RE.exec(v);
  if (!m) return null;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  return d.getUTCFullYear() === Number(m[1]) && d.getUTCMonth() === Number(m[2]) - 1 && d.getUTCDate() === Number(m[3]) ? v : null;
}

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** "2026-10-20" → "OCTOBER 20" (calendar date, no timezone shift). */
export function briefDateLabel(day: string | null | undefined): string {
  const m = DATE_RE.exec((day ?? "").slice(0, 10));
  if (!m) return "UNDATED";
  return `${MONTHS[Number(m[2]) - 1].toUpperCase()} ${Number(m[3])}`;
}

/** "ANTI RADAR · OCTOBER 20 · 5 things worth knowing today" — the count is the real item count. */
export function briefKicker(day: string | null | undefined, count: number, isToday: boolean): string {
  const things = count === 1 ? "1 thing" : `${count} things`;
  return `ANTI RADAR · ${briefDateLabel(day)} · ${things} worth knowing ${isToday ? "today" : "that day"}`;
}

/** Number of items an archive entry holds (null when the backend did not say). */
export function archiveItemCount(e: { items: unknown }): number | null {
  if (Array.isArray(e.items)) return e.items.length;
  return typeof e.items === "number" && Number.isFinite(e.items) ? e.items : null;
}

/** Shift a "YYYY-MM-DD" by whole days (UTC calendar arithmetic). */
export function shiftDay(day: string, delta: number): string | null {
  const m = DATE_RE.exec(day);
  if (!m) return null;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]) + delta));
  return d.toISOString().slice(0, 10);
}

// ── Daily v2 normalization ───────────────────────────────────────────────────

export interface DailyRow {
  key: string;
  headline: string;
  whyNow: string | null;
  at: string | null;
  sources: number;
  confidence: string | null;
  href: string | null;
  entityType: string | null;
  evidence: EvidenceItem[];
  isDemo: boolean;
  label: string | null;
  score: number | null;
  delta: number | null;
}

const s = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v.trim() : null);
const n = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const obj = (v: unknown): JsonObject | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as JsonObject) : null);

function entityHref(type: string | null, slug: string | null): string | null {
  if (!type || !slug) return null;
  const e = encodeURIComponent(slug);
  if (type === "company") return `/companies/${e}`;
  if (type === "person") return `/founders/${e}`;
  if (type === "formation") return `/formation/${e}`;
  return null;
}

function asEvidence(v: unknown): EvidenceItem[] {
  if (!Array.isArray(v)) return [];
  return v
    .map((x) => obj(x))
    .filter((x): x is JsonObject => !!x && !!s(x.title))
    .map((x) => ({
      signal_id: s(x.signal_id),
      title: s(x.title)!,
      detail: s(x.detail),
      source_type: s(x.source_type),
      source_url: s(x.source_url),
      event_at: s(x.event_at),
      weight: n(x.weight),
    }));
}

/**
 * Normalize one Daily v2 section entry into a display row. Accepts the contract
 * `ChangeItem` and tolerates richer card shapes (IntelCard, MeetCard, FormationCard,
 * PaperCard, StrangeSignal, WatchChange) so the page never drops honest data.
 */
export function toDailyItem(raw: unknown, i: number): DailyRow | null {
  const o = obj(raw);
  if (!o) return null;
  const person = obj(o.person);
  const entity = obj(o.entity) ?? obj(o.anchor);
  const evidence = [o.sources, o.evidence, o.why_now, o.why_surfaced].map(asEvidence).find((l) => l.length > 0) ?? [];
  const headline = s(o.headline) ?? s(o.name) ?? s(o.title) ?? s(person?.name) ?? s(entity?.name) ?? null;
  if (!headline) return null;
  const whyNow =
    (typeof o.why_now === "string" ? s(o.why_now) : null) ??
    s(o.why_now_sentence) ??
    s(o.why_it_matters) ??
    s(o.detail) ??
    (Array.isArray(o.changes) ? s(o.changes[0]) : null) ??
    (Array.isArray(o.what_changed) ? s(o.what_changed[0]) : null) ??
    evidence[0]?.title ??
    null;
  const type = s(entity?.type) ?? s(o.entity_type) ?? (person ? "person" : null) ?? (s(o.arxiv_id) ? "paper" : null);
  const slug = s(entity?.slug) ?? s(o.slug) ?? s(person?.slug) ?? null;
  const kind = s(o.kind);
  const href =
    s(o.href) ??
    (kind === "formation" || (s(o.tier) && s(o.label) && Array.isArray(o.members)) ? entityHref("formation", s(o.slug) ?? slug) : null) ??
    entityHref(type, slug) ??
    (type === "paper" ? s(o.url) : null);
  const at =
    s(o.at) ?? s(o.detected_at) ?? s(o.latest_activity_at) ?? s(o.published_at) ?? s(o.first_seen_at) ?? evidence[0]?.event_at ?? null;
  const sources = n(o.sources) ?? n(o.sources_count) ?? n(o.evidence_count) ?? (evidence.length ? countSources(evidence) : n(o.independent_signals) ?? 0);
  return {
    key: `${s(o.id) ?? s(entity?.id) ?? s(person?.id) ?? slug ?? headline}-${i}`,
    headline,
    whyNow: whyNow && whyNow !== headline ? whyNow : null,
    at,
    sources,
    confidence: s(o.confidence),
    label: s(o.label),
    score: n(o.score),
    delta: n(o.delta),
    href,
    entityType: kind ?? type,
    evidence,
    isDemo: o.is_demo === true || person?.is_demo === true,
  };
}

export function dailyRows(items: unknown[] | null | undefined, limit?: number): DailyRow[] {
  const rows = (items ?? []).map(toDailyItem).filter((r): r is DailyRow => r !== null);
  return limit ? rows.slice(0, limit) : rows;
}

/** Display order and copy for the v2 Daily Radar sections. */
export const DAILY_SECTIONS: { key: DailySectionKey; title: string; empty: string }[] = [
  { key: "what_changed", title: "What changed", empty: "Nothing on the radar changed materially in this window." },
  { key: "people", title: "People to know", empty: "No person cleared the bar in this window." },
  { key: "formations", title: "Formations", empty: "No new formation signals in this window." },
  { key: "companies_accelerating", title: "Companies accelerating", empty: "No company accelerated in this window." },
  { key: "research", title: "Research that matters", empty: "No thesis-relevant paper arrived in this window." },
  { key: "strange_signals", title: "Strange signals", empty: "No unusual pattern cleared the detectors." },
  { key: "watchlist_changes", title: "Watchlist changes", empty: "Nothing on the watchlist moved." },
];
