import Link from "next/link";
import { EmptyState } from "@/components/EmptyState";
import { longDate } from "@/components/feed/prose";
import { loadBriefArchive, loadBriefDay, shiftDay } from "@/lib/api-pages";
import { today } from "@/lib/clock";
import { BriefArchive } from "./BriefArchive";
import { BriefView } from "./BriefView";
import { PrintButton } from "./PrintButton";

/** The stored brief for one day (or "today"), with the archive. Never generates a brief retroactively. */
export async function StoredBriefPage({ day }: { day: string }) {
  const [brief, archive] = await Promise.all([loadBriefDay(day), loadBriefArchive()]);
  const todayIso = today().toISOString().slice(0, 10);
  const date = brief && brief !== "unavailable" ? brief.date : day === "today" ? todayIso : day;
  const isToday = date === todayIso;
  const prev = shiftDay(date, -1);
  const next = shiftDay(date, 1);

  const nav = (
    <>
      <Link href="/" className="label text-ink-2 hover:text-ink">
        ← Radar
      </Link>
      <span className="flex flex-wrap items-center gap-x-5 gap-y-2">
        {prev && (
          <Link href={`/brief/${prev}`} className="label text-ink-2 hover:text-ink">
            ← {longDate(prev)}
          </Link>
        )}
        {next && !isToday && next <= todayIso && (
          <Link href={next === todayIso ? "/brief/today" : `/brief/${next}`} className="label text-ink-2 hover:text-ink">
            {longDate(next)} →
          </Link>
        )}
        <Link href="/brief" className="label text-ink-2 hover:text-ink">
          Live brief
        </Link>
        {brief && brief !== "unavailable" && <PrintButton />}
      </span>
    </>
  );

  return (
    <div className="grid gap-x-14 gap-y-12 pb-24 xl:grid-cols-[minmax(0,1fr)_280px]">
      <div className="min-w-0">
        {brief && brief !== "unavailable" ? (
          <BriefView brief={brief} mode="stored" isToday={isToday} actions={nav} />
        ) : (
          <div className="mx-auto max-w-[860px]">
            <div className="mb-8 flex flex-wrap items-center justify-between gap-4">{nav}</div>
            <header className="border-t-[3px] border-ink pt-1">
              <div className="border-t border-ink pt-4">
                <p className="font-mono text-[11.5px] uppercase tracking-[0.2em] text-ink">
                  Anti <span className="text-accent">Radar</span> Morning Brief
                </p>
                <h1 className="mt-4 font-serif text-[46px] leading-[1.02] tracking-[-0.018em] text-ink">
                  <time dateTime={date}>{longDate(date, { year: true })}</time>
                </h1>
              </div>
            </header>
            <div className="mt-10">
              {brief === "unavailable" ? (
                <EmptyState title="The brief archive is unavailable.">
                  Stored briefs could not be read from the backend right now. Nothing is shown rather than a brief composed after the fact.
                </EmptyState>
              ) : (
                <EmptyState
                  title="No brief was generated for this date."
                  action={
                    <Link href="/brief" className="link font-mono text-[11px] uppercase tracking-[0.08em] text-ink">
                      Read the brief composed from current data →
                    </Link>
                  }
                >
                  Briefs are stored by the morning job. {isToday ? "Today's has not been stored yet." : "None was stored that day."} The radar does not
                  generate briefs retroactively — a brief written today about a past day would not show what we knew then.
                </EmptyState>
              )}
            </div>
          </div>
        )}
      </div>
      <BriefArchive entries={archive} current={date} className="xl:pt-[52px]" />
    </div>
  );
}
