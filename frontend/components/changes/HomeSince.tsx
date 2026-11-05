import clsx from "clsx";
import Link from "next/link";
import type { ChangesReport } from "@/lib/types-changes";
import { hasReport, homeCaveat, homeCounts, sinceLabel } from "./logic";

/**
 * Home: compact "SINCE OCT 20" strip between the count strip and the filter strip. Every count links into
 * /changes. Renders nothing when there is no second snapshot to compare against.
 */
export function HomeSince({ report }: { report: ChangesReport | null }) {
  if (!hasReport(report)) return null;
  const counts = homeCounts(report);
  const since = sinceLabel(report.base_day) ?? "since the previous snapshot";
  return (
    <section aria-labelledby="since-h" data-demo="home-since" className="mt-7 border-y border-rule py-3">
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
        <h2 id="since-h" className="label shrink-0 text-ink">
          <span aria-hidden className="mr-2 inline-block size-[6px] translate-y-[-1px] bg-accent" />
          {since}
        </h2>
        <ul className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-6 gap-y-1.5">
          {counts.map((c) => (
            <li key={c.key} className="min-w-0">
              <Link href={c.href} className="group inline-flex items-baseline gap-1.5">
                <span
                  className={clsx(
                    "tnum font-serif text-[21px] leading-none tracking-[-0.01em] transition-colors group-hover:text-accent",
                    c.count === 0 ? "text-ink-3" : c.tone === "up" ? "text-accent" : "text-ink",
                  )}
                >
                  {c.tone === "up" && c.count > 0 && <span aria-hidden className="mr-0.5 font-mono text-[12px]">↑</span>}
                  {c.tone === "down" && c.count > 0 && <span aria-hidden className="mr-0.5 font-mono text-[12px]">↓</span>}
                  {c.count.toLocaleString("en-US")}
                </span>
                <span className="label text-ink-2 transition-colors group-hover:text-ink">{c.label}</span>
              </Link>
            </li>
          ))}
        </ul>
        <Link href="/changes" className="label shrink-0 text-ink transition-colors hover:text-accent">
          What changed →
        </Link>
      </div>
      <p className="mt-2 text-[12px] leading-snug text-ink-3">{homeCaveat(report)}</p>
    </section>
  );
}
