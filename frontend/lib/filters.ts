/**
 * Validate a URL filter value against the known options before it reaches the
 * API. Stale or shared links (e.g. after a taxonomy key is renamed) must
 * degrade to "filter ignored" rather than an API error page.
 */
export interface ResolvedFilter {
  /** Value to send to the API (undefined = no filter). */
  value: string | undefined;
  /** The raw value when it was dropped as unknown, else null. */
  unknown: string | null;
}

export function resolveFilter(raw: string | undefined, allowed: readonly string[]): ResolvedFilter {
  const v = raw?.trim();
  if (!v) return { value: undefined, unknown: null };
  // Options unavailable (taxonomy fetch failed): pass through; the page still guards the API's 422.
  if (allowed.length === 0) return { value: v, unknown: null };
  return allowed.includes(v) ? { value: v, unknown: null } : { value: undefined, unknown: v };
}
