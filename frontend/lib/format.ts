/**
 * Formatting helpers. All dates render in UTC so server and client output
 * match exactly (no hydration drift) and so day boundaries agree with the
 * backend's clock.
 */
import type { EntityKind } from "./types";

const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
const MONTHS_LONG = [
  "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
  "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
];
const WEEKDAYS = ["SUNDAY", "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY"];

export const pad2 = (n: number): string => String(n).padStart(2, "0");

/** Parse an ISO date or datetime. Date-only strings are treated as UTC midnight. */
export function parseDate(value: string | Date | null | undefined): Date | null {
  if (!value) return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  const iso = /^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00Z` : value;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "TUESDAY 06 OCTOBER 2026" */
export function dateLine(d: Date): string {
  return `${WEEKDAYS[d.getUTCDay()]} ${pad2(d.getUTCDate())} ${MONTHS_LONG[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

/** "06 OCT 2026" (or "06 OCT" with year=false). */
export function fmtDate(value: string | Date | null | undefined, opts: { year?: boolean } = {}): string {
  const d = parseDate(value);
  if (!d) return "—";
  const base = `${pad2(d.getUTCDate())} ${MONTHS[d.getUTCMonth()]}`;
  return opts.year === false ? base : `${base} ${d.getUTCFullYear()}`;
}

/** "06 OCT 2026 · 14:05 UTC" */
export function fmtDateTime(value: string | Date | null | undefined): string {
  const d = parseDate(value);
  if (!d) return "—";
  return `${fmtDate(d)} · ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())} UTC`;
}

/** Whole days between two instants (b - a), floored. */
export function daysBetween(a: Date, b: Date): number {
  return Math.floor((b.getTime() - a.getTime()) / 86_400_000);
}

/** 1234 → "1,234"; 12345 → "12.3k"; 1.2e6 → "1.2M". */
export function fmtCompact(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e6) return `${(n / 1e6).toFixed(abs >= 1e7 ? 0 : 1)}M`;
  if (abs >= 1e4) return `${(n / 1e3).toFixed(abs >= 1e5 ? 0 : 1)}k`;
  return Math.round(n).toLocaleString("en-US");
}

/** Number with fixed decimals, trimming trailing ".0". */
export function fmtNum(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Number(n.toFixed(digits)).toLocaleString("en-US", { maximumFractionDigits: digits });
}

/** Signed delta with a true minus sign: +18, −4, 0. */
export function fmtSigned(n: number, digits = 0): string {
  const rounded = Number(n.toFixed(digits));
  if (rounded === 0) return "0";
  return `${rounded > 0 ? "+" : "−"}${Math.abs(rounded).toLocaleString("en-US", { maximumFractionDigits: digits })}`;
}

/** Registrable-ish host of a URL for display ("github.com"), or null. */
export function domainOf(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return null;
  }
}

/** True for http(s) URLs only — never render javascript:/data: links. */
export function isSafeUrl(url: string | null | undefined): url is string {
  if (!url) return false;
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:";
  } catch {
    return false;
  }
}

/**
 * RFC 2606 / 6761 reserved hosts. Fictional (demo) data may only ever link to
 * these — never to a real third-party host where anyone could register the
 * invented handle.
 */
export function isReservedHost(url: string | null | undefined): boolean {
  if (!isSafeUrl(url)) return false;
  const host = new URL(url).hostname.toLowerCase().replace(/\.$/, "");
  return (
    /\.(example|test|invalid|localhost)$/.test(host) ||
    ["example", "test", "invalid", "localhost"].includes(host) ||
    /^(.+\.)?example\.(com|net|org)$/.test(host)
  );
}

/**
 * The href to render for an external URL, or null when it must not be a link:
 * non-http(s) schemes always, and — for demo entities — anything outside the
 * reserved example hosts.
 */
export function externalHref(url: string | null | undefined, isDemo = false): string | null {
  if (!isSafeUrl(url)) return null;
  if (isDemo && !isReservedHost(url)) return null;
  return url;
}

const HANDLE_RE = /^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,38})$/;

/** github.com profile/org URL for a real entity; null for demo entities or malformed handles. */
export function githubUrl(handle: string | null | undefined, isDemo: boolean): string | null {
  if (isDemo || !handle || !HANDLE_RE.test(handle)) return null;
  return `https://github.com/${encodeURIComponent(handle)}`;
}

/** x.com profile URL for a real entity; null for demo entities or malformed handles. */
export function xUrl(handle: string | null | undefined, isDemo: boolean): string | null {
  const h = handle?.replace(/^@/, "");
  if (isDemo || !h || !/^[A-Za-z0-9_]{1,15}$/.test(h)) return null;
  return `https://x.com/${encodeURIComponent(h)}`;
}

/**
 * Role label for a person. Only people with a recorded founder link are called
 * "Founder"; everyone else is a person to watch — never presume a role.
 */
export function personKindLabel(p: { is_founder: boolean }): string {
  return p.is_founder ? "Founder" : "Person to watch";
}

/** Factual marker for a person with no current company — states the record, not a prediction. */
export const NO_COMPANY_LABEL = "No company on record";

const SOURCE_META: Record<string, { glyph: string; label: string }> = {
  github: { glyph: "GH", label: "GitHub" },
  arxiv: { glyph: "AX", label: "arXiv" },
  hackernews: { glyph: "HN", label: "Hacker News" },
  website: { glyph: "WB", label: "Website" },
  jobs: { glyph: "JB", label: "Jobs" },
  x: { glyph: "X", label: "X" },
  manual: { glyph: "MN", label: "Manual" },
  demo: { glyph: "DM", label: "Demo" },
  system: { glyph: "SY", label: "System" },
};

/** Two-letter mono glyph for a source family ("GH", "AX", "HN" …). */
export function sourceGlyph(sourceType: string | null | undefined): string {
  if (!sourceType) return "··";
  return SOURCE_META[sourceType]?.glyph ?? sourceType.slice(0, 2).toUpperCase();
}

export function sourceLabel(sourceType: string | null | undefined): string {
  if (!sourceType) return "Unknown source";
  return SOURCE_META[sourceType]?.label ?? sourceType.replace(/_/g, " ");
}

/** Canonical in-app route for an entity. */
export function hrefFor(entityType: EntityKind | string, slugOrId: string): string {
  const s = encodeURIComponent(slugOrId);
  switch (entityType) {
    case "company":
      return `/companies/${s}`;
    case "person":
      return `/founders/${s}`;
    default:
      return `/search?q=${s}`;
  }
}

/** "too_early" → "Too early" */
export function humanize(key: string): string {
  const s = key.replace(/[_.]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}
