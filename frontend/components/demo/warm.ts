/**
 * Demo pre-flight: select the demo dataset, wake the backend (Render free instances sleep), fetch what the
 * plan needs and warm the pages the tour will open. Browser-only; every request goes through the proxy.
 */
import type { CompanySummary, FormationFeed, HomeFeed, MeetFeed, ReportOut } from "../../lib/types";
import { buildPlan, type DemoPlan } from "./plan";

export type WarmStatus = { phase: "dataset" | "waking" | "loading" | "warming-pages"; attempt: number; detail?: string };

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

class Waking extends Error {}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { cache: "no-store", headers: { Accept: "application/json" }, signal });
  } catch (err) {
    if (signal?.aborted) throw err;
    throw new Waking("network");
  }
  if (res.status === 503 || res.status === 502 || res.status === 504) {
    const body = (await res.json().catch(() => null)) as { detail?: string } | null;
    throw new Waking(body?.detail ?? `HTTP ${res.status}`);
  }
  if (!res.ok) throw new Error(`HTTP ${res.status} on ${path}`);
  return (await res.json()) as T;
}

/** GET with retries while the backend is waking (503 backend_waking / network errors), up to `budgetMs`. */
async function getWhileWaking<T>(path: string, onStatus: (s: WarmStatus) => void, signal: AbortSignal, budgetMs = 180_000): Promise<T> {
  const end = Date.now() + budgetMs;
  for (let attempt = 1; ; attempt++) {
    try {
      return await getJson<T>(path, signal);
    } catch (err) {
      if (!(err instanceof Waking) || Date.now() > end || signal.aborted) throw err;
      onStatus({ phase: "waking", attempt, detail: err.message });
      await sleep(Math.min(5000, 1200 + attempt * 600));
    }
  }
}

async function optional<T>(p: Promise<T>, fallback: T): Promise<T> {
  try {
    return await p;
  } catch {
    return fallback;
  }
}

export async function warmAndPlan(onStatus: (s: WarmStatus) => void, signal: AbortSignal): Promise<DemoPlan> {
  onStatus({ phase: "dataset", attempt: 0 });
  // Route this browser to the demo dataset (the verified snapshot in production; the same backend locally).
  await fetch("/api/admin/dataset?mode=demo", { cache: "no-store", signal }).catch(() => undefined);

  await getWhileWaking<unknown>("/api/proxy/health", onStatus, signal);
  onStatus({ phase: "loading", attempt: 0 });
  const home = await getWhileWaking<HomeFeed>("/api/proxy/home", onStatus, signal);

  const [formation, meet, companies, briefs] = await Promise.all([
    optional(getJson<FormationFeed>("/api/proxy/formation", signal), null),
    optional(getJson<MeetFeed>("/api/proxy/meet?week=current", signal), null),
    optional(getJson<CompanySummary[]>("/api/proxy/companies?sort=attention_gap&limit=40", signal), []),
    optional(getJson<ReportOut[]>("/api/proxy/reports?kind=brief&limit=50", signal), []),
  ]);
  const meetTop = [...(meet?.people ?? [])].sort((a, b) => a.rank - b.rank)[0];
  const meetingBriefs = meetTop
    ? await optional(getJson<ReportOut[]>(`/api/proxy/reports?entity_id=${encodeURIComponent(meetTop.person.id)}&kind=meeting_brief&limit=5`, signal), [])
    : [];
  const plan = buildPlan({ home, formation, meet, companies, briefs, meetingBriefs });

  // Warm every page the tour opens (server render + backend queries), bounded so Start is never held long.
  // Skipped when this tab warmed them in the last ten minutes (a reload should land on Start quickly).
  const urls = [...new Set(plan.steps.map((s) => s.url).filter((u): u is string => !!u))];
  const key = `anti-demo-warmed:${urls.join("|")}`;
  let warmedAt = 0;
  try {
    warmedAt = Number(sessionStorage.getItem(key) ?? 0);
  } catch {
    /* storage unavailable */
  }
  if (Date.now() - warmedAt > 10 * 60_000) {
    onStatus({ phase: "warming-pages", attempt: 0 });
    await Promise.race([
      Promise.allSettled(urls.map((u) => fetch(u, { cache: "no-store", signal, headers: { Accept: "text/html" } }).then((r) => r.text()))),
      sleep(15_000),
    ]);
    try {
      sessionStorage.setItem(key, String(Date.now()));
    } catch {
      /* storage unavailable */
    }
  }
  return plan;
}
