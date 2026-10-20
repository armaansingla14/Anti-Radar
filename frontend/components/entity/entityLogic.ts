/**
 * Display logic for the company / person pages (WHY NOW-first hierarchy):
 * dated evidence, collision strength, recent change, velocity, source-family
 * presence, verified lab contributions, formation evidence and relationships.
 * Pure functions — unit tested in tests/pages.test.mts. Nothing here invents a
 * number: missing inputs come back as null / "insufficient history".
 */
import { ordinal } from "../../lib/attention.ts";
import { type AttentionView, collisionView, epistemicDisplay, FAMILY_LABEL, familyOf } from "../../lib/labels.ts";
import type {
  AntiScore,
  Collision,
  DataAge,
  EvidenceItem,
  FormationCard,
  FormationFeed,
  GraphOut,
  JsonObject,
  RelationshipOut,
  ScoreComponent,
  SourceFamily,
  TimelineEvent,
  VelocityMetric,
} from "../../lib/types.ts";

// ── Source families (vocabulary from lib/labels) ─────────────────────────────

/** The six independent families shown on entity pages (labels' "other" bucket is not a family). */
export const ENTITY_FAMILIES: SourceFamily[] = ["github", "research", "hiring", "web", "community", "social"];

/** Source type → one of the six families, or null for "other". */
export function familyKey(sourceType: string | null | undefined): SourceFamily | null {
  const base = (sourceType ?? "").toLowerCase().split(/[:/]/)[0];
  const f = familyOf(base);
  return f === "other" ? null : f;
}

// ── Collision 2.0 ────────────────────────────────────────────────────────────

export type CollisionLine = ReturnType<typeof collisionView>;

/** Collision line for the page (null when there is no collision at all). */
export function collisionLine(c: Collision | null | undefined): CollisionLine | null {
  if (!c || !c.families?.length) return null;
  return collisionView(c);
}

// ── WHY NOW evidence ─────────────────────────────────────────────────────────

function evidenceKey(e: EvidenceItem): string {
  return e.signal_id ?? `${e.title}|${e.event_at ?? ""}`;
}

/**
 * Dated evidence bullets behind WHY NOW: collision evidence first (independent families), then every score
 * component's evidence; deduplicated, dated items only, newest first.
 */
export function whyNowEvidence(score: Pick<AntiScore, "collision" | "components"> | null | undefined, limit = 4): EvidenceItem[] {
  if (!score) return [];
  const seen = new Set<string>();
  const out: EvidenceItem[] = [];
  const push = (e: EvidenceItem) => {
    if (!e?.title || !e.event_at) return;
    const k = evidenceKey(e);
    const t = `${e.title}|${e.event_at.slice(0, 10)}`;
    if (seen.has(k) || seen.has(t)) return;
    seen.add(k);
    seen.add(t);
    out.push(e);
  };
  score.collision?.evidence.forEach(push);
  score.components?.forEach((c) => c.evidence.forEach(push));
  out.sort((a, b) => (b.event_at ?? "").localeCompare(a.event_at ?? ""));
  // Prefer breadth: make sure each source family is represented before repeating one.
  const byFamily: EvidenceItem[] = [];
  const fams = new Set<string>();
  for (const e of out) {
    const f = familyKey(e.source_type) ?? e.source_type ?? "?";
    if (!fams.has(f)) {
      fams.add(f);
      byFamily.push(e);
    }
  }
  const rest = out.filter((e) => !byFamily.includes(e));
  return [...byFamily, ...rest].slice(0, limit).sort((a, b) => (b.event_at ?? "").localeCompare(a.event_at ?? ""));
}

/** Distinct source families across a set of evidence items. */
export function evidenceFamilies(items: EvidenceItem[]): SourceFamily[] {
  const set = new Set<SourceFamily>();
  for (const e of items) {
    const f = familyKey(e.source_type);
    if (f) set.add(f);
  }
  return ENTITY_FAMILIES.filter((f) => set.has(f));
}

// ── Recent change ────────────────────────────────────────────────────────────

/** Timeline events within `days` of `asOf` (calendar days), newest first. */
export function recentChanges(timeline: TimelineEvent[], asOf: Date, days = 30, limit = 6): TimelineEvent[] {
  const cutoff = new Date(asOf.getTime() - days * 86_400_000).toISOString().slice(0, 10);
  return timeline
    .filter((e) => e.day >= cutoff)
    .sort((a, b) => b.day.localeCompare(a.day))
    .slice(0, limit);
}

const VELOCITY_PRIORITY = [
  "star_acceleration",
  "commit_acceleration",
  "star_velocity_30d",
  "commit_velocity_30d",
  "contributor_velocity_30d",
  "release_velocity_90d",
  "issue_velocity_30d",
];

/** Velocity lines worth reading, most informative first; insufficient-history lines are kept (honest state). */
export function velocityHighlights(velocity: VelocityMetric[] | null | undefined, limit = 5): VelocityMetric[] {
  if (!velocity?.length) return [];
  const rank = (k: string) => {
    const i = VELOCITY_PRIORITY.indexOf(k);
    return i < 0 ? VELOCITY_PRIORITY.length : i;
  };
  const sorted = [...velocity].sort((a, b) => rank(a.key) - rank(b.key));
  // Acceleration already says "12 → 61 stars/day"; drop the matching plain 7d/prev-7d lines.
  const shown = sorted.filter((v) => !/_velocity_(7d|prev_7d)$/.test(v.key));
  return shown.slice(0, limit);
}

export function velocityBasisLabel(v: Pick<VelocityMetric, "basis">): string {
  return v.basis === "observed" ? "Observed" : v.basis === "backfill" ? "Reconstructed" : "Insufficient history";
}

// ── Source-family presence / data age ───────────────────────────────────────

export type FamilyState = "observed" | "silent" | "unavailable";

export interface FamilyPresence {
  key: SourceFamily;
  label: string;
  state: FamilyState;
  /** Latest observation for this family (data age) — null when never observed. */
  lastObserved: string | null;
  /** Evidence items from this family inside the WHY NOW / collision window. */
  inWindow: number;
  note: string | null;
}

/**
 * All six families with an honest state: observed (with date and in-window evidence count),
 * silent (collector exists, nothing observed for this entity) or unavailable (no collector access).
 */
export function familyPresence(
  dataAge: DataAge | null | undefined,
  evidence: EvidenceItem[],
  opts: { socialAvailable?: boolean; hiringAvailable?: boolean } = {},
): FamilyPresence[] {
  const counts = new Map<SourceFamily, number>();
  for (const e of evidence) {
    const f = familyKey(e.source_type);
    if (f) counts.set(f, (counts.get(f) ?? 0) + 1);
  }
  return ENTITY_FAMILIES.map((key) => {
    const last = dataAge?.families?.[key] ?? null;
    const inWindow = counts.get(key) ?? 0;
    const unavailable = (key === "social" && !opts.socialAvailable) || (key === "hiring" && opts.hiringAvailable === false);
    const state: FamilyState = last || inWindow ? "observed" : unavailable ? "unavailable" : "silent";
    const note =
      state === "unavailable"
        ? key === "social"
          ? "X unavailable — API access not configured"
          : "No collector configured"
        : state === "silent"
          ? "No observation for this entity"
          : null;
    return { key, label: FAMILY_LABEL[key], state, lastObserved: last, inWindow, note };
  });
}

// ── Attention Gap 2.0 ────────────────────────────────────────────────────────

/** Fallback explanation when the backend did not send one (never claims a gap for unmeasured attention). */
export function attentionExplanation(v: Pick<AttentionView, "technical" | "attention" | "gap" | "explanation">): string {
  if (v.explanation) return v.explanation;
  const t = Math.round(v.technical);
  if (v.attention === null) return `Technical momentum at the ${ordinal(t)} percentile; public attention was not measured, so no gap is claimed.`;
  if (v.gap !== null && v.gap > 0) return `Technical momentum at the ${ordinal(t)} percentile vs public attention at the ${ordinal(Math.round(v.attention))}.`;
  return "Public attention is at or ahead of technical momentum.";
}

// ── Score components (raw) ───────────────────────────────────────────────────

export function component(score: Pick<AntiScore, "components"> | null | undefined, key: string): ScoreComponent | null {
  return score?.components.find((c) => c.key === key) ?? null;
}

export interface LabContribution {
  fullName: string;
  org: string;
  label: string;
  commits: number | null;
  tier: number | null;
  firstCommitAt: string | null;
  lastCommitAt: string | null;
  url: string;
}

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** Verified lab contributions recorded by the Founder Score pedigree component, most commits first. */
export function labContributions(score: Pick<AntiScore, "components"> | null | undefined): LabContribution[] {
  const raw = component(score, "pedigree")?.raw?.lab_contributions;
  if (!Array.isArray(raw)) return [];
  return raw
    .map((x: JsonObject) => {
      const full = str(x?.full_name) ?? (str(x?.org) && str(x?.repo) ? `${x.org}/${x.repo}` : null);
      if (!full || !/^[\w.-]+\/[\w.-]+$/.test(full)) return null;
      return {
        fullName: full,
        org: str(x.org) ?? full.split("/")[0],
        label: str(x.label) ?? full.split("/")[0],
        commits: num(x.commits),
        tier: num(x.tier),
        firstCommitAt: str(x.first_commit_at),
        lastCommitAt: str(x.last_commit_at),
        url: `https://github.com/${full}`,
      } satisfies LabContribution;
    })
    .filter((x): x is LabContribution => x !== null)
    .sort((a, b) => (b.commits ?? 0) - (a.commits ?? 0));
}

/** "149 commits to google-deepmind/mujoco_menagerie" (no count → "Contributed to …"). */
export function labContributionText(c: Pick<LabContribution, "commits" | "fullName">): string {
  if (c.commits === null) return `Contributed to ${c.fullName}`;
  return `${c.commits.toLocaleString("en-US")} ${c.commits === 1 ? "commit" : "commits"} to ${c.fullName}`;
}

export interface FormationEvidence {
  newProjects: string[];
  youngOrgs: string[];
  joinedYoungOrg: boolean;
  recentTransition: boolean;
  evidence: EvidenceItem[];
  value: number | null;
  max: number | null;
}

/** The Founder Score formation component, unpacked (new_project / joined_young_org evidence). */
export function formationEvidence(score: Pick<AntiScore, "components"> | null | undefined): FormationEvidence {
  const c = component(score, "formation");
  const raw = (c?.raw ?? {}) as JsonObject;
  const list = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && !!x) : []);
  return {
    newProjects: list(raw.new_projects),
    youngOrgs: list(raw.young_orgs),
    joinedYoungOrg: raw.joined_young_org === true,
    recentTransition: raw.recent_transition === true,
    evidence: c?.evidence ?? [],
    value: c ? c.value : null,
    max: c ? c.max : null,
  };
}

// ── Formation candidates ─────────────────────────────────────────────────────

/** Formation candidates that include this entity (as anchor or member), strongest first. */
export function formationsFor(feed: Pick<FormationFeed, "high" | "emerging" | "early"> | null | undefined, entityId: string): FormationCard[] {
  if (!feed) return [];
  return [...feed.high, ...feed.emerging, ...feed.early]
    .filter((c) => c.anchor?.id === entityId || c.members.some((m) => m.id === entityId))
    .sort((a, b) => b.score - a.score);
}

// ── Relationships ────────────────────────────────────────────────────────────

const EDGE_TO_KIND: Record<string, string> = {
  founder: "FOUNDED",
  member: "MEMBER_OF",
  contributor: "CONTRIBUTED_TO",
  author: "AUTHORED",
  worked_with: "WORKED_WITH",
  coauthored_with: "COAUTHORED_WITH",
  contributed_with: "CONTRIBUTED_WITH",
};

/**
 * Direct relationships of `focalId` from the founder graph. Graph edges carry no provenance, so
 * WORKED_WITH (usually derived from shared self-reported org strings) is conservatively labelled
 * INFERRED; co-authorship, co-contribution, membership and authorship come from primary records
 * (arXiv author lists, GitHub contributor/member lists) and are VERIFIED.
 */
export function relationshipsFromGraph(graph: GraphOut | null | undefined, focalId: string): RelationshipOut[] {
  if (!graph) return [];
  const nodes = new Map(graph.nodes.map((n) => [n.id, n]));
  const out: RelationshipOut[] = [];
  const seen = new Set<string>();
  for (const e of graph.edges) {
    if (e.source !== focalId && e.target !== focalId) continue;
    const kind = EDGE_TO_KIND[e.kind];
    if (!kind) continue;
    const a = nodes.get(e.source);
    const b = nodes.get(e.target);
    if (!a || !b) continue;
    const other = e.source === focalId ? b : a;
    const k = `${kind}|${other.id}`;
    if (seen.has(k)) continue;
    seen.add(k);
    if (isAutomationName(other.label)) continue;
    const ref = (n: typeof a) => ({ type: n.type, id: n.id, slug: n.slug, name: n.label });
    out.push({
      id: `${e.source}-${e.target}-${e.kind}`,
      kind,
      epistemic: kind === "WORKED_WITH" ? "INFERRED" : "VERIFIED",
      src: ref(a),
      dst: ref(b),
      first_seen_at: null,
      last_seen_at: null,
      evidence: [],
    });
  }
  return out;
}

// ── Labels ───────────────────────────────────────────────────────────────────

/** FACT / INFERRED / ANALYSIS for a narrative — anything a model wrote is ANALYSIS, never fact. */
export function narrativeLabel(n: { generator?: string | null; epistemic?: string | null } | null | undefined): "FACT" | "INFERRED" | "ANALYSIS" {
  if (!n) return "INFERRED";
  if (n.generator === "llm" || n.generator === "cached") return "ANALYSIS";
  return epistemicDisplay(n.epistemic).text;
}

/** First sentence of a narrative (for a one-line WHY NOW lead). */
export function firstSentence(text: string | null | undefined): string | null {
  const t = (text ?? "").trim();
  if (!t) return null;
  const m = /^(.+?[.!?])(\s|$)/.exec(t);
  return m ? m[1] : t;
}

// ── X / social ───────────────────────────────────────────────────────────────

export type XMode = "official" | "manual" | "disabled" | "unknown";

/** Provider mode from the status row for "x" (state + reason). */
export function xModeFromStatus(row: { state?: string | null; reason?: string | null } | null | undefined): XMode {
  if (!row || !row.state) return "unknown";
  if (row.state === "disabled") return "disabled";
  if (row.state === "never_run" && /not configured|X_BEARER_TOKEN/i.test(row.reason ?? "")) return "disabled";
  if (row.state === "healthy" || row.state === "degraded" || row.state === "error") return "official";
  return "manual";
}

export function xModeCopy(mode: XMode, reason?: string | null): { headline: string; body: string; line: string } {
  switch (mode) {
    case "official":
      return {
        headline: "● X intelligence · official API active",
        body: "Recent-search discovery runs on schedule within the API's rate limits. Pasted posts below are still labelled user-provided.",
        line: "X intelligence: official API active",
      };
    case "disabled":
      return {
        headline: "○ X intelligence — API access not configured",
        body:
          "No X API token is set, so the radar does not read X on its own and no follower or post counts are shown anywhere. You can still record a specific public post by URL." +
          (reason && !/not configured/i.test(reason) ? ` (${reason})` : ""),
        line: "X intelligence unavailable — API access not configured",
      };
    case "manual":
      return {
        headline: "◇ X intelligence — manual posts only",
        body: "Only posts pasted here are recorded. Nothing is collected from X automatically.",
        line: "X intelligence: manual posts only",
      };
    default:
      return {
        headline: "X intelligence — status unavailable",
        body: "The provider status could not be read. Posts can still be recorded by URL.",
        line: "X intelligence status unavailable",
      };
  }
}

/** x.com / twitter.com status URL. */
export function isXPostUrl(raw: string): boolean {
  return /^https?:\/\/(www\.|mobile\.)?(x|twitter)\.com\/[A-Za-z0-9_]{1,15}\/status\/\d+/i.test(raw.trim());
}

/** Accounts that are automation, not people (kept out of relationship lists). */
export function isAutomationName(name: string): boolean {
  return /\[bot\]$|(^|[\s_-])bot$|^copybara|^dependabot|^github-actions|^renovate/i.test(name.trim());
}

// ── Section numbering ────────────────────────────────────────────────────────

/**
 * Section numbers in display order (falsy entries = sections not rendered on this page), so the
 * numbered labels and the table of contents follow reading order, not definition order.
 */
export function sectionNumbers(order: (string | false | null | undefined)[]): Map<string, number> {
  const ids = order.filter((x): x is string => typeof x === "string" && x.length > 0);
  return new Map(ids.map((id, i) => [id, i + 1]));
}
