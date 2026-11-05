import type { Metadata } from "next";
import Link from "next/link";
import clsx from "clsx";
import { DemoBadge } from "@/components/Badges";
import { EmptyState } from "@/components/EmptyState";
import { SectionLabel } from "@/components/SectionLabel";
import { PageHeader } from "@/components/insight/PageHeader";
import { DiligenceStateBadge } from "@/components/diligence/Badges";
import { RunDiligenceButton } from "@/components/diligence/RunButton";
import { canWrite, diligenceHref, modeLabel, pct, statusCounts } from "@/components/diligence/logic";
import { dataSource } from "@/lib/dataSource";
import { getDiligenceIndex } from "@/lib/api";
import { fmtDate, fmtDateTime } from "@/lib/format";
import { getSession } from "@/lib/session";

export const metadata: Metadata = { title: "Diligence" };

const th = "label whitespace-nowrap px-2 py-2 text-left font-normal";
const td = "px-2 py-3 align-top";

export default async function DiligenceIndexPage() {
  const [index, session] = await Promise.all([getDiligenceIndex(), getSession()]);
  const writable = canWrite(session, dataSource() === "snapshot");
  const workspaces = index.workspaces.filter((w) => w.run);

  return (
    <div className="pb-24">
      <PageHeader
        kicker="Technical diligence"
        title="Diligence — what should we believe?"
        dek={
          <>
            For each company: the material technical claims it makes, the public evidence for and against each one, and what remains
            unknown. Statuses come from documented rules (R1–R6) over collected public artifacts; test plans are generated, never run.
            This is an evidence assessment, not an investment recommendation.
          </>
        }
        aside={
          <dl className="grid grid-cols-2 gap-x-6 gap-y-4">
            <div className="border-t border-ink pt-2">
              <dt className="label">Workspaces</dt>
              <dd className="tnum mt-1 font-serif text-[34px] leading-none text-ink">{workspaces.length}</dd>
            </div>
            <div className="border-t border-ink pt-2">
              <dt className="label">Ready for diligence</dt>
              <dd className="tnum mt-1 font-serif text-[34px] leading-none text-ink">{index.candidates.length}</dd>
            </div>
          </dl>
        }
      />

      <section aria-labelledby="ws-h">
        <SectionLabel index={1} id="ws-h" right={<span className="tnum">{workspaces.length}</span>}>
          Workspaces
        </SectionLabel>
        {workspaces.length === 0 ? (
          <EmptyState className="mt-6" title="No diligence has been run yet.">
            A workspace appears once public artifacts for a company have been collected and analyzed.
          </EmptyState>
        ) : (
          <div className="mt-5 overflow-x-auto">
            <table className="w-full min-w-[920px] border-collapse border-t border-ink text-[13px]">
              <thead>
                <tr className="border-b border-ink">
                  <th className={th} scope="col">Company</th>
                  <th className={th} scope="col">Status</th>
                  <th className={th} scope="col">Claims by status</th>
                  <th className={clsx(th, "text-right")} scope="col" title="Share of material claims with evidence beyond the company's own statements">
                    Evidence coverage
                  </th>
                  <th className={clsx(th, "text-right")} scope="col">Sources analyzed</th>
                  <th className={clsx(th, "text-right")} scope="col">Last updated</th>
                </tr>
              </thead>
              <tbody>
                {workspaces.map((w) => {
                  const run = w.run!;
                  const counts = statusCounts(run.summary.by_status);
                  const total = run.summary.claims ?? counts.reduce((a, c) => a + c.count, 0);
                  return (
                    <tr key={w.company.id} className="border-b border-rule">
                      <td className={clsx(td, "min-w-[220px]")}>
                        <Link
                          href={diligenceHref(w.company.slug)}
                          className="font-serif text-[19px] leading-tight text-ink decoration-accent decoration-1 underline-offset-4 hover:underline"
                        >
                          {w.company.name}
                        </Link>
                        {w.company.is_demo && <DemoBadge className="ml-2" />}
                        <p className="mt-0.5 font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">
                          {w.company.technical_domain ?? "Domain unknown"} · <span title={modeLabel(run.mode)}>{run.mode} run</span>
                        </p>
                      </td>
                      <td className={clsx(td, "max-w-[260px]")}>
                        <DiligenceStateBadge state={w.status} />
                        {(w.pending_review?.length ?? 0) > 0 && (
                          <p className="mt-1.5 text-[11.5px] leading-snug text-ink-2">
                            {w.pending_review!.length} pending review: {w.pending_review!.map((p) => p.label).join("; ")}
                          </p>
                        )}
                        {(w.last_error ?? run.error) && (
                          <p className="mt-1.5 text-[11.5px] leading-snug text-accent">Last run failed: {w.last_error ?? run.error}</p>
                        )}
                      </td>
                      <td className={td}>
                        <p className="tnum font-mono text-[11px] text-ink">{total} claims</p>
                        <p className="tnum mt-0.5 font-mono text-[10.5px] text-ink-2">
                          {counts
                            .filter((c) => c.count > 0 || c.status === "CONTRADICTED")
                            .map((c) => `${c.label.toLowerCase()} ${c.count}`)
                            .join(" · ")}
                        </p>
                      </td>
                      <td className={clsx(td, "tnum text-right font-serif text-[20px] leading-none text-ink")}>{pct(run.evidence_coverage)}</td>
                      <td className={clsx(td, "tnum text-right font-mono text-[12px] text-ink")}>{run.sources_analyzed}</td>
                      <td className={clsx(td, "tnum whitespace-nowrap text-right font-mono text-[10.5px] uppercase text-ink-2")} title={fmtDateTime(run.finished_at)}>
                        {fmtDate(run.finished_at)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section aria-labelledby="cand-h" className="mt-16">
        <SectionLabel index={2} id="cand-h" right={<span className="tnum">{index.candidates.length}</span>}>
          Ready for diligence
        </SectionLabel>
        <p className="mt-4 max-w-[78ch] text-[13px] leading-relaxed text-ink-2">
          Companies on the radar with a website or GitHub organization to collect from and no diligence run yet, top 20 by Anti Score.{" "}
          {writable
            ? "Running diligence collects public artifacts (up to 3 minutes) and analyzes them offline; untrusted code is never executed."
            : "Running diligence needs a live, writable backend — this deployment serves a read-only snapshot, so no run can be started here."}
        </p>
        {index.candidates.length === 0 ? (
          <EmptyState className="mt-6" title="No candidates." />
        ) : (
          <ol className="mt-4 grid border-t border-ink md:grid-cols-2 md:gap-x-10">
            {index.candidates.map((c, i) => (
              <li key={c.company.id} className="grid grid-cols-[30px_minmax(0,1fr)_auto] items-baseline gap-x-3 border-b border-rule py-2.5">
                <span className="tnum font-mono text-[10.5px] text-ink-3">{String(i + 1).padStart(2, "0")}</span>
                <div className="min-w-0">
                  <Link href={`/companies/${c.company.slug}`} className="font-serif text-[16px] leading-tight text-ink hover:text-accent">
                    {c.company.name}
                  </Link>
                  <span className="ml-2 font-mono text-[9.5px] uppercase tracking-[0.06em] text-ink-3">
                    {c.company.technical_domain ?? "domain unknown"}
                    {c.company.github_org ? ` · github/${c.company.github_org}` : ""}
                  </span>
                </div>
                <span className="flex items-baseline gap-3">
                  <span className="tnum font-mono text-[12px] text-ink-2" title="Anti Score">
                    {c.score ?? "—"}
                  </span>
                  <RunDiligenceButton slug={c.company.slug} writable={writable} label="Run" />
                </span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
