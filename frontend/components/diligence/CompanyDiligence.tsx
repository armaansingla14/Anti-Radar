/** Company-page integration: the DILIGENCE STATUS strip, the ?tab= strip and the DILIGENCE tab body. */
import clsx from "clsx";
import Link from "next/link";
import { Quiet } from "@/components/entity/primitives";
import { fmtDate } from "@/lib/format";
import type { DiligenceStatus, DiligenceWorkspace } from "@/lib/types-diligence";
import { ClaimStatusBadge, DiligenceStateBadge } from "./Badges";
import { COMPANY_TABS, claimHref, claimLabel, countsLine, diligenceHref, pct, stateReason, tabHref, type CompanyTab } from "./logic";
import { RunDiligenceButton } from "./RunButton";
import { ExecSummary } from "./Workspace";

/** One line under the company header. NOT_RUN is quiet; nothing clickable that would fail on a read-only deployment. */
export function DiligenceStrip({ status, slug, writable, className }: { status: DiligenceStatus | null; slug: string; writable: boolean; className?: string }) {
  if (!status) return null;
  if (status.status === "NOT_RUN") {
    return (
      <div className={clsx("flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-rule py-2.5", className)} aria-label="Diligence status">
        <span className="label shrink-0 text-ink">Diligence</span>
        <span className="font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-3">Diligence not run</span>
        <RunDiligenceButton slug={slug} writable={writable} />
      </div>
    );
  }
  const reason = stateReason(status);
  return (
    <div className={clsx("flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-rule py-2.5", className)} aria-label="Diligence status">
      <span className="label shrink-0 text-ink">Diligence</span>
      <DiligenceStateBadge state={status.status} title={reason} />
      <span className="tnum font-mono text-[11px] text-ink-2">{countsLine(status.counts)}</span>
      {status.finished_at && <span className="font-mono text-[10.5px] uppercase text-ink-3">{fmtDate(status.finished_at)}</span>}
      <Link href={diligenceHref(slug)} className="link ml-auto font-mono text-[11px] uppercase tracking-[0.08em] text-ink">
        Open diligence →
      </Link>
    </div>
  );
}

const base = "font-mono text-[11px] uppercase tracking-[0.08em] transition-colors duration-150 whitespace-nowrap";
const on = "text-ink underline decoration-accent decoration-[1.5px] underline-offset-[7px]";
const off = "text-ink-2 hover:text-ink";

/** OVERVIEW · WHY NOW · DILIGENCE · RELATIONSHIPS · REPORTS — plain links, so they work without JS. */
export function CompanyTabs({ slug, active, reports }: { slug: string; active: CompanyTab; reports?: number }) {
  return (
    <nav aria-label="Company views" className="flex flex-wrap items-baseline gap-x-6 gap-y-3 border-b border-ink pb-3 pt-4">
      {COMPANY_TABS.map((t) => (
        <Link key={t.key} href={tabHref(slug, t.key)} className={clsx(base, t.key === active ? on : off)} aria-current={t.key === active ? "page" : undefined} scroll={false}>
          {t.label}
          {t.key === "reports" && reports !== undefined && <span className="tnum ml-1.5 text-ink-3">{reports}</span>}
        </Link>
      ))}
    </nav>
  );
}

/** DILIGENCE tab: summary, the first claims, and the way into the workspace. */
export function DiligenceTab({ ws, slug }: { ws: DiligenceWorkspace | null; slug: string }) {
  if (!ws || !ws.run) {
    return (
      <Quiet>
        Diligence has not been run for this company. It collects public artifacts, extracts the company&apos;s material technical claims and
        checks each against independent evidence.{" "}
        <Link href="/diligence" className="link">
          About diligence →
        </Link>
      </Quiet>
    );
  }
  const defs = ws.definitions ?? {};
  const top = ws.claims.slice(0, 6);
  return (
    <div className="space-y-10">
      <div className="flex flex-wrap items-baseline justify-between gap-3 border-t border-ink pt-3">
        <p className="font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-2">
          {ws.header.technical_domain ?? "Domain unknown"} · coverage {pct(ws.header.evidence_coverage)} · {ws.header.sources_analyzed} sources analyzed · method{" "}
          {ws.method_version}
        </p>
        <Link
          href={diligenceHref(slug)}
          className="border border-ink bg-ink px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.08em] text-paper transition-colors hover:border-accent hover:bg-accent"
        >
          Open the diligence workspace →
        </Link>
      </div>
      {ws.executive_summary && <ExecSummary summary={ws.executive_summary} labels={defs.status_labels} defs={defs} glossaryHref={diligenceHref(slug)} />}
      <div>
        <p className="label mb-2 text-ink">
          Claims <span className="tnum text-ink-3">{top.length} of {ws.claims.length}</span>
        </p>
        <ol className="border-t border-ink">
          {top.map((c) => (
            <li key={c.ordinal} className="grid grid-cols-[72px_minmax(0,1fr)] gap-x-3 gap-y-1.5 border-b border-rule py-3 md:grid-cols-[72px_minmax(0,1fr)_170px]">
              <span className="tnum pt-0.5 font-mono text-[10.5px] text-ink-3">{claimLabel(c.ordinal)}</span>
              <Link href={claimHref(slug, c.ordinal)} className="min-w-0 font-serif text-[16px] leading-snug text-ink hover:text-accent">
                “{c.text}”
              </Link>
              <span className="col-start-2 md:col-start-3 md:text-right">
                <ClaimStatusBadge status={c.status} labels={defs.status_labels} definition={defs.statuses?.[c.status]} />
              </span>
            </li>
          ))}
        </ol>
        {ws.claims.length > top.length && (
          <Link href={`${diligenceHref(slug)}#claims`} className="link mt-2 inline-block font-mono text-[10.5px] uppercase tracking-[0.06em] text-ink-2">
            All {ws.claims.length} claims in the ledger →
          </Link>
        )}
      </div>
    </div>
  );
}
