"use client";

import clsx from "clsx";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { DemoBadge } from "@/components/Badges";
import { Skeleton } from "@/components/Skeleton";
import { StreamLog } from "@/components/StreamLog";
import { getReport } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import type { ReportOut } from "@/lib/types";
import { useInvestigation } from "@/lib/useInvestigation";
import { GeneratorLabel } from "./primitives";
import { ReportBody } from "./ReportBody";
import { Sources } from "./Sources";

export interface InvestigationPanelProps {
  entityType: "company" | "person";
  entityId: string;
  entityName: string;
  onClose: () => void;
}

const btn = "font-mono text-[10.5px] uppercase tracking-[0.08em] text-ink-2 transition-colors hover:text-ink disabled:opacity-50";

/**
 * Inline RADAR ANALYST run: streams agent steps, then renders the resulting
 * Anti Brief section by section with citations. Starts on mount.
 */
export function InvestigationPanel({ entityType, entityId, entityName, onClose }: InvestigationPanelProps) {
  const router = useRouter();
  const inv = useInvestigation();
  const { start } = inv;
  const [report, setReport] = useState<ReportOut | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);
  const [runs, setRuns] = useState(1);
  const panelRef = useRef<HTMLElement>(null);

  // Deferred so React strict-mode's mount/unmount/mount does not fire two runs.
  useEffect(() => {
    const t = window.setTimeout(() => {
      setReport(null);
      setReportError(null);
      void start({ entity_type: entityType, entity_id: entityId, live_fetch: true });
    }, 0);
    return () => window.clearTimeout(t);
  }, [start, entityType, entityId, runs]);

  useEffect(() => {
    panelRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, []);

  const reportId = inv.reportId;
  useEffect(() => {
    if (!reportId || inv.running) return;
    let cancelled = false;
    getReport(reportId)
      .then((r) => {
        if (cancelled) return;
        setReport(r);
        router.refresh();
      })
      .catch(() => !cancelled && setReportError("The brief was generated but could not be loaded."));
    return () => {
      cancelled = true;
    };
  }, [reportId, inv.running, router]);

  const done = !inv.running && !!report;

  return (
    <section
      ref={panelRef}
      id="investigation"
      aria-label={`Investigation of ${entityName}`}
      className="mt-6 animate-rise scroll-mt-24 border border-ink bg-paper"
    >
      <header className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-ink px-5 py-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="label text-ink">Radar Analyst</span>
          <span className="font-serif text-[18px] italic text-ink">Investigating {entityName}</span>
          {inv.running && <span className="label text-accent">● Live fetch</span>}
        </div>
        <div className="flex items-center gap-5">
          {report && (
            <Link href={`/reports/${report.id}`} className={clsx(btn, "text-ink")}>
              Open full report →
            </Link>
          )}
          {inv.running ? (
            <button type="button" className={btn} onClick={inv.cancel}>
              Stop
            </button>
          ) : (
            <button type="button" className={btn} onClick={() => setRuns((n) => n + 1)}>
              Re-run
            </button>
          )}
          <button type="button" className={btn} onClick={onClose} aria-label="Close investigation">
            Close ×
          </button>
        </div>
      </header>

      <div className="grid gap-0 lg:grid-cols-[minmax(0,5fr)_minmax(0,8fr)]">
        <div className="border-b border-rule p-5 lg:border-b-0 lg:border-r">
          <StreamLog events={inv.events} running={inv.running} error={inv.error} title="Agent trace" maxHeight={done ? 520 : 360} />
          <p className="mt-3 text-[12px] leading-relaxed text-ink-3">
            The analyst gathers the profile, 90 days of signals, metric windows, team and papers, fetches live public pages,
            then writes the brief. Untrusted page text is treated as data, never as instructions.
          </p>
        </div>

        <div className="max-h-[78vh] min-w-0 overflow-y-auto p-5">
          {!report && !reportError && (
            <div aria-busy={inv.running}>
              <p className="label mb-3">{inv.error ? "No brief produced" : inv.running ? "Assembling the Anti Brief…" : "Waiting for the analyst"}</p>
              {inv.error ? (
                <p className="text-[13.5px] text-ink-2">
                  The run stopped before a brief was written. Re-run to try again; the score page above is unaffected.
                </p>
              ) : (
                <div className="space-y-6">
                  <Skeleton lines={3} />
                  <Skeleton lines={4} />
                  <Skeleton lines={2} />
                </div>
              )}
            </div>
          )}
          {reportError && <p className="text-[13.5px] text-accent">{reportError}</p>}
          {report && (
            <article className="animate-fade-in">
              <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1.5">
                <span className="label text-accent">{report.kind === "brief" ? "Anti Brief" : report.kind}</span>
                <GeneratorLabel generator={report.generator} model={report.model} />
                {report.is_demo && <DemoBadge />}
                <span className="tnum font-mono text-[10.5px] text-ink-3">{fmtDateTime(report.created_at)}</span>
              </div>
              {report.generator === "template" && (
                <p className="mb-4 border-l-2 border-rule-strong pl-3 text-[12.5px] leading-relaxed text-ink-2">
                  LLM disabled — this brief is a deterministic template assembled from database facts. Market and competitor
                  sections stay empty rather than guessed.
                </p>
              )}
              <ReportBody kind={report.kind} body={report.body} markdown={report.markdown} density="panel" citePrefix="brief-source" />
              <div className="mt-2 border-t border-ink pt-3">
                <h3 className="label mb-3 text-ink">Sources · {report.sources.length}</h3>
                <Sources sources={report.sources} idPrefix="brief-source" />
              </div>
            </article>
          )}
        </div>
      </div>
    </section>
  );
}
