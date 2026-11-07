/**
 * /demo/portfolio pre-flight: select the demo dataset, wake the backend, fetch what the plan needs (through the
 * same-origin proxy) and warm the pages the tour opens. Browser-only.
 */
import type { DiligenceWorkspace } from "../../lib/types-diligence";
import type { AgentsOverview, FailureOut } from "../../lib/types-agents";
import type { CompsMap, EventDetail, PortfolioBrief, PortfolioCompany, Pulse } from "../../lib/types-portfolio";
import { buildPortfolioPlan, pickCompany, pickEventId, pickHandoffCard, pickHandoffEvent, PORTFOLIO_AGENT, type PortfolioPlan } from "./portfolioPlan";
import { getJson, getWhileWaking, optional, warmPages, type WarmStatus } from "./warm";

const enc = encodeURIComponent;

export async function warmPortfolioPlan(onStatus: (s: WarmStatus) => void, signal: AbortSignal): Promise<PortfolioPlan> {
  onStatus({ phase: "dataset", attempt: 0 });
  await fetch("/api/admin/dataset?mode=demo", { cache: "no-store", signal }).catch(() => undefined);
  await getWhileWaking<unknown>("/api/proxy/health", onStatus, signal);
  onStatus({ phase: "loading", attempt: 0 });

  const pulse = await getWhileWaking<Pulse>("/api/proxy/portfolio", onStatus, signal);
  const card = pickCompany(pulse.needs_attention);
  const eventId = pickEventId(card);
  const hCard = pickHandoffCard(pulse.needs_attention);
  const [company, event, comps, hCompany, workspace, brief, agents, failures, home] = await Promise.all([
    card ? optional(getJson<PortfolioCompany>(`/api/proxy/portfolio/${enc(card.company.slug)}`, signal), null) : Promise.resolve(null),
    eventId ? optional(getJson<EventDetail>(`/api/proxy/portfolio/events/${enc(eventId)}`, signal), null) : Promise.resolve(null),
    optional(getJson<CompsMap[]>("/api/proxy/portfolio/comps", signal), null),
    hCard ? optional(getJson<PortfolioCompany>(`/api/proxy/portfolio/${enc(hCard.company.slug)}`, signal), null) : Promise.resolve(null),
    hCard ? optional(getJson<DiligenceWorkspace>(`/api/proxy/diligence/${enc(hCard.company.slug)}`, signal), null) : Promise.resolve(null),
    optional(getJson<PortfolioBrief>("/api/proxy/portfolio/brief", signal), null),
    optional(getJson<AgentsOverview>("/api/proxy/agents", signal), null),
    optional(getJson<FailureOut[]>(`/api/proxy/agents/failures?agent=${enc(PORTFOLIO_AGENT)}`, signal), []),
    optional(getJson<{ summary?: { is_snapshot?: boolean } }>("/api/proxy/home", signal), null),
  ]);
  const hEventOut = pickHandoffEvent(hCompany);
  const hEvent = hEventOut ? await optional(getJson<EventDetail>(`/api/proxy/portfolio/events/${enc(hEventOut.id)}`, signal), null) : null;
  const plan = buildPortfolioPlan({
    pulse,
    company,
    event,
    comps,
    handoff: { company: hCompany, event: hEvent, workspace },
    brief,
    agents,
    failures,
    isSnapshot: Boolean(home?.summary?.is_snapshot),
  });
  await warmPages(plan.steps.map((st) => st.url), onStatus, signal);
  return plan;
}
