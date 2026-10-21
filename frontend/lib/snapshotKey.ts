/**
 * Archive key for a backend request: `/api/companies?b=2&a=1&x=` → `/companies?a=1&b=2`.
 * Empty values are dropped (like lib/api.ts toQueryString) and parameters sorted, so the server render and the
 * browser proxy produce the same key for the same request. Pure: shared by the snapshot server and its tests.
 */
export function snapshotKey(url: URL): string | null {
  if (!url.pathname.startsWith("/api/")) return null;
  const pathPart = url.pathname.slice(4).replace(/\/+$/, "") || "/";
  const pairs = [...url.searchParams.entries()].filter(([, v]) => v !== "");
  pairs.sort(([ak, av], [bk, bv]) => (ak === bk ? (av < bv ? -1 : av > bv ? 1 : 0) : ak < bk ? -1 : 1));
  const qs = new URLSearchParams(pairs).toString();
  return qs ? `${pathPart}?${qs}` : pathPart;
}
