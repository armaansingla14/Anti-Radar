/**
 * Pure display logic for the Formation Radar (no React, relative imports only,
 * so tests/formation.test.mts can run it under Node's type stripping).
 *
 * Honesty rules encoded here: never say "founded" without a FOUNDED relation;
 * profile-derived affiliations are labelled self-reported; empty states say so.
 */
import type {
  AntiScore,
  EntityRef,
  FormationCard,
  FormationCounts,
  FormationMember,
  GraphEdge,
  GraphNode,
  GraphOut,
  RelationshipOut,
  ScoreComponent,
} from "../../lib/types.ts";

// ── Tiers ────────────────────────────────────────────────────────────────────

export const TIERS = [
  { key: "high", title: "High confidence", note: "Converging team, dated formation evidence, several independent sources" },
  { key: "emerging", title: "Emerging", note: "Formation evidence present; corroboration still building" },
  { key: "early", title: "Early signal", note: "A first formation signal — watch, do not conclude" },
] as const;

export type TierKey = (typeof TIERS)[number]["key"];

export function tierTitle(tier: string): string {
  return TIERS.find((t) => t.key === tier)?.title ?? tier;
}

// ── Source families ──────────────────────────────────────────────────────────

const FAMILY_WORD: Record<string, string> = {
  github: "GitHub",
  research: "Research",
  hiring: "Hiring",
  web: "Web",
  community: "Community",
  social: "Social",
};
const FAMILY_ORDER = Object.keys(FAMILY_WORD);
/** Collision-family keys emitted by the scorer → display families. */
const FAMILY_ALIAS: Record<string, string> = {
  arxiv: "research",
  hackernews: "community",
  hn: "community",
  x: "social",
  jobs: "hiring",
  website: "web",
  "hiring/web": "hiring/web",
};

function canonicalFamilies(f: string): string[] {
  const k = f.toLowerCase().trim();
  if (k === "hiring/web") return ["hiring", "web"];
  return [FAMILY_ALIAS[k] ?? k];
}

/** ["research","github"] → ["GitHub","Research"] (canonical order, deduped, unknowns humanized last). */
export function familyWords(families: string[]): string[] {
  const uniq = [...new Set(families.flatMap(canonicalFamilies))];
  uniq.sort((a, b) => {
    const ia = FAMILY_ORDER.indexOf(a);
    const ib = FAMILY_ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
  return uniq.map((f) => FAMILY_WORD[f] ?? f.charAt(0).toUpperCase() + f.slice(1).replace(/_/g, " "));
}

// ── Prose ────────────────────────────────────────────────────────────────────

const SMALL = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"];
const spell = (n: number) => (n >= 0 && n < 10 ? SMALL[n] : n.toLocaleString("en-US"));

/** One-line deck from tier counts. Honest when nothing was detected. */
export function formationDeck(c: FormationCounts): string {
  if (!c.total) return "No formation signals detected in this period.";
  const parts = [
    c.high > 0 && `${spell(c.high)} high-confidence`,
    c.emerging > 0 && `${spell(c.emerging)} emerging`,
    c.early > 0 && `${spell(c.early)} early`,
  ].filter((x): x is string => Boolean(x));
  const list = parts.length <= 1 ? parts.join("") : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
  const lead = c.total === 1 ? "One possible formation" : `${spell(c.total).replace(/^./, (m) => m.toUpperCase())} possible formations`;
  return `${lead} in view: ${list}.`;
}

/** Strip whitespace, drop empties, dedupe case-insensitively (first spelling wins). */
export function cleanAffiliations(list: string[], limit = 4): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of list) {
    const s = raw.trim().replace(/^@/, "");
    const k = s.toLowerCase();
    if (!s || seen.has(k)) continue;
    seen.add(k);
    out.push(s);
  }
  return out.slice(0, limit);
}

/** "3 people" / "1 person". */
export function peopleCount(n: number): string {
  return `${n} ${n === 1 ? "person" : "people"}`;
}

/** Member line parts: count, affiliations (always labelled self-reported in the UI). */
export function memberSummary(card: Pick<FormationCard, "members" | "previous_affiliations">): {
  count: string;
  previously: string[];
} {
  return { count: peopleCount(card.members.length), previously: cleanAffiliations(card.previous_affiliations, 3) };
}

/** Names for the card title line, e.g. "A. Chen, R. Patel and 1 other". */
export function memberNames(members: FormationMember[], max = 3): string {
  const names = members.map((m) => m.name);
  if (names.length <= max) return names.length <= 1 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  const rest = names.length - max;
  return `${names.slice(0, max).join(", ")} and ${rest} ${rest === 1 ? "other" : "others"}`;
}

// ── Relationships ────────────────────────────────────────────────────────────

const VERB: Record<string, string> = {
  COAUTHORED_WITH: "co-authored with",
  CONTRIBUTED_WITH: "contributed alongside",
  WORKED_WITH: "shared an org with",
  MEMBER_OF: "member of",
  FOUNDED: "founded",
  AUTHORED: "authored",
  CONTRIBUTED_TO: "contributed to",
};

/** Relationship kind → plain verb phrase. Only FOUNDED ever says "founded". */
export function relationshipVerb(kind: string): string {
  return VERB[kind] ?? kind.toLowerCase().replace(/_/g, " ");
}

const yearOf = (iso: string | null | undefined): number | null => {
  if (!iso) return null;
  const y = Number(iso.slice(0, 4));
  return Number.isFinite(y) && y > 1900 ? y : null;
};

/** "2023–2025", "2025", or "" when undated. */
export function relationshipYears(r: Pick<RelationshipOut, "first_seen_at" | "last_seen_at">): string {
  const a = yearOf(r.first_seen_at);
  const b = yearOf(r.last_seen_at);
  if (a && b && a !== b) return `${Math.min(a, b)}–${Math.max(a, b)}`;
  return String(a ?? b ?? "");
}

/** Short label of the source behind a relationship's first evidence ("arXiv", "GitHub", …). */
export function relationshipSource(r: Pick<RelationshipOut, "evidence">): { label: string; url: string | null } | null {
  const e = r.evidence.find((x) => x.source_url) ?? r.evidence[0];
  if (!e) return null;
  const t = (e.source_type ?? "").toLowerCase();
  const label =
    t === "arxiv" ? "arXiv" : t === "github" ? "GitHub" : t === "hackernews" ? "Hacker News" : t === "x" ? "X" : t ? t.charAt(0).toUpperCase() + t.slice(1) : "Source";
  return { label, url: e.source_url };
}

/** VERIFIED first, then person–person ties, then most recent. Stable for equal keys. */
export function sortRelationships(rels: RelationshipOut[]): RelationshipOut[] {
  const pp = (r: RelationshipOut) => r.src.type === "person" && r.dst.type === "person";
  return [...rels].sort(
    (a, b) =>
      Number(b.epistemic === "VERIFIED") - Number(a.epistemic === "VERIFIED") ||
      Number(pp(b)) - Number(pp(a)) ||
      (b.last_seen_at ?? "").localeCompare(a.last_seen_at ?? ""),
  );
}

/**
 * Nodes/edges for EntityGraph from the relationship table. WORKED_WITH (inferred
 * from shared self-reported org strings) is drawn as the dashed "prior
 * affiliation" edge; everything else solid. The anchor is always present.
 */
export function relationshipGraph(rels: RelationshipOut[], anchor: EntityRef, members: FormationMember[]): GraphOut {
  const nodes = new Map<string, GraphNode>();
  const add = (ref: EntityRef, score: number | null = null, isDemo = false) => {
    if (!nodes.has(ref.id)) nodes.set(ref.id, { id: ref.id, type: ref.type, label: ref.name, slug: ref.slug, score, is_demo: isDemo });
  };
  add(anchor);
  for (const m of members) add({ type: "person", id: m.id, slug: m.slug, name: m.name }, m.founder_score, m.is_demo);
  const edges: GraphEdge[] = [];
  const seen = new Set<string>();
  for (const r of rels) {
    if (r.src.id === r.dst.id) continue;
    add(r.src);
    add(r.dst);
    const key = [r.src.id, r.dst.id].sort().join("|") + (r.kind === "WORKED_WITH" ? "|p" : "");
    if (seen.has(key)) continue;
    seen.add(key);
    edges.push({ source: r.src.id, target: r.dst.id, kind: r.kind === "WORKED_WITH" ? "prior_affiliation" : r.kind.toLowerCase() });
  }
  return { nodes: [...nodes.values()], edges };
}

// ── Convergence ──────────────────────────────────────────────────────────────

/** Strength from the API, or derived from the 0..1 score when absent. */
export function convergenceStrength(c: { strength?: string | null; score: number }): "HIGH" | "MEDIUM" | "LOW" {
  const s = (c.strength ?? "").toUpperCase();
  if (s === "HIGH" || s === "MEDIUM" || s === "LOW") return s;
  return c.score >= 0.66 ? "HIGH" : c.score >= 0.33 ? "MEDIUM" : "LOW";
}

// ── Score ────────────────────────────────────────────────────────────────────

/**
 * Coerce the /api/formation/{id}/score payload into a full AntiScore for the
 * shared Why drawer. Missing optional fields get neutral values (never invented
 * signal): no deltas, no attention gap, no collision, no feedback adjustment.
 */
export function toAntiScore(
  raw: (Partial<AntiScore> & { total?: number; components?: ScoreComponent[] }) | null | undefined,
  fallback?: { score: number; components: ScoreComponent[] },
): AntiScore | null {
  const total = raw?.total ?? fallback?.score;
  const components = raw?.components ?? fallback?.components;
  if (total === undefined || total === null || !components) return null;
  return {
    total: Math.round(total),
    delta_7d: raw?.delta_7d ?? null,
    delta_30d: raw?.delta_30d ?? null,
    components: components.map((c) => ({ ...c, raw: c.raw ?? {}, evidence: c.evidence ?? [] })),
    attention_gap: raw?.attention_gap ?? null,
    collision: raw?.collision ?? null,
    version: raw?.version ?? "formation",
    computed_on: raw?.computed_on ?? "",
    feedback_adjustment: raw?.feedback_adjustment ?? 0,
    cohort_size: raw?.cohort_size ?? 0,
    low_coverage: raw?.low_coverage ?? false,
  };
}

/** Where the Watch button should point: the anchor if it is a company or person. */
export function watchTarget(anchor: EntityRef): { entityType: "company" | "person"; entityId: string } | null {
  return anchor.type === "company" || anchor.type === "person" ? { entityType: anchor.type, entityId: anchor.id } : null;
}

// ── List URL state ───────────────────────────────────────────────────────────

export interface FormationQuery {
  tier: TierKey | null;
  theme: string | null;
  /** null = API default; false = exclude demo rows. */
  demo: boolean | null;
}

/** Parse /formation search params; unknown tiers are dropped (theme is validated by the API). */
export function parseFormationQuery(sp: Record<string, string | string[] | undefined>): FormationQuery {
  const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v)?.trim() || null;
  const tier = one(sp.tier);
  const demo = one(sp.demo);
  return {
    tier: TIERS.some((t) => t.key === tier) ? (tier as TierKey) : null,
    theme: one(sp.theme)?.slice(0, 120) ?? null,
    demo: demo === "0" ? false : demo === "1" ? true : null,
  };
}

/** Canonical /formation URL for a query state. */
export function formationListHref(q: FormationQuery): string {
  const sp = new URLSearchParams();
  if (q.tier) sp.set("tier", q.tier);
  if (q.theme) sp.set("theme", q.theme);
  if (q.demo !== null) sp.set("demo", q.demo ? "1" : "0");
  const qs = sp.toString();
  return qs ? `/formation?${qs}` : "/formation";
}
