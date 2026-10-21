import "server-only";

/**
 * The verified snapshot, served without a backend.
 *
 * `frontend/snapshot/` holds the backend's GET responses for every page of the public site, recorded from
 * the verified real-data snapshot (scripts/export-snapshot.mjs). Each response is one gzip file named by the
 * SHA-1 of its normalised request key (`/path?sorted=query`), so a lookup is a single file read.
 *
 * `install()` (called once by instrumentation.ts) puts a fetch hook on globalThis that lib/dataSource.ts
 * `serverFetch` uses for every server-side backend call and the browser proxy:
 *   snapshot  answer from the archive; writes → 403 snapshot_read_only; misses → 404 not_in_snapshot
 *   auto      try the live backend (short timeout); if it does not answer, answer from the archive
 *   record    (SNAPSHOT_RECORD=1, live mode) pass through and write each GET response into the archive
 */
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { gunzipSync, gzipSync } from "node:zlib";
import { dataSource, type DataSource } from "./dataSource";
import { snapshotKey } from "./snapshotKey";

export { snapshotKey };

export const SNAPSHOT_DIR = path.join(process.cwd(), "snapshot");

export interface SnapshotEntry {
  key: string;
  status: number;
  content_type: string;
  content_disposition?: string;
  body: string;
}

export interface SnapshotMeta {
  as_of: string;
  captured_at: string;
  exported_at: string;
  source_commit?: string;
  counts?: Record<string, number>;
}


export function entryFile(key: string, dir = SNAPSHOT_DIR): string {
  const h = createHash("sha1").update(key).digest("hex");
  return path.join(dir, "data", h.slice(0, 2), `${h}.json.gz`);
}

const cache = new Map<string, SnapshotEntry | null>();
const CACHE_MAX = 400;

export function readEntry(key: string, dir = SNAPSHOT_DIR): SnapshotEntry | null {
  if (cache.has(key)) return cache.get(key)!;
  let entry: SnapshotEntry | null = null;
  try {
    entry = JSON.parse(gunzipSync(fs.readFileSync(entryFile(key, dir))).toString("utf8")) as SnapshotEntry;
  } catch {
    entry = null;
  }
  if (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value as string);
  cache.set(key, entry);
  return entry;
}

export function writeEntry(entry: SnapshotEntry, dir = SNAPSHOT_DIR): void {
  const file = entryFile(entry.key, dir);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  // mtime 0 keeps the gzip bytes identical across exports of the same data.
  fs.writeFileSync(file, gzipSync(Buffer.from(JSON.stringify(entry)), { level: 9 }));
}

let metaCache: SnapshotMeta | null | undefined;
export function snapshotMeta(dir = SNAPSHOT_DIR): SnapshotMeta | null {
  if (metaCache !== undefined) return metaCache;
  try {
    metaCache = JSON.parse(fs.readFileSync(path.join(dir, "meta.json"), "utf8")) as SnapshotMeta;
  } catch {
    metaCache = null;
  }
  return metaCache;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function respond(entry: SnapshotEntry, method: string): Response {
  const headers: Record<string, string> = { "content-type": entry.content_type || "application/json" };
  if (entry.content_disposition) headers["content-disposition"] = entry.content_disposition;
  return new Response(method === "HEAD" ? null : entry.body, { status: entry.status, headers });
}

interface IndexedEntity {
  entity_type: "company" | "person";
  id: string;
  slug: string;
  name: string;
  subtitle: string | null;
  score: number | null;
  terms: string;
}

let searchIndex: IndexedEntity[] | undefined;
function loadSearchIndex(dir = SNAPSHOT_DIR): IndexedEntity[] {
  if (searchIndex) return searchIndex;
  try {
    searchIndex = JSON.parse(fs.readFileSync(path.join(dir, "search-index.json"), "utf8")) as IndexedEntity[];
  } catch {
    searchIndex = [];
  }
  return searchIndex;
}

/** Name/slug/subtitle substring search over the exported entities (the live search parser needs the backend). */
export function snapshotSearch(url: URL, dir = SNAPSHOT_DIR): unknown {
  const q = (url.searchParams.get("q") ?? "").trim();
  const limit = Math.max(1, Math.min(50, Number(url.searchParams.get("limit")) || 20));
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const hits = words.length
    ? loadSearchIndex(dir).filter((e) => words.every((w) => e.terms.includes(w)))
    : [];
  hits.sort((a, b) => {
    const an = a.name.toLowerCase().startsWith(words[0] ?? "") ? 0 : 1;
    const bn = b.name.toLowerCase().startsWith(words[0] ?? "") ? 0 : 1;
    return an - bn || (b.score ?? -1) - (a.score ?? -1) || a.name.localeCompare(b.name);
  });
  return {
    query: q,
    filters: {},
    interpretation: q ? [`Name match in the verified snapshot for “${q}”`] : [],
    parser: "deterministic",
    results: hits.slice(0, limit).map((e) => ({
      entity_type: e.entity_type,
      id: e.id,
      slug: e.slug,
      name: e.name,
      subtitle: e.subtitle,
      score: e.score,
      match_reasons: ["name"],
      is_demo: false,
    })),
    total: hits.length,
  };
}

/** Answer one backend request from the archive. */
export function serveSnapshot(url: URL, method: string, dir = SNAPSHOT_DIR): Response {
  const p = url.pathname;
  const meta = snapshotMeta(dir);
  if (p === "/api/session") {
    return json(200, {
      app_env: "demo",
      read_only: true,
      admin: false,
      demo_snapshot: true,
      demo_synthetic: false,
      snapshot_as_of: meta?.as_of ?? null,
      static_snapshot: true,
    });
  }
  if (method !== "GET" && method !== "HEAD") return json(403, { detail: "snapshot_read_only" });
  if (p === "/api/search") return json(200, snapshotSearch(url, dir));
  const key = snapshotKey(url);
  const entry = key ? readEntry(key, dir) : null;
  if (entry) return respond(entry, method);
  if (p === "/api/health") return json(200, { status: "ok", source: "snapshot" });
  return json(404, { detail: "not_in_snapshot" });
}

// ── status for the masthead ──────────────────────────────────────────────────

let lastFallbackAt = 0;
let liveHealthy: { ok: boolean; at: number } | null = null;

export interface DataStatus {
  mode: DataSource;
  /** Data shown comes from the archive (snapshot mode, or auto mode while the backend is unreachable). */
  fromSnapshot: boolean;
  /** auto mode only: the live backend did not answer recently. */
  liveUnavailable: boolean;
  asOf: string | null;
  capturedAt: string | null;
}

export function dataStatus(): DataStatus {
  const mode = dataSource();
  const meta = snapshotMeta();
  const liveUnavailable = mode === "auto" && Date.now() - lastFallbackAt < 120_000;
  return {
    mode,
    fromSnapshot: mode === "snapshot" || liveUnavailable,
    liveUnavailable,
    asOf: meta?.as_of ?? null,
    capturedAt: meta?.captured_at ?? null,
  };
}

// ── the hook ─────────────────────────────────────────────────────────────────

type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

const AUTO_TIMEOUT_MS = 4000;

async function recordThrough(realFetch: FetchLike, input: string, init?: RequestInit): Promise<Response> {
  const res = await realFetch(input, init);
  const method = (init?.method ?? "GET").toUpperCase();
  const key = snapshotKey(new URL(input));
  const type = res.headers.get("content-type") ?? "";
  if (method !== "GET" || !key || res.status >= 500 || type.includes("text/event-stream") || key === "/session") return res;
  const body = await res.text();
  const disposition = res.headers.get("content-disposition") ?? undefined;
  writeEntry({ key, status: res.status, content_type: type, content_disposition: disposition, body });
  const headers: Record<string, string> = { "content-type": type };
  if (disposition) headers["content-disposition"] = disposition;
  return new Response(body, { status: res.status, headers });
}

async function autoThrough(realFetch: FetchLike, input: string, init?: RequestInit): Promise<Response> {
  const url = new URL(input);
  const method = (init?.method ?? "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD") return realFetch(input, init);
  // Skip a backend that failed moments ago instead of making every request wait for the timeout.
  if (liveHealthy && !liveHealthy.ok && Date.now() - liveHealthy.at < 30_000) {
    lastFallbackAt = Date.now();
    return serveSnapshot(url, method);
  }
  try {
    const signal = init?.signal ?? AbortSignal.timeout(AUTO_TIMEOUT_MS);
    const res = await realFetch(input, { ...init, signal });
    if ([502, 503, 504].includes(res.status)) throw new Error(`upstream ${res.status}`);
    liveHealthy = { ok: true, at: Date.now() };
    return res;
  } catch {
    liveHealthy = { ok: false, at: Date.now() };
    lastFallbackAt = Date.now();
    return serveSnapshot(url, method);
  }
}

export function install(): void {
  const mode = dataSource();
  const record = process.env.SNAPSHOT_RECORD === "1";
  if (mode === "live" && !record) return;
  const realFetch: FetchLike = globalThis.fetch.bind(globalThis);
  const hook: FetchLike = (input, init) => {
    if (mode === "snapshot") return Promise.resolve(serveSnapshot(new URL(input), (init?.method ?? "GET").toUpperCase()));
    if (mode === "auto") return autoThrough(realFetch, input, init);
    return recordThrough(realFetch, input, init);
  };
  (globalThis as { __radarFetch?: FetchLike }).__radarFetch = hook;
}
