/**
 * "Who should we meet this week" display logic — pure functions shared by the
 * /meet page, the founder profile and the meeting-brief reader (unit tested).
 */
import type { JsonObject, JsonValue, MeetCard, MeetFeed } from "../../lib/types.ts";

// ── ISO weeks ────────────────────────────────────────────────────────────────

const ISO_WEEK = /^(\d{4})-W(\d{2})$/;

export function isIsoWeek(value: string | null | undefined): value is string {
  const m = value ? ISO_WEEK.exec(value) : null;
  if (!m) return false;
  const w = Number(m[2]);
  return w >= 1 && w <= weeksInYear(Number(m[1]));
}

/** ISO years have 52 or 53 weeks: 53 when Dec 28 falls in week 53. */
function weeksInYear(year: number): number {
  const dec28 = new Date(Date.UTC(year, 11, 28));
  return isoWeekOf(dec28).week;
}

function isoWeekOf(d: Date): { year: number; week: number } {
  const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  const day = t.getUTCDay() || 7;
  t.setUTCDate(t.getUTCDate() + 4 - day);
  const yearStart = Date.UTC(t.getUTCFullYear(), 0, 1);
  return { year: t.getUTCFullYear(), week: Math.ceil(((t.getTime() - yearStart) / 86400000 + 1) / 7) };
}

/** ISO week ("YYYY-Www") of an instant as seen in a time zone (default America/New_York, like the backend). */
export function isoWeekFor(at: Date, timeZone = "America/New_York"): string {
  const ymd = new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(at);
  const { year, week } = isoWeekOf(new Date(`${ymd}T00:00:00Z`));
  return `${year}-W${String(week).padStart(2, "0")}`;
}

/** Monday (UTC midnight) of an ISO week, or null for a malformed value. */
export function weekMonday(week: string): Date | null {
  if (!isIsoWeek(week)) return null;
  const [y, w] = week.split("-W").map(Number);
  const jan4 = new Date(Date.UTC(y, 0, 4));
  const monday = new Date(jan4);
  monday.setUTCDate(jan4.getUTCDate() - ((jan4.getUTCDay() || 7) - 1) + (w - 1) * 7);
  return monday;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Oct 19 – 25, 2026" / "Dec 28, 2026 – Jan 3, 2027" for an ISO week. */
export function weekRange(week: string): string | null {
  const mon = weekMonday(week);
  if (!mon) return null;
  const sun = new Date(mon);
  sun.setUTCDate(mon.getUTCDate() + 6);
  const md = (d: Date) => `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
  if (mon.getUTCFullYear() !== sun.getUTCFullYear()) return `${md(mon)}, ${mon.getUTCFullYear()} – ${md(sun)}, ${sun.getUTCFullYear()}`;
  if (mon.getUTCMonth() !== sun.getUTCMonth()) return `${md(mon)} – ${md(sun)}, ${sun.getUTCFullYear()}`;
  return `${md(mon)} – ${sun.getUTCDate()}, ${sun.getUTCFullYear()}`;
}

/** "Week 43" for "2026-W43". */
export function weekShort(week: string): string {
  const m = ISO_WEEK.exec(week);
  return m ? `Week ${Number(m[2])}` : week;
}

/** `?week=` → API param: a valid ISO week, else "current". */
export function parseWeekParam(raw: string | string[] | undefined): string {
  const v = Array.isArray(raw) ? raw[0] : raw;
  return v && isIsoWeek(v) ? v : "current";
}

export interface WeekOption {
  week: string;
  href: string;
  label: string;
  current: boolean;
  selected: boolean;
}

/**
 * Week selector options: this week first, then stored history (newest first),
 * deduplicated. `currentWeek` is the week "current" resolves to, when known.
 */
export function weekOptions(feed: Pick<MeetFeed, "week" | "history_weeks">, requested: string, currentWeek?: string | null): WeekOption[] {
  const current = currentWeek ?? (requested === "current" ? feed.week : null);
  const weeks = [current, feed.week, ...feed.history_weeks].filter((w): w is string => !!w && isIsoWeek(w));
  const unique = Array.from(new Set(weeks)).sort((a, b) => (a === current ? -1 : b === current ? 1 : b.localeCompare(a)));
  return unique.map((week) => ({
    week,
    href: week === current ? "/meet" : `/meet?week=${week}`,
    label: week === current ? "This week" : weekShort(week),
    current: week === current,
    selected: week === feed.week,
  }));
}

// ── Cards ────────────────────────────────────────────────────────────────────

/** Badge tone for priority / confidence. */
export function priorityTone(priority: string): "accent" | "ink" | "muted" {
  const p = priority.toUpperCase();
  return p === "HIGH" ? "accent" : p === "MEDIUM" ? "ink" : "muted";
}

export function confidenceTone(confidence: string): "ink" | "muted" | "dotted" {
  const c = confidence.toUpperCase();
  return c.startsWith("HIGH") ? "ink" : c.startsWith("MEDIUM") ? "muted" : "dotted";
}

/**
 * The affiliation line with its provenance: profile-derived affiliations are
 * self-reported unless the backend already says how it knows.
 */
export function affiliationParts(line: string | null | undefined): { text: string; note: string | null } | null {
  const text = (line ?? "").trim();
  if (!text) return null;
  if (/unknown|self[- ]reported|verified|inferred/i.test(text)) return { text, note: null };
  return { text, note: "self-reported" };
}

/** Why-now evidence newest first; undated items last (stable otherwise). */
export function sortEvidence<T extends { event_at: string | null }>(items: T[]): T[] {
  const t = (e: T) => (e.event_at ? Date.parse(e.event_at) : Number.NEGATIVE_INFINITY);
  return items
    .map((e, i) => ({ e, i }))
    .sort((a, b) => t(b.e) - t(a.e) || a.i - b.i)
    .map((x) => x.e);
}

/** Short thesis labels render as chips; sentence-length explanations as lines. */
export function isChip(label: string): boolean {
  return label.length <= 32 && !/[.;:(]/.test(label);
}

export type BriefAction =
  | { kind: "open"; href: string }
  | { kind: "generate" }
  | { kind: "unlock"; href: "/unlock" };

/** Primary meeting-brief action: open a cached brief, generate one, or unlock (read-only). */
export function briefAction(card: Pick<MeetCard, "brief_report_id">, readOnly: boolean): BriefAction {
  if (card.brief_report_id) return { kind: "open", href: `/reports/${encodeURIComponent(card.brief_report_id)}` };
  return readOnly ? { kind: "unlock", href: "/unlock" } : { kind: "generate" };
}

/** Toast copy for a failed brief request. */
export function briefErrorText(status: number | null, detail?: string | null): string {
  if (status === 403 || detail === "read_only") return "Read-only — unlock to generate meeting briefs";
  if (status === 429) return "Rate limited — try again in a minute";
  if (status === 404) return "Person not found — the radar may have been refreshed";
  if (status === 503) return "Backend is waking up — try again shortly";
  return "Could not generate the meeting brief";
}

export function formationHref(f: { slug: string | null; id: string }): string {
  return `/formation/${encodeURIComponent(f.slug || f.id)}`;
}

const WORDS = ["No one", "One person", "Two people", "Three people", "Four people", "Five people", "Six people", "Seven people", "Eight people", "Nine people", "Ten people"];

/** Page deck: the standard line for a full week; honest counts otherwise. */
export function meetDeck(n: number): string {
  if (n <= 0) return "No one stands out this week — the radar needs more evidence.";
  const who = WORDS[n] ?? `${n} people`;
  return `${who} whose public technical activity changed recently — ranked by evidence, not by fame.`;
}

// ── Meeting brief body ───────────────────────────────────────────────────────

export interface MeetingSectionDef {
  key: string;
  title: string;
  /** "claims" render with citations + epistemic markers; "list" are plain strings. */
  type: "claims" | "list";
  numbered?: boolean;
  empty: string;
}

export const MEETING_BRIEF_SECTIONS: MeetingSectionDef[] = [
  { key: "context_30s", title: "30-second context", type: "claims", empty: "No context could be established from evidence." },
  { key: "why_on_radar", title: "Why on radar", type: "claims", empty: "No radar evidence recorded." },
  { key: "what_they_built", title: "What they have built", type: "claims", empty: "No public work attributed to this person yet." },
  { key: "technical_background", title: "Technical background", type: "claims", empty: "No technical background established from sources." },
  { key: "what_changed", title: "What changed recently", type: "claims", empty: "No dated recent change recorded." },
  { key: "why_anti_fund", title: "Why Anti Fund could be relevant", type: "claims", empty: "No thesis link established." },
  { key: "questions", title: "5 questions worth asking", type: "list", numbered: true, empty: "No questions drafted." },
  { key: "things_to_verify", title: "Things to verify", type: "list", empty: "Nothing flagged for verification." },
  { key: "unknowns", title: "Unknowns", type: "list", empty: "None listed." },
];

const MEETING_KEYS = new Set(["context_30s", "why_on_radar", "what_they_built", "technical_background", "why_anti_fund", "questions", "things_to_verify"]);

/** True for a meeting-brief report (by kind, or by a body that carries its sections). */
export function isMeetingBrief(kind: string | null | undefined, body: JsonObject | null | undefined): boolean {
  if (kind === "meeting_brief") return true;
  if (!body || typeof body !== "object") return false;
  return Object.keys(body).filter((k) => MEETING_KEYS.has(k)).length >= 3;
}

/** Plain strings out of a list section (accepts claim objects defensively). */
export function toStrings(v: JsonValue): string[] {
  if (v === null || v === undefined) return [];
  const arr = Array.isArray(v) ? v : [v];
  return arr
    .map((x) => (typeof x === "string" ? x : x && typeof x === "object" && typeof (x as JsonObject).text === "string" ? (x as JsonObject).text : null))
    .filter((x: string | null): x is string => !!x && !!x.trim());
}

/** Source ids cited anywhere in the brief (for "N cited / M registered" honesty). */
export function citedSourceIds(body: JsonObject | null | undefined): string[] {
  const out = new Set<string>();
  if (!body) return [];
  for (const def of MEETING_BRIEF_SECTIONS) {
    if (def.type !== "claims") continue;
    const v = body[def.key];
    if (!Array.isArray(v)) continue;
    for (const c of v) {
      const ids = c && typeof c === "object" && Array.isArray((c as JsonObject).source_ids) ? (c as JsonObject).source_ids : [];
      for (const id of ids) if (typeof id === "string") out.add(id);
    }
  }
  return Array.from(out);
}
