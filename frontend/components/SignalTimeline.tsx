import clsx from "clsx";
import { fmtDate, humanize, parseDate } from "@/lib/format";
import type { TimelineEvent } from "@/lib/types";
import { DemoBadge, EpistemicBadge } from "./Badges";
import { SourceGlyph, SourceLink } from "./SourceLink";

export interface SignalTimelineProps {
  events: TimelineEvent[];
  /** Show only the first N (events are rendered newest first). */
  limit?: number;
  className?: string;
}

/** Vertical evidence timeline: mono date column, hairline spine, sourced entries. */
export function SignalTimeline({ events, limit, className }: SignalTimelineProps) {
  const sorted = [...events].sort((a, b) => (parseDate(b.day)?.getTime() ?? 0) - (parseDate(a.day)?.getTime() ?? 0));
  const shown = limit ? sorted.slice(0, limit) : sorted;
  if (shown.length === 0) return <p className="text-[13px] text-ink-3">No dated events yet.</p>;

  return (
    <ol className={clsx("relative", className)}>
      <span aria-hidden className="absolute bottom-2 left-[92px] top-2 w-px bg-rule" />
      {shown.map((ev, i) => {
        const d = parseDate(ev.day);
        const year = d?.getUTCFullYear() ?? null;
        const showYear = i === 0 || year !== (parseDate(shown[i - 1].day)?.getUTCFullYear() ?? null);
        return (
          <li key={`${ev.day}-${ev.title}-${i}`} className="relative grid grid-cols-[80px_24px_1fr] gap-x-0 pb-6 last:pb-0">
            <div className="pt-[1px] text-right">
              <time dateTime={ev.day} className="tnum block font-mono text-[11px] uppercase text-ink">
                {fmtDate(ev.day, { year: false })}
              </time>
              {showYear && <span className="tnum font-mono text-[10px] text-ink-3">{year}</span>}
            </div>
            <div className="relative flex justify-center">
              <span
                aria-hidden
                className={clsx(
                  "mt-[5px] block h-[7px] w-[7px] border bg-paper",
                  ev.epistemic === "FACT" ? "border-ink bg-ink" : "border-ink-2",
                )}
              />
            </div>
            <div className="min-w-0 pl-2">
              <p className="text-[14px] leading-snug text-ink">{ev.title}</p>
              {ev.detail && <p className="mt-1 max-w-[70ch] text-[13px] leading-relaxed text-ink-2">{ev.detail}</p>}
              <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
                <SourceGlyph sourceType={ev.source_type} />
                <span className="font-mono text-[10px] uppercase tracking-[0.06em] text-ink-3">{humanize(ev.signal_type)}</span>
                <SourceLink url={ev.source_url} demo={ev.is_demo} />
                <EpistemicBadge value={ev.epistemic} />
                {ev.is_demo && <DemoBadge />}
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
