/**
 * /demo/diligence pre-flight: select the demo dataset, wake the backend, fetch what the plan needs (through
 * the same-origin proxy) and warm the pages the tour opens. Browser-only.
 */
import type { SinceSnapshot } from "../../lib/types";
import type { ChangesReport } from "../../lib/types-changes";
import type { DiligenceIndex, DiligenceWorkspace } from "../../lib/types-diligence";
import type { AgentsOverview, CaseOut, FailureOut } from "../../lib/types-agents";
import { buildDiligencePlan, pickWorkspace, type DiligencePlan } from "./diligencePlan";
import { getJson, getWhileWaking, optional, warmPages, type WarmStatus } from "./warm";

const enc = encodeURIComponent;

export async function warmDiligencePlan(onStatus: (s: WarmStatus) => void, signal: AbortSignal): Promise<DiligencePlan> {
  onStatus({ phase: "dataset", attempt: 0 });
  await fetch("/api/admin/dataset?mode=demo", { cache: "no-store", signal }).catch(() => undefined);
  await getWhileWaking<unknown>("/api/proxy/health", onStatus, signal);
  onStatus({ phase: "loading", attempt: 0 });

  const index = await getWhileWaking<DiligenceIndex>("/api/proxy/diligence", onStatus, signal);
  const slug = pickWorkspace(index)?.company.slug ?? null;
  const [workspace, company, changes, agents, failures, cases] = await Promise.all([
    slug ? optional(getJson<DiligenceWorkspace>(`/api/proxy/diligence/${enc(slug)}`, signal), null) : Promise.resolve(null),
    slug
      ? optional(
          getJson<{ summary: { name: string; score: number | null }; since_snapshot?: SinceSnapshot | null }>(`/api/proxy/companies/${enc(slug)}`, signal).then((d) => ({
            name: d.summary.name,
            score: d.summary.score,
            since_snapshot: d.since_snapshot ?? null,
          })),
          null,
        )
      : Promise.resolve(null),
    optional(getJson<ChangesReport>("/api/proxy/changes", signal), null),
    optional(getJson<AgentsOverview>("/api/proxy/agents", signal), null),
    optional(getJson<FailureOut[]>("/api/proxy/agents/failures", signal), []),
    optional(getJson<CaseOut[]>("/api/proxy/agents/cases", signal), []),
  ]);
  const plan = buildDiligencePlan({ index, workspace, company, changes, agents, failures, cases });
  await warmPages(plan.steps.map((st) => st.url), onStatus, signal);
  return plan;
}
