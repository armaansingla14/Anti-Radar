/** Editorial helpers for the feed pages: prose counts, long dates, entity routes. */
import { isSafeUrl, parseDate } from "@/lib/format";
import type { EntityLink } from "@/lib/types";

const SMALL = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"];
const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** Newspaper style: spell out one–nine, digits (with separators) from 10. */
export function spell(n: number): string {
  return n >= 0 && n < 10 ? SMALL[n] : n.toLocaleString("en-US");
}

/** "3 thesis shifts" / "one founder" — count + noun, pluralised. */
export function counted(n: number, singular: string, plural = `${singular}s`): string {
  return `${spell(n)} ${n === 1 ? singular : plural}`;
}

/** ["a", "b", "c"] → "a, b and c". */
export function joinProse(parts: string[]): string {
  if (parts.length <= 1) return parts.join("");
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

export function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** "2026-10-06" → "Tuesday, October 6" (UTC, matching the backend clock). */
export function longDate(value: string, opts: { year?: boolean } = {}): string {
  const d = parseDate(value);
  if (!d) return value;
  const base = `${DAYS[d.getUTCDay()]}, ${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
  return opts.year ? `${base}, ${d.getUTCFullYear()}` : base;
}

/** "17:41 UTC" */
export function clockTime(value: string | null | undefined): string {
  const d = parseDate(value);
  if (!d) return "—";
  return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")} UTC`;
}

/** Hours elapsed between an ISO instant and a reference instant, rounded. */
export function hoursAgo(value: string | null | undefined, now: Date): number | null {
  const d = parseDate(value);
  if (!d) return null;
  return Math.max(0, Math.round((now.getTime() - d.getTime()) / 3_600_000));
}

export const THEMES: { key: string; label: string }[] = [
  { key: "ai", label: "AI" },
  { key: "robotics", label: "Robotics" },
  { key: "defense", label: "Defense" },
  { key: "energy", label: "Energy" },
  { key: "semiconductors", label: "Semiconductors" },
  { key: "manufacturing", label: "Manufacturing" },
  { key: "frontier_infrastructure", label: "Frontier Infra" },
];

export function themeLabel(key: string | null | undefined): string | null {
  if (!key) return null;
  return THEMES.find((t) => t.key === key)?.label ?? key.replace(/_/g, " ");
}

/** EntityLink as emitted by the backend (may carry is_demo, not in the TS contract). */
export type LooseEntityLink = EntityLink & { is_demo?: boolean };

export interface ResolvedLink {
  href: string;
  external: boolean;
}

/**
 * Where an entity link should go. Companies and founders have in-app reports;
 * real repositories/papers go to their public source; anything else falls back
 * to in-app search so the link is never dead.
 */
export function entityHref(e: LooseEntityLink): ResolvedLink {
  const key = e.slug ?? e.id ?? e.name;
  if (e.type === "company" && key) return { href: `/companies/${encodeURIComponent(key)}`, external: false };
  if (e.type === "person" && key) return { href: `/founders/${encodeURIComponent(key)}`, external: false };
  if (!e.is_demo && e.type === "repository" && e.slug && /^[\w.-]+\/[\w.-]+$/.test(e.slug)) {
    const url = `https://github.com/${e.slug}`;
    if (isSafeUrl(url)) return { href: url, external: true };
  }
  if (!e.is_demo && e.type === "paper" && e.slug && /^\d{4}\.\d{4,5}(v\d+)?$/.test(e.slug)) {
    return { href: `https://arxiv.org/abs/${e.slug}`, external: true };
  }
  return { href: `/search?q=${encodeURIComponent(e.name)}`, external: false };
}

const TYPE_GLYPH: Record<string, string> = { company: "CO", person: "FN", repository: "GH", paper: "AX" };

export function entityGlyph(type: string): string {
  return TYPE_GLYPH[type] ?? type.slice(0, 2).toUpperCase();
}
