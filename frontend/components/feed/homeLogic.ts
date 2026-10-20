/**
 * Pure logic for the Home page (no React, relative imports only — tested by
 * tests/home.test.mts under Node type stripping).
 */
import type {
  ChangeItem,
  EvidenceItem,
  FormationCard,
  FormationFeed,
  HomeFeed,
  HomeSummary,
  HomeWindow,
  IntelCard,
  MeetCard,
  MeetFeed,
  RadarFeed,
  StrangeSignal,
} from "../../lib/types.ts";
import { updatedAgo } from "../../lib/labels.ts";

// ── Query ────────────────────────────────────────────────────────────────────

/** ?new=today|week (legacy ?new=1 → week); anything else → all. */
export function parseWindow(raw: string | string[] | undefined): HomeWindow | null {
  const v = (Array.isArray(raw) ? raw[0] : raw)?.trim().toLowerCase();
  if (v === "today") return "today";
  if (v === "week" || v === "1") return "week";
  return null;
}

export function windowHref(w: HomeWindow | null): string {
  return w ? `/?new=${w}` : "/";
}

export const WINDOW_TABS: { key: HomeWindow | null; label: string; title: string }[] = [
  { key: null, label: "All", title: "Everything currently on the radar" },
  { key: "today", label: "New today", title: "Entities first seen on the data day" },
  { key: "week", label: "New this week", title: "Unknown yesterday — first seen in the last 7 days" },
];

// ── Header ───────────────────────────────────────────────────────────────────

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Oct 20, 2026" (UTC). */
export function shortDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T00:00:00Z` : iso);
  if (Number.isNaN(d.getTime())) return null;
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`;
}

/**
 * Header status line. Live: "Updated 12 minutes ago". Snapshot:
 * "VERIFIED SNAPSHOT · collected Oct 20, 2026 · 3 days old".
 */
export function homeStatusLine(s: Pick<HomeSummary, "updated_at" | "data_as_of" | "is_snapshot" | "snapshot_age_days">, now: Date): string {
  if (s.is_snapshot) {
    const collected = shortDate(s.data_as_of) ?? "date unknown";
    const age =
      s.snapshot_age_days === null || s.snapshot_age_days === undefined
        ? "age unknown"
        : s.snapshot_age_days === 0
          ? "collected today"
          : `${s.snapshot_age_days} ${s.snapshot_age_days === 1 ? "day" : "days"} old`;
    return `VERIFIED SNAPSHOT · collected ${collected} · ${age}`;
  }
  return updatedAgo(s.updated_at, now);
}

// ── Counts ───────────────────────────────────────────────────────────────────

export interface CountDef {
  key: keyof Pick<HomeSummary, "possible_formations" | "people_to_meet" | "companies_accelerating" | "signal_collisions" | "strange_signals">;
  label: string;
  href: string;
}

export const COUNT_DEFS: CountDef[] = [
  { key: "possible_formations", label: "Possible formations", href: "/formation" },
  { key: "people_to_meet", label: "People to meet", href: "/meet" },
  { key: "companies_accelerating", label: "Companies accelerating", href: "#companies" },
  { key: "signal_collisions", label: "Signal collisions", href: "#what-changed" },
  { key: "strange_signals", label: "Strange signals", href: "#strange" },
];

// ── What changed ─────────────────────────────────────────────────────────────

const KIND_LABEL: Record<string, string> = {
  formation: "Formation",
  person: "Person",
  company: "Company",
  collision: "Signal collision",
  strange: "Strange signal",
  research: "Research",
};

export function changeKindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind.replace(/_/g, " ");
}

/** In-app link for a change item (entity route by type; search as a never-dead fallback). */
export function changeHref(item: Pick<ChangeItem, "kind" | "entity">): string | null {
  const e = item.entity;
  if (!e) return null;
  const key = encodeURIComponent(e.slug || e.id);
  switch (e.type) {
    case "company":
      return `/companies/${key}`;
    case "person":
      return `/founders/${key}`;
    case "formation":
    case "team":
      return `/formation/${key}`;
    default:
      return `/search?q=${encodeURIComponent(e.name)}`;
  }
}

/** "01" … "08" */
export const changeIndex = (i: number): string => String(i + 1).padStart(2, "0");

// ── Fallback composition (backend without GET /api/home) ─────────────────────

const DAY = 86_400_000;

function ts(v: string | null | undefined): number | null {
  if (!v) return null;
  const t = Date.parse(/^\d{4}-\d{2}-\d{2}$/.test(v) ? `${v}T00:00:00Z` : v);
  return Number.isNaN(t) ? null : t;
}

function latest(items: EvidenceItem[]): string | null {
  let best: string | null = null;
  for (const e of items) if (e.event_at && (best === null || (ts(e.event_at) ?? 0) > (ts(best) ?? 0))) best = e.event_at;
  return best;
}

/** First seen within the window ending at the end of the data day. */
export function inWindow(firstSeen: string | null | undefined, asOf: string | null | undefined, w: HomeWindow | null): boolean {
  if (!w) return true;
  const t = ts(firstSeen);
  const end = ts(asOf);
  if (t === null || end === null) return false;
  const endOfDay = end + DAY;
  return t < endOfDay && t >= endOfDay - (w === "today" ? 1 : 7) * DAY;
}

const TIER_CONFIDENCE: Record<string, string> = { high: "HIGH CONFIDENCE", emerging: "MEDIUM CONFIDENCE", early: "EARLY SIGNAL" };

function fromFormation(f: FormationCard): ChangeItem {
  return {
    kind: "formation",
    headline: f.title,
    why_now: f.why_now[0]?.title ?? `${f.independent_signals} independent signals`,
    entity: { type: "formation", id: f.id, slug: f.slug, name: f.title },
    evidence: f.why_now,
    confidence: TIER_CONFIDENCE[f.tier] ?? null,
    at: f.latest_activity_at ?? latest(f.why_now),
  };
}

function fromMeet(m: MeetCard): ChangeItem {
  return {
    kind: "person",
    headline: m.person.name,
    why_now: m.what_changed[0] ?? m.why_now[0]?.title ?? m.why_person[0] ?? "",
    entity: { type: "person", id: m.person.id, slug: m.person.slug, name: m.person.name },
    evidence: m.why_now,
    confidence: m.confidence,
    at: latest(m.why_now),
  };
}

function fromCard(c: IntelCard): ChangeItem {
  const ev = [...c.why_surfaced, ...(c.collision?.evidence ?? [])];
  const collides = (c.collision?.families.length ?? 0) >= 2;
  return {
    kind: collides ? "collision" : "company",
    headline: c.why_surfaced[0] ? `${c.name}: ${c.why_surfaced[0].title}` : c.name,
    why_now: (collides ? c.collision?.summary : null) ?? c.why_surfaced[1]?.title ?? c.one_liner ?? "",
    entity: { type: c.entity_type, id: c.id, slug: c.slug, name: c.name },
    evidence: ev,
    confidence: c.confidence ?? null,
    at: latest(ev),
  };
}

function fromStrange(s: StrangeSignal): ChangeItem {
  const e = s.entities[0];
  return {
    kind: "strange",
    headline: s.headline,
    why_now: s.detail,
    entity: e ? { type: e.type, id: e.id ?? e.slug ?? e.name, slug: e.slug ?? null, name: e.name } : null,
    evidence: s.evidence,
    confidence: null,
    at: s.detected_at,
  };
}

/** Round-robin across kinds so one source cannot crowd the feed; ≤ limit items. */
export function interleave<T>(lists: T[][], limit: number): T[] {
  const out: T[] = [];
  for (let i = 0; out.length < limit && lists.some((l) => i < l.length); i++) {
    for (const l of lists) if (i < l.length && out.length < limit) out.push(l[i]);
  }
  return out;
}

/**
 * Compose a HomeFeed from the legacy feeds when GET /api/home is not available
 * (an older backend). Counts come straight from those feeds; nothing is invented.
 */
export function composeHomeFallback(
  radar: RadarFeed,
  formation: FormationFeed | null,
  meet: MeetFeed | null,
  w: HomeWindow | null,
): HomeFeed {
  const asOf = radar.data_as_of ?? radar.date;
  const forms = formation ? [...formation.high, ...formation.emerging, ...formation.early] : [];
  const formsIn = forms.filter((f) => inWindow(f.first_detected_at, asOf, w)).sort((a, b) => b.score - a.score);
  const people = (meet?.people ?? []).filter((m) => inWindow(m.person.first_seen_at, asOf, w));
  const cards = radar.cards.filter((c) => inWindow(c.first_seen_at, asOf, w));
  const strange = radar.strange_signals;
  const collisions = cards.filter((c) => (c.collision?.families.length ?? 0) >= 2);
  return {
    summary: {
      updated_at: radar.generated_at,
      data_as_of: asOf,
      is_snapshot: Boolean(radar.is_snapshot),
      snapshot_age_days: radar.snapshot_age_days ?? null,
      possible_formations: w ? formsIn.length : (formation?.counts.total ?? 0),
      people_to_meet: people.length,
      companies_accelerating: cards.length,
      signal_collisions: collisions.length,
      strange_signals: w ? strange.length : radar.counts.strange_signals,
    },
    what_changed: interleave(
      [formsIn.map(fromFormation), people.map(fromMeet), cards.map(fromCard), strange.map(fromStrange)],
      8,
    ),
    formations: formsIn.slice(0, 3),
    people_to_meet: people.slice(0, 5),
    companies: cards.slice(0, 6),
    strange_signals: strange.slice(0, 3),
  };
}
