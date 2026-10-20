import clsx from "clsx";
import { SourceGlyph, SourceLink } from "@/components/SourceLink";
import { fmtDate } from "@/lib/format";
import type { EvidenceItem } from "@/lib/types";

/**
 * WHY NOW — dated evidence first. Mono date column, source glyph, the claim,
 * and a link to the primary source. Undated items are labelled as such.
 */
export function WhyNow({
  items,
  limit,
  demo = false,
  size = "card",
  className,
}: {
  items: EvidenceItem[];
  limit?: number;
  demo?: boolean;
  size?: "card" | "lead";
  className?: string;
}) {
  const shown = limit ? items.slice(0, limit) : items;
  if (shown.length === 0) {
    return <p className={clsx("text-[13px] text-ink-3", className)}>No dated formation evidence recorded.</p>;
  }
  const lead = size === "lead";
  return (
    <ul className={clsx(lead ? "space-y-3.5" : "space-y-2", className)}>
      {shown.map((e, i) => (
        <li key={e.signal_id ?? `${e.title}-${i}`} className={clsx("grid items-baseline gap-x-3", lead ? "grid-cols-[64px_26px_1fr]" : "grid-cols-[52px_24px_1fr]")}>
          <time
            dateTime={e.event_at ?? undefined}
            className={clsx("tnum whitespace-nowrap font-mono uppercase", lead ? "text-[11px] text-ink" : "text-[10.5px] text-ink-2")}
          >
            {e.event_at ? fmtDate(e.event_at, { year: false }) : "undated"}
          </time>
          <SourceGlyph sourceType={e.source_type} className="translate-y-[2px] self-start" />
          <div className="min-w-0">
            <span className={clsx("text-ink", lead ? "text-[15px] leading-snug" : "text-[13.5px] leading-snug")}>{e.title}</span>
            {e.source_url && <SourceLink url={e.source_url} demo={demo} className="ml-2 whitespace-nowrap" />}
            {lead && e.detail && <p className="mt-0.5 max-w-[68ch] text-[13px] leading-snug text-ink-2">{e.detail}</p>}
          </div>
        </li>
      ))}
      {limit && items.length > limit && <li className="label pl-[88px] text-ink-3">+ {items.length - limit} more in the report</li>}
    </ul>
  );
}
