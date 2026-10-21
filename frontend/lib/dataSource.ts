/**
 * Where server-side reads come from (server-only env; never shipped to the browser).
 *
 * - `live`     (default) the FastAPI backend: BACKEND_URL / DEMO_BACKEND_URL (see lib/session.ts).
 * - `snapshot` the verified snapshot exported into `frontend/snapshot/` and bundled with the deployment.
 *              No backend is contacted; writes are refused with 403 `snapshot_read_only`.
 * - `auto`     the live backend first; when it does not answer, the same snapshot (and a masthead notice).
 *
 * The reader itself (`lib/snapshot-server.ts`) needs `fs`/`zlib`, so it is installed at server start by
 * `instrumentation.ts` as a fetch hook; this module stays importable from client code.
 */

export type DataSource = "live" | "snapshot" | "auto";

/** Placeholder backend base used in snapshot mode; requests to it are answered by the snapshot hook. */
export const SNAPSHOT_BASE = "http://snapshot.invalid";

type Env = Record<string, string | undefined>;

export function dataSource(env: Env = process.env): DataSource {
  const v = (env.DATA_SOURCE ?? "").trim().toLowerCase();
  return v === "snapshot" || v === "auto" ? v : "live";
}

type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

/**
 * `fetch` for server-side backend calls: goes through the snapshot hook when one is installed
 * (snapshot / auto / recording), otherwise the platform fetch. In the browser this is plain fetch.
 */
export function serverFetch(input: string, init?: RequestInit): Promise<Response> {
  const hook = (globalThis as { __radarFetch?: FetchLike }).__radarFetch;
  return hook ? hook(input, init) : fetch(input, init);
}

/**
 * "Now" for relative times: the moment the verified snapshot was frozen (inlined at build time from
 * snapshot/meta.json when DATA_SOURCE=snapshot) so "4 h ago" never drifts; otherwise the wall clock.
 * Safe on server and client (no hydration mismatch).
 */
export function displayNow(): Date {
  const pinned = process.env.NEXT_PUBLIC_SNAPSHOT_NOW;
  return pinned ? new Date(pinned) : new Date();
}
