import "server-only";
import { cache } from "react";
import {
  getCompany,
  getDiligence,
  getDiligenceIndex,
  getDiligenceStatus,
  getFormation,
  getFounder,
  getGraph,
  getPortfolio,
  getPortfolioBrief,
  getPortfolioBriefArchive,
  getPortfolioCompanies,
  getPortfolioCompany,
  getPortfolioComps,
  getPortfolioCompsFor,
  getPortfolioEvent,
  getReport,
  getStatus,
  orNull,
} from "@/lib/api";
import type { BriefArchiveItem, CompsMap, PortfolioIndexItem } from "@/lib/types-portfolio";
import type { DiligenceIndex, DiligenceStatus } from "@/lib/types-diligence";
import type { FormationFeed, GraphOut, SystemStatus } from "@/lib/types";

/** Request-scoped, deduplicated loaders (metadata + page share one fetch). */
export const loadCompany = cache((slug: string) => orNull(getCompany(slug)));
export const loadFounder = cache((slug: string) => orNull(getFounder(slug)));
export const loadReport = cache((id: string) => orNull(getReport(id)));

/** Graph is optional context — never fail the page because of it. */
export async function loadGraph(entityId: string): Promise<GraphOut | null> {
  try {
    return await getGraph({ entity_id: entityId, depth: 2 });
  } catch {
    return null;
  }
}

/** System status (provider modes, LLM) — optional context, null when unavailable. */
export const loadStatus = cache(async (): Promise<SystemStatus | null> => {
  try {
    return await getStatus();
  } catch {
    return null;
  }
});

/** Whether an LLM provider is active (null when status is unavailable). */
export async function loadLlmEnabled(): Promise<boolean | null> {
  const s = await loadStatus();
  return typeof s?.llm?.enabled === "boolean" ? s.llm.enabled : null;
}

/** Formation candidates (to link an entity to the candidates it belongs to) — optional context. */
export const loadFormationFeed = cache(async (): Promise<FormationFeed | null> => {
  try {
    return await getFormation();
  } catch {
    return null;
  }
});

/** Newest stored Anti Brief (`kind: "brief"`) among an entity's reports, or null. */
export function latestBriefId(reports: { id: string; kind: string; created_at: string }[] | null | undefined): string | null {
  const briefs = (reports ?? []).filter((r) => r.kind === "brief").sort((a, b) => b.created_at.localeCompare(a.created_at));
  return briefs[0]?.id ?? null;
}

/** Diligence workspace for a company (null when the company is unknown). */
export const loadDiligence = cache((slug: string) => orNull(getDiligence(slug)));

/**
 * Diligence index (workspaces + candidates) — optional context for "Open diligence" links: fetched once per
 * request, never fails the page (null on an older backend or snapshot without it).
 */
export const loadDiligenceIndex = cache(async (): Promise<DiligenceIndex | null> => {
  try {
    return await getDiligenceIndex();
  } catch {
    return null;
  }
});

/** Diligence status for one company — optional context (null when unavailable). */
export const loadDiligenceStatus = cache(async (slug: string): Promise<DiligenceStatus | null> => {
  try {
    return await getDiligenceStatus(slug);
  } catch {
    return null;
  }
});

// ── Portfolio Pulse ──────────────────────────────────────────────────────────

/** The Pulse page payload (throws: the page's error boundary explains an outage). */
export const loadPortfolio = cache(() => getPortfolio());
/** Portfolio view of one company (null when it is not a portfolio company). */
export const loadPortfolioCompany = cache((slug: string) => orNull(getPortfolioCompany(slug)));
/** One material event (null when unknown). */
export const loadPortfolioEvent = cache((id: string) => orNull(getPortfolioEvent(id)));
/** Competitive map for one portfolio company (null when unknown). */
export const loadPortfolioCompsFor = cache((slug: string) => orNull(getPortfolioCompsFor(slug)));
/** Stored portfolio brief: the latest, or one day's (null when none was stored). */
export const loadPortfolioBrief = cache((day?: string) => orNull(getPortfolioBrief(day)));

/**
 * Portfolio companies — optional context for PORTFOLIO badges, company tabs and the home link: fetched once per
 * request, never fails the page (null on an older backend or snapshot without portfolio data).
 */
export const loadPortfolioIndex = cache(async (): Promise<PortfolioIndexItem[] | null> => {
  try {
    return await getPortfolioCompanies();
  } catch {
    return null;
  }
});

/** Every competitive map — optional context ("tracked as a comp of …", "via" lines); null when unavailable. */
export const loadPortfolioComps = cache(async (): Promise<CompsMap[] | null> => {
  try {
    return await getPortfolioComps();
  } catch {
    return null;
  }
});

/** Stored portfolio brief editions — optional context; null when unavailable. */
export const loadPortfolioBriefArchive = cache(async (): Promise<BriefArchiveItem[] | null> => {
  try {
    return await getPortfolioBriefArchive();
  } catch {
    return null;
  }
});
