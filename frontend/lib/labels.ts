/**
 * Display vocabulary for trust: epistemic labels, confidence, source families
 * and feedback. Pure (relative imports only) so tests can run it under Node.
 *
 * The backend keeps its enum names (FACT / INFERENCE / MODEL_OPINION); the UI
 * shows FACT / INFERRED / ANALYSIS. Model-generated text is always ANALYSIS,
 * never presented as fact.
 */
import type { AttentionGap, DataAge, EvidenceItem, Epistemic, FeedbackLabel } from "./types.ts";

// ── Epistemic ────────────────────────────────────────────────────────────────

export interface EpistemicDisplay {
  /** Uppercase display word. */
  text: "FACT" | "INFERRED" | "ANALYSIS";
  glyph: string;
  title: string;
}

export const EPISTEMIC_DISPLAY: Record<Epistemic, EpistemicDisplay> = {
  FACT: { text: "FACT", glyph: "●", title: "Fact — directly observed in a cited, verified source" },
  INFERENCE: { text: "INFERRED", glyph: "◆", title: "Inferred — derived deterministically from observed facts" },
  MODEL_OPINION: {
    text: "ANALYSIS",
    glyph: "○",
    title: "Analysis — written by a language model from the cited evidence; not a fact, verify before relying on it",
  },
};

/** Display entry for an epistemic value; unknown strings fall back to ANALYSIS (the least trusted). */
export function epistemicDisplay(value: string | null | undefined): EpistemicDisplay {
  return EPISTEMIC_DISPLAY[(value ?? "") as Epistemic] ?? EPISTEMIC_DISPLAY.MODEL_OPINION;
}

// ── Confidence ───────────────────────────────────────────────────────────────

export type ConfidenceLevel = "HIGH CONFIDENCE" | "MEDIUM CONFIDENCE" | "EARLY SIGNAL";

export const CONFIDENCE_TITLE: Record<ConfidenceLevel, string> = {
  "HIGH CONFIDENCE": "Several independent source families, dated and recent",
  "MEDIUM CONFIDENCE": "More than one source, corroboration still building",
  "EARLY SIGNAL": "A first signal from limited evidence — watch, do not conclude",
};

/** Normalize backend confidence strings ("HIGH", "high confidence", "EARLY") → display level; null when absent. */
export function confidenceLevel(value: string | null | undefined): ConfidenceLevel | null {
  const v = (value ?? "").trim().toUpperCase();
  if (!v) return null;
  if (v.startsWith("HIGH")) return "HIGH CONFIDENCE";
  if (v.startsWith("MED")) return "MEDIUM CONFIDENCE";
  if (v.startsWith("EARLY") || v.startsWith("LOW")) return "EARLY SIGNAL";
  return null;
}

// ── Source families ──────────────────────────────────────────────────────────

export type FamilyKey = "github" | "research" | "hiring" | "web" | "community" | "social" | "other";

export const FAMILY_LABEL: Record<FamilyKey, string> = {
  github: "GitHub",
  research: "Research",
  hiring: "Hiring",
  web: "Web",
  community: "Community",
  social: "Social",
  other: "Other",
};

export const FAMILY_ORDER: FamilyKey[] = ["github", "research", "hiring", "web", "community", "social", "other"];

const SOURCE_FAMILY: Record<string, FamilyKey> = {
  github: "github",
  arxiv: "research",
  research: "research",
  paper: "research",
  semantic_scholar: "research",
  jobs: "hiring",
  hiring: "hiring",
  ashby: "hiring",
  greenhouse: "hiring",
  lever: "hiring",
  website: "web",
  web: "web",
  hackernews: "community",
  hn: "community",
  community: "community",
  x: "social",
  social: "social",
  twitter: "social",
};

/** Source type ("arxiv", "hackernews", "x") → independent family. */
export function familyOf(sourceType: string | null | undefined): FamilyKey {
  const k = (sourceType ?? "").toLowerCase().trim();
  return SOURCE_FAMILY[k] ?? "other";
}

/** Family keys or source types (["arxiv","github","hiring/web"]) → display words in canonical order, deduped. */
export function familyLabels(families: string[]): string[] {
  const keys = new Set<FamilyKey>();
  for (const f of families) for (const part of f.split("/")) keys.add(familyOf(part));
  return FAMILY_ORDER.filter((k) => keys.has(k)).map((k) => FAMILY_LABEL[k]);
}

export type StrengthWord = "HIGH" | "MEDIUM" | "LOW";

/** Collision display: backend strength/labels/sentence when present, honest fallbacks otherwise (no invented strength). */
export function collisionView(c: {
  families: string[];
  summary: string;
  strength?: string | null;
  family_labels?: string[] | null;
  sentence?: string | null;
}): { strength: StrengthWord | null; families: string[]; sentence: string } {
  const s = (c.strength ?? "").toUpperCase();
  return {
    strength: s === "HIGH" || s === "MEDIUM" || s === "LOW" ? s : null,
    families: c.family_labels && c.family_labels.length > 0 ? c.family_labels : familyLabels(c.families),
    sentence: c.sentence || c.summary,
  };
}

/** A collision worth showing: ≥ 2 independent families, or a backend strength above LOW. */
export function isCollision(c: { families: string[]; strength?: string | null } | null | undefined): boolean {
  if (!c) return false;
  const s = (c.strength ?? "").toUpperCase();
  return c.families.length >= 2 || s === "HIGH" || s === "MEDIUM";
}

/**
 * Copy for the "independent signals" slot when there is no collision to draw.
 * A stored single-family collision reads "Single source family"; an entity with
 * no stored collision at all gets null (callers render nothing / a neutral note)
 * rather than a claim about its source families.
 */
export function collisionFallback(c: { families: string[]; strength?: string | null } | null | undefined): string | null {
  if (isCollision(c)) return null;
  if (!c || c.families.length === 0) return null;
  return "Single source family";
}

/** Distinct sources behind a set of evidence (same URL counts once; unlinked items count by title). */
export function countSources(items: Pick<EvidenceItem, "source_url" | "title">[]): number {
  return new Set(items.map((e) => e.source_url || `t:${e.title}`)).size;
}

export interface FamilyGroup {
  key: FamilyKey;
  label: string;
  items: EvidenceItem[];
}

/**
 * Evidence grouped by family (canonical order), each group newest-first,
 * duplicates (same URL + title) dropped.
 */
export function groupByFamily(items: EvidenceItem[]): FamilyGroup[] {
  const seen = new Set<string>();
  const groups = new Map<FamilyKey, EvidenceItem[]>();
  for (const e of items) {
    const id = `${e.source_url ?? ""}|${e.title}`;
    if (seen.has(id)) continue;
    seen.add(id);
    const k = familyOf(e.source_type);
    const list = groups.get(k) ?? [];
    list.push(e);
    groups.set(k, list);
  }
  const t = (e: EvidenceItem) => (e.event_at ? Date.parse(e.event_at) || 0 : 0);
  return FAMILY_ORDER.filter((k) => groups.has(k)).map((k) => ({
    key: k,
    label: FAMILY_LABEL[k],
    items: [...groups.get(k)!].sort((a, b) => t(b) - t(a)),
  }));
}

/** "1 SOURCE" / "3 SOURCES". */
export function sourcesLabel(n: number): string {
  return `${n} ${n === 1 ? "source" : "sources"}`;
}

// ── Attention gap ────────────────────────────────────────────────────────────

export interface AttentionView {
  technical: number;
  /** null = unmeasured, never 0. */
  attention: number | null;
  /** null when attention is unmeasured — no gap is claimed. */
  gap: number | null;
  explanation: string | null;
  socialAvailable: boolean;
  unavailable: string[];
}

const SOCIAL_KEY = /^(x|x_|twitter|social)/i;

/** Attention Gap 2.0 fields with fallbacks for older backends. */
export function attentionView(g: AttentionGap): AttentionView {
  const technical = g.technical_score ?? g.technical;
  const rawAttention = g.attention_score !== undefined ? g.attention_score : g.attention;
  const attention = rawAttention !== null && rawAttention !== undefined && Number.isFinite(rawAttention) ? rawAttention : null;
  // The displayed gap is the percentile difference (technical − attention), never `gap_points` (the 0–10
  // ANTI SCORE component), and it is never claimed when attention was not measured.
  const gap = attention === null ? null : g.gap;
  return {
    technical,
    attention,
    gap: gap === null || gap === undefined || !Number.isFinite(gap) ? null : gap,
    explanation: g.explanation?.trim() || null,
    socialAvailable: g.social_available ?? g.sources.some((s) => SOCIAL_KEY.test(s.key) && s.available),
    unavailable: g.unavailable ?? g.sources.filter((s) => !s.available).map((s) => s.label),
  };
}

// ── Data age ─────────────────────────────────────────────────────────────────

/** Data-age families with the source word an analyst recognizes. */
export const DATA_AGE_FAMILIES: { key: string; label: string }[] = [
  { key: "github", label: "GitHub" },
  { key: "research", label: "Research" },
  { key: "hiring", label: "Hiring" },
  { key: "web", label: "Web" },
  { key: "community", label: "HN" },
  { key: "social", label: "X" },
];

/** Source families that can describe a person (job postings and company web pages cannot). */
export const PERSON_DATA_AGE_FAMILIES: ReadonlySet<string> = new Set(["github", "research", "community", "social"]);

/** Compact elapsed time: "just now", "8 min ago", "19 h ago", "1 d ago"; null when unparseable or in the future by > 5 min. */
export function ageShort(at: string | null | undefined, now: Date): string | null {
  if (!at) return null;
  const t = Date.parse(/^\d{4}-\d{2}-\d{2}$/.test(at) ? `${at}T00:00:00Z` : at);
  if (Number.isNaN(t)) return null;
  const s = (now.getTime() - t) / 1000;
  if (s < -300) return null;
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.floor(h / 24)} d ago`;
}

export interface DataAgePart {
  label: string;
  /** "8 min ago" | "unavailable" */
  value: string;
  available: boolean;
}

/**
 * Per-family age parts in canonical order. Families the backend reports as
 * never observed (null) read "unavailable"; X/social is always listed so its
 * absence is visible rather than silent.
 */
export function dataAgeParts(
  age: DataAge | null | undefined,
  now: Date,
  entityType: "company" | "person" = "company",
): DataAgePart[] {
  const fams = age?.families ?? {};
  const keys = Object.keys(fams);
  if (keys.length === 0) return [];
  const parts: DataAgePart[] = [];
  for (const f of DATA_AGE_FAMILIES) {
    if (entityType === "person" && !PERSON_DATA_AGE_FAMILIES.has(f.key)) continue;
    const present = f.key in fams;
    if (!present && f.key !== "social") continue;
    const at = (fams as Record<string, string | null | undefined>)[f.key];
    const v = ageShort(at, now);
    parts.push({ label: f.label, value: v ?? "unavailable", available: v !== null });
  }
  return parts;
}

/** "Updated 12 minutes ago" — header phrasing for live data. */
export function updatedAgo(at: string | null | undefined, now: Date): string {
  if (!at) return "Update time unknown";
  const t = Date.parse(at);
  if (Number.isNaN(t)) return "Update time unknown";
  const m = Math.max(0, Math.floor((now.getTime() - t) / 60000));
  if (m < 1) return "Updated just now";
  if (m < 60) return `Updated ${m} ${m === 1 ? "minute" : "minutes"} ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `Updated ${h} ${h === 1 ? "hour" : "hours"} ago`;
  const d = Math.floor(h / 24);
  return `Updated ${d} days ago`;
}

// ── Feedback ─────────────────────────────────────────────────────────────────

export interface FeedbackOption {
  label: FeedbackLabel;
  text: string;
}

export interface FeedbackGroup {
  key: "interesting" | "not_interesting";
  head: FeedbackOption;
  reasons: FeedbackOption[];
}

export const FEEDBACK_GROUPS: FeedbackGroup[] = [
  {
    key: "interesting",
    head: { label: "interesting", text: "Interesting" },
    reasons: [
      { label: "strong_founder", text: "Strong founder" },
      { label: "interesting_shift", text: "Interesting technical shift" },
    ],
  },
  {
    key: "not_interesting",
    head: { label: "not_interesting", text: "Not interesting" },
    reasons: [
      { label: "already_known", text: "Already knew this" },
      { label: "too_obvious", text: "Too obvious" },
      { label: "wrong_thesis", text: "Wrong thesis" },
      { label: "weak_team", text: "Weak team" },
      { label: "bad_data", text: "Bad data" },
      { label: "too_early", text: "Too early" },
    ],
  },
];

/** Every selectable option, in menu order. */
export const FEEDBACK_ITEMS: FeedbackOption[] = FEEDBACK_GROUPS.flatMap((g) => [g.head, ...g.reasons]);

/** Text for a recorded label (legacy labels included). */
export function feedbackText(label: FeedbackLabel | null | undefined): string | null {
  if (!label) return null;
  if (label === "useful") return "Interesting";
  if (label === "dismiss") return "Dismissed";
  return FEEDBACK_ITEMS.find((o) => o.label === label)?.text ?? null;
}

/** Which side a label falls on. */
export function feedbackPolarity(label: FeedbackLabel): "positive" | "negative" | "neutral" {
  if (label === "useful" || FEEDBACK_GROUPS[0].head.label === label || FEEDBACK_GROUPS[0].reasons.some((r) => r.label === label))
    return "positive";
  if (label === "dismiss") return "neutral";
  return "negative";
}

const LEGACY_FEEDBACK: Partial<Record<FeedbackLabel, FeedbackLabel>> = {
  interesting: "useful",
  strong_founder: "useful",
  interesting_shift: "useful",
  weak_team: "not_interesting",
};

/**
 * The closest label an older backend (pre-v2 FeedbackRequest) accepts, or null
 * when the label is already a legacy one. Used to retry once on 422.
 */
export function legacyFeedbackLabel(label: FeedbackLabel): FeedbackLabel | null {
  return LEGACY_FEEDBACK[label] ?? null;
}

// ── Provider health (shared by /status and the footer) ──────────────────────

export interface ProviderHealth {
  healthy: number;
  /** Providers that are switched on (everything except "disabled"). */
  live: number;
  disabled: number;
}

/** Healthy / live / disabled counts — one definition for the status page and the footer. */
export function providerHealth(providers: { state: string }[]): ProviderHealth {
  const disabled = providers.filter((p) => p.state === "disabled").length;
  return {
    healthy: providers.filter((p) => p.state === "healthy").length,
    live: providers.length - disabled,
    disabled,
  };
}

/** "3/5 live providers healthy · 1 disabled". */
export function providerHealthLine(providers: { state: string }[]): string {
  const h = providerHealth(providers);
  return `${h.healthy}/${h.live} live providers healthy${h.disabled ? ` · ${h.disabled} disabled` : ""}`;
}
