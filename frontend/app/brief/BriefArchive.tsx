import clsx from "clsx";
import Link from "next/link";
import { longDate } from "@/components/feed/prose";
import { archiveItemCount, type BriefArchiveEntry } from "@/lib/api-pages";

/** Stored morning briefs, newest first. `null` = the archive could not be read (said so, not hidden). */
export function BriefArchive({ entries, current, className }: { entries: BriefArchiveEntry[] | null; current?: string | null; className?: string }) {
  return (
    <aside aria-labelledby="brief-archive" className={clsx("print:hidden", className)}>
      <div className="mb-3 flex items-baseline justify-between gap-4 border-b border-ink pb-1.5">
        <h2 id="brief-archive" className="font-mono text-[11px] uppercase tracking-[0.14em] text-ink">
          Brief archive
        </h2>
        {entries && <span className="label tnum text-ink-3">{entries.length} stored</span>}
      </div>
      {entries === null ? (
        <p className="text-[13px] text-ink-3">The archive is unavailable right now.</p>
      ) : entries.length === 0 ? (
        <p className="text-[13px] leading-snug text-ink-3">
          No briefs stored yet. A brief is saved each morning by the scheduled job; days before that have no stored edition.
        </p>
      ) : (
        <ol className="divide-y divide-rule">
          {entries.map((e) => {
            const n = archiveItemCount(e);
            const active = current === e.date;
            return (
              <li key={e.id}>
                <Link
                  href={`/brief/${e.date}`}
                  aria-current={active ? "page" : undefined}
                  className={clsx("group grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 py-2.5", active && "bg-accent-wash")}
                >
                  <span className="min-w-0">
                    <span className={clsx("block text-[13.5px] group-hover:underline group-hover:decoration-accent group-hover:underline-offset-4", active ? "text-accent" : "text-ink")}>
                      {longDate(e.date, { year: true })}
                    </span>
                    {e.headlines && e.headlines[0] && <span className="mt-0.5 block truncate text-[12px] text-ink-2">{e.headlines[0]}</span>}
                  </span>
                  <span className="tnum whitespace-nowrap font-mono text-[10px] uppercase text-ink-3">
                    {n === null ? "" : `${n} ${n === 1 ? "item" : "items"}`}
                    {e.generator && e.generator !== "template" ? " · LLM" : ""}
                  </span>
                </Link>
              </li>
            );
          })}
        </ol>
      )}
    </aside>
  );
}
