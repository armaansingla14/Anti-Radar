import clsx from "clsx";
import Link from "next/link";
import { EmptyState } from "@/components/EmptyState";
import { loadPortfolioBrief, loadPortfolioBriefArchive } from "@/components/entity/data";
import { longDate } from "@/components/feed/prose";
import { fmtDateTime } from "@/lib/format";
import type { BriefArchiveItem } from "@/lib/types-portfolio";
import { PrintButton } from "@/app/brief/PrintButton";
import { PortfolioBriefView } from "./BriefView";

function Archive({ entries, current }: { entries: BriefArchiveItem[] | null; current: string | null }) {
  return (
    <aside aria-labelledby="pbrief-archive" className="print:hidden xl:pt-[52px]">
      <div className="mb-3 flex items-baseline justify-between gap-4 border-b border-ink pb-1.5">
        <h2 id="pbrief-archive" className="font-mono text-[11px] uppercase tracking-[0.14em] text-ink">
          Brief archive
        </h2>
        {entries && <span className="label tnum text-ink-3">{entries.length} stored</span>}
      </div>
      {entries === null ? (
        <p className="text-[13px] text-ink-3">The archive is unavailable right now.</p>
      ) : entries.length === 0 ? (
        <p className="text-[13px] text-ink-3">No portfolio brief stored yet.</p>
      ) : (
        <ol className="divide-y divide-rule">
          {entries.map((e) => (
            <li key={e.id}>
              <Link
                href={`/portfolio/brief/${e.date}`}
                aria-current={current === e.date ? "page" : undefined}
                className={clsx("block py-2.5 text-[13.5px] hover:underline hover:decoration-accent hover:underline-offset-4", current === e.date ? "text-accent" : "text-ink")}
              >
                {longDate(e.date, { year: true })}
              </Link>
            </li>
          ))}
        </ol>
      )}
    </aside>
  );
}

/** The stored PORTFOLIO MORNING BRIEF (latest, or one day's) with its archive. Never composed after the fact. */
export async function PortfolioBriefPage({ day }: { day?: string }) {
  const [brief, archive] = await Promise.all([loadPortfolioBrief(day), loadPortfolioBriefArchive()]);
  const nav = (
    <>
      <span className="flex items-center gap-x-5">
        <Link href="/portfolio" className="label text-ink-2 hover:text-ink">
          ← Portfolio Pulse
        </Link>
        {brief && (
          <Link href={`/reports/${brief.id}`} className="label text-ink-2 hover:text-ink">
            Stored report →
          </Link>
        )}
      </span>
      {brief && <PrintButton />}
    </>
  );
  return (
    <div className="grid gap-x-14 gap-y-12 pb-24 xl:grid-cols-[minmax(0,1fr)_240px]">
      <div className="min-w-0">
        {brief ? (
          <PortfolioBriefView
            body={brief.body}
            sources={brief.sources}
            actions={nav}
            generatedNote={
              <>
                Stored {fmtDateTime(brief.created_at)} ·{" "}
                {brief.generator === "template" ? "deterministic template from the portfolio run — no language model" : `generator: ${brief.generator}`}
              </>
            }
          />
        ) : (
          <div className="mx-auto max-w-[860px]">
            <div className="mb-8 flex flex-wrap items-center justify-between gap-4">{nav}</div>
            <EmptyState title={day ? "No portfolio brief was stored for this date." : "No portfolio brief has been stored yet."}>
              The portfolio brief is written by each portfolio run. The radar does not generate briefs retroactively.
            </EmptyState>
          </div>
        )}
      </div>
      <Archive entries={archive} current={brief?.date ?? day ?? null} />
    </div>
  );
}
