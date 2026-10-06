import "server-only";
import { cache } from "react";
import { getCompany, getFounder, getGraph, getReport, getStatus, orNull } from "@/lib/api";
import type { GraphOut } from "@/lib/types";

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

/** Whether an LLM provider is active (null when status is unavailable). */
export async function loadLlmEnabled(): Promise<boolean | null> {
  try {
    const s = await getStatus();
    return typeof s.llm?.enabled === "boolean" ? s.llm.enabled : null;
  } catch {
    return null;
  }
}
