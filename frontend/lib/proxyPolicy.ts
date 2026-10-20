/**
 * What the browser may reach through the same-origin proxy (`app/api/proxy/[...path]/route.ts`).
 *
 * Server components call the backend directly (BACKEND_URL), so the proxy only needs the sections that
 * client-side code fetches — keep this list to exactly those. Read-only views rendered on the server
 * (radar, daily, thesis, status, retrospective, graph, signals, session) are deliberately absent.
 * Every write section is additionally gated by the backend's admin token (X-Radar-Admin).
 */

/** Sections fetched by the browser for reads. */
export const PROXY_READ_SECTIONS: ReadonlySet<string> = new Set([
  "health", // /demo warm-up (waking backend)
  "home", // /demo warm-up
  "brief", // "Download .md" link (brief/markdown)
  "companies", // load-more, palette preview, /demo
  "founders", // load-more, palette preview
  "formation", // score anatomy drawer, /demo
  "meet", // /demo; meet/refresh (write)
  "reports", // report viewer, markdown download, /demo
  "search", // search page / palette
  "watchlist", // add / remove / mark seen (writes)
  "investigate", // SSE investigation (write)
]);

/** Sections the browser writes to (all admin-gated on the backend). */
export const PROXY_WRITE_SECTIONS: ReadonlySet<string> = new Set([
  "feedback",
  "investigate",
  "memo",
  "ingest",
  "meet",
  "meeting-brief",
  "watchlist",
]);

export const PROXY_ALLOWED_SECTIONS: ReadonlySet<string> = new Set([...PROXY_READ_SECTIONS, ...PROXY_WRITE_SECTIONS]);

const SEGMENT = /^[A-Za-z0-9._~@:+-]+$/;

/** True when `segments` (the path after /api/proxy/) may be forwarded. */
export function proxyPathAllowed(segments: string[]): boolean {
  if (segments.length === 0 || !PROXY_ALLOWED_SECTIONS.has(segments[0])) return false;
  return segments.every((s) => s !== "." && s !== ".." && SEGMENT.test(s));
}

/** Reads go to read sections; POST/DELETE only to write sections (the backend still enforces the admin gate). */
export function proxyMethodAllowed(segments: string[], method: string): boolean {
  const section = segments[0] ?? "";
  const m = method.toUpperCase();
  if (m === "GET" || m === "HEAD") return PROXY_READ_SECTIONS.has(section);
  if (m === "POST" || m === "DELETE") return PROXY_WRITE_SECTIONS.has(section);
  return false;
}
