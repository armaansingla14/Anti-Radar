/**
 * Backend selection + session (who is looking, can they write).
 *
 * Two backends, both server-only env vars (never shipped to the browser):
 * - BACKEND_URL       live data
 * - DEMO_BACKEND_URL  the verified real-data snapshot (selected by the `radar_dataset=demo` cookie)
 *
 * Write access: the `/unlock` page stores the admin token in an httpOnly cookie (`radar_admin`); the
 * proxy turns it into the `X-Radar-Admin` header. The token never reaches client JavaScript.
 *
 * This module is imported by `lib/api.ts` (shared by server and client code), so it must not statically
 * import `next/headers`; `getSession()` loads it lazily and only runs on the server.
 */

export const DATASET_COOKIE = "radar_dataset";
export const ADMIN_COOKIE = "radar_admin";
export const ADMIN_HEADER = "X-Radar-Admin";

export type Dataset = "live" | "demo";

export interface Session {
  app_env: "development" | "demo" | "production";
  read_only: boolean;
  admin: boolean;
  demo_snapshot: boolean;
  demo_synthetic: boolean;
  snapshot_as_of: string | null;
  /** Which backend served this session. */
  dataset: Dataset;
  /** False when the backend could not be reached (asleep or misconfigured); other fields are defaults. */
  reachable: boolean;
}

export type BackendResolution = { ok: true; base: string } | { ok: false; detail: "backend_not_configured" };

type Env = Record<string, string | undefined>;

const DEV_FALLBACK = "http://localhost:8000";

export function parseDataset(value: string | undefined | null): Dataset {
  return value === "demo" ? "demo" : "live";
}

/** Production deployment (Vercel, or an explicit APP_ENV=production) — no silent localhost fallback. */
export function isProductionRuntime(env: Env = process.env): boolean {
  return env.NODE_ENV === "production" && (Boolean(env.VERCEL) || env.APP_ENV === "production");
}

function clean(url: string | undefined): string | undefined {
  const v = url?.trim();
  return v ? v.replace(/\/+$/, "") : undefined;
}

/**
 * The backend base URL for a dataset. In production the URL must be set and https; elsewhere a missing
 * URL falls back to BACKEND_URL / localhost for local development.
 */
export function resolveBackend(dataset: Dataset, env: Env = process.env): BackendResolution {
  const live = clean(env.BACKEND_URL);
  const demo = clean(env.DEMO_BACKEND_URL);
  const prod = isProductionRuntime(env);
  const url = dataset === "demo" ? demo ?? (prod ? undefined : live) : live;
  if (prod) {
    if (!url || !/^https:\/\/[^/]+/i.test(url)) return { ok: false, detail: "backend_not_configured" };
    return { ok: true, base: url };
  }
  return { ok: true, base: url ?? DEV_FALLBACK };
}

export const DEFAULT_SESSION: Session = {
  app_env: "development",
  read_only: true,
  admin: false,
  demo_snapshot: false,
  demo_synthetic: false,
  snapshot_as_of: null,
  dataset: "live",
  reachable: false,
};

export function isReadOnly(session: Pick<Session, "read_only"> | null | undefined): boolean {
  return session ? session.read_only : true;
}

/**
 * Re-throw Next.js control-flow errors (dynamic-rendering bailout, notFound, redirect) so a surrounding
 * try/catch does not swallow them. No-op in the browser and outside Next (unit tests).
 */
export async function rethrowNextInternal(err: unknown): Promise<void> {
  if (typeof window !== "undefined") return;
  let rethrow: ((e: unknown) => void) | undefined;
  try {
    rethrow = (await import("next/navigation")).unstable_rethrow;
  } catch {
    return;
  }
  rethrow(err);
}

/** Cookie values for the current request (server only; empty outside a request scope). */
export async function requestCookies(): Promise<{ dataset: Dataset; admin?: string }> {
  if (typeof window !== "undefined") return { dataset: "live" };
  try {
    const { cookies } = await import("next/headers");
    const jar = await cookies();
    return { dataset: parseDataset(jar.get(DATASET_COOKIE)?.value), admin: jar.get(ADMIN_COOKIE)?.value || undefined };
  } catch (err) {
    await rethrowNextInternal(err); // prerender bailout: this page is dynamic
    return { dataset: "live" }; // outside a request scope
  }
}

/** Server-side: the backend's view of this visitor (`GET /api/session`), with safe read-only defaults. */
export async function getSession(timeoutMs = 8000): Promise<Session> {
  const { dataset, admin } = await requestCookies();
  const backend = resolveBackend(dataset);
  if (!backend.ok) return { ...DEFAULT_SESSION, dataset };
  try {
    const res = await fetch(`${backend.base}/api/session`, {
      cache: "no-store",
      headers: { Accept: "application/json", ...(admin ? { [ADMIN_HEADER]: admin } : {}) },
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!res.ok) return { ...DEFAULT_SESSION, dataset };
    const body = (await res.json()) as Omit<Session, "dataset" | "reachable">;
    return { ...DEFAULT_SESSION, ...body, dataset, reachable: true };
  } catch (err) {
    await rethrowNextInternal(err);
    return { ...DEFAULT_SESSION, dataset };
  }
}
